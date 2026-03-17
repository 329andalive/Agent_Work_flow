"""
briefing_agent.py — Sends a morning schedule briefing to foremans and the owner

Called automatically by the cron job each morning.
Pulls today's scheduled jobs for a client and sends a single formatted SMS
to every active foreman and the owner. Outbound-only — not triggered by SMS.

Usage:
    from execution.briefing_agent import send_morning_briefing
    result = send_morning_briefing(client)

Where:
    client — full dict from clients table (must include id, business_name,
              phone, owner_mobile, and optionally timezone)

Returns a string summary for the cron log:
    "Sent to 3 recipients for Holt Sewer & Drain"
    "Skipped — no jobs for Holt Sewer & Drain"
    "Error — skipped Holt Sewer & Drain"
"""

import os
import sys
from datetime import datetime

import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase
from execution.sms_send import send_sms


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


DEFAULT_TIMEZONE = "America/New_York"


# ---------------------------------------------------------------------------
# Step 1 — Resolve today's date in client's local timezone
# ---------------------------------------------------------------------------

def _get_local_today(client: dict) -> str:
    """
    Return today's ISO date string (YYYY-MM-DD) in the client's timezone.
    Falls back to America/New_York and logs a warning if timezone is missing
    or invalid.
    """
    tz_name = client.get("timezone") or DEFAULT_TIMEZONE
    try:
        tz = pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        print(
            f"[{timestamp()}] WARN briefing_agent: Unknown timezone '{tz_name}' "
            f"for client {client.get('id')} — falling back to {DEFAULT_TIMEZONE}"
        )
        tz = pytz.timezone(DEFAULT_TIMEZONE)

    local_now = datetime.now(tz)
    return local_now.date().isoformat()


# ---------------------------------------------------------------------------
# Step 2 — Query today's schedule
# ---------------------------------------------------------------------------

def _fetch_today_rows(client_id: str, today_iso: str) -> list | None:
    """
    Query the schedule table for a single client on a single date.
    Returns the list of rows (may be empty), or None on DB error.
    None vs [] lets the caller distinguish an error from a genuinely empty day.
    """
    try:
        supabase = get_supabase()
        result = (
            supabase.table("schedule")
            .select("*")
            .eq("client_id", client_id)
            .eq("scheduled_date", today_iso)
            .neq("status", "cancelled")
            .order("scheduled_time", desc=False)
            .execute()
        )
        rows = result.data or []
        print(f"[{timestamp()}] INFO briefing_agent: Found {len(rows)} jobs for {today_iso}")
        return rows

    except Exception as e:
        print(f"[{timestamp()}] ERROR briefing_agent: Schedule query failed — {e}")
        return None


# ---------------------------------------------------------------------------
# Step 3 — Enrich helpers (same pattern as job_list_agent.py)
# ---------------------------------------------------------------------------

def _fetch_customer(customer_id: str) -> dict | None:
    """Fetch customer_name and customer_address by id. Returns None on failure."""
    if not customer_id:
        return None
    try:
        supabase = get_supabase()
        result = (
            supabase.table("customers")
            .select("customer_name, customer_address")
            .eq("id", customer_id)
            .single()
            .execute()
        )
        return result.data
    except Exception:
        return None


def _fetch_job(job_id: str) -> dict | None:
    """Fetch job_type by id. Returns None on failure."""
    if not job_id:
        return None
    try:
        supabase = get_supabase()
        result = (
            supabase.table("jobs")
            .select("job_type")
            .eq("id", job_id)
            .single()
            .execute()
        )
        return result.data
    except Exception:
        return None


def _fetch_employee_names(client_id: str, employee_ids: list) -> list[str]:
    """
    Given a list of employee UUIDs, return their first names.
    Silently skips any ID that can't be resolved.
    """
    if not employee_ids:
        return []
    try:
        supabase = get_supabase()
        result = (
            supabase.table("employees")
            .select("id, name")
            .eq("client_id", client_id)
            .in_("id", employee_ids)
            .execute()
        )
        return [row["name"].split()[0] for row in (result.data or [])]
    except Exception as e:
        print(f"[{timestamp()}] WARN briefing_agent: Employee name lookup failed — {e}")
        return []


# ---------------------------------------------------------------------------
# Step 4 — Build the briefing message
# ---------------------------------------------------------------------------

def _fmt_time(hhmm: str | None) -> str:
    """'08:00' → '8:00am', '14:30' → '2:30pm', None → 'TBD'"""
    if not hhmm:
        return "TBD"
    try:
        t = datetime.strptime(hhmm, "%H:%M")
        return t.strftime("%-I:%M%p").lower()
    except ValueError:
        return hhmm


def _build_briefing(client_id: str, business_name: str, rows: list, today_iso: str) -> str:
    """
    Build the full briefing SMS body.
    No job cap — foremans need the complete list.
    """
    from datetime import date as date_cls
    try:
        d = date_cls.fromisoformat(today_iso)
        date_display = d.strftime("%A %b %-d")  # "Monday Mar 17"
    except ValueError:
        date_display = today_iso

    lines = [f"Good morning! Here's today's schedule for {business_name} ({date_display}):"]
    lines.append("")

    for row in rows:
        customer  = _fetch_customer(row.get("customer_id"))
        job       = _fetch_job(row.get("job_id"))
        emp_names = _fetch_employee_names(client_id, row.get("assigned_employee_ids") or [])

        job_type  = (job or {}).get("job_type") or "Job"
        address   = (customer or {}).get("customer_address") or "address TBD"
        time_str  = _fmt_time(row.get("scheduled_time"))
        names_str = ", ".join(emp_names) if emp_names else "unassigned"

        lines.append(f"{time_str} - {job_type.title()} at {address} ({names_str})")

    lines.append("")
    job_word = "job" if len(rows) == 1 else "jobs"
    lines.append(f"{len(rows)} {job_word} scheduled. Have a great day!")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 5 — Get recipients
# ---------------------------------------------------------------------------

def _get_recipients(client: dict) -> list[str]:
    """
    Build the deduplicated list of phone numbers to send the briefing to.

    Includes:
      - All active foreman and owner employees (employees table)
      - client['owner_mobile'] as a fallback for the business owner

    Returns a list of unique E.164 phone numbers.
    """
    phones: set[str] = set()

    # Always include the owner's personal cell from the clients table
    owner_mobile = client.get("owner_mobile")
    if owner_mobile:
        phones.add(owner_mobile)

    # Pull foremen and owner-role employees
    try:
        supabase = get_supabase()
        result = (
            supabase.table("employees")
            .select("name, phone, role")
            .eq("client_id", client["id"])
            .eq("active", True)
            .in_("role", ["foreman", "owner"])
            .execute()
        )
        for row in (result.data or []):
            phone = row.get("phone")
            if phone:
                phones.add(phone)
                print(
                    f"[{timestamp()}] INFO briefing_agent: Recipient → "
                    f"{row.get('name')} ({row.get('role')}) {phone}"
                )
    except Exception as e:
        print(f"[{timestamp()}] WARN briefing_agent: Employee recipient query failed — {e}")

    return list(phones)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def send_morning_briefing(client: dict) -> str:
    """
    Build and send the morning job briefing to all foremans and the owner.

    Args:
        client: Full clients table row dict

    Returns:
        Summary string for cron logging.
    """
    client_id     = client["id"]
    business_name = client.get("business_name", "your business")
    client_phone  = client.get("phone")   # Telnyx from-number for all outbound SMS

    print(f"[{timestamp()}] INFO briefing_agent: Starting | client={client_id} ({business_name})")

    # ------------------------------------------------------------------
    # Step 1 — Resolve today's date in client's timezone
    # ------------------------------------------------------------------
    today_iso = _get_local_today(client)

    # ------------------------------------------------------------------
    # Step 2 — Query today's schedule
    # ------------------------------------------------------------------
    rows = _fetch_today_rows(client_id, today_iso)

    if rows is None:
        # DB error already logged inside _fetch_today_rows
        return f"Error — skipped {business_name}"

    if not rows:
        print(f"[{timestamp()}] INFO briefing_agent: No jobs today — skipping {business_name}")
        return f"Skipped — no jobs for {business_name}"

    # ------------------------------------------------------------------
    # Step 4 — Build the briefing message
    # ------------------------------------------------------------------
    message = _build_briefing(client_id, business_name, rows, today_iso)

    # ------------------------------------------------------------------
    # Step 5 — Get recipients
    # ------------------------------------------------------------------
    recipients = _get_recipients(client)

    if not recipients:
        print(f"[{timestamp()}] WARN briefing_agent: No recipients found for {business_name}")
        return f"Skipped — no recipients for {business_name}"

    # ------------------------------------------------------------------
    # Step 6 — Send to all recipients
    # ------------------------------------------------------------------
    sent_count = 0
    for phone in recipients:
        result = send_sms(
            to_number=phone,
            message_body=message,
            from_number=client_phone,
        )
        if result["success"]:
            print(
                f"[{timestamp()}] INFO briefing_agent: "
                f"Sent briefing to {phone} for {business_name}"
            )
            sent_count += 1
        else:
            print(
                f"[{timestamp()}] ERROR briefing_agent: "
                f"SMS to {phone} failed — {result['error']}"
            )

    # ------------------------------------------------------------------
    # Step 7 — Return summary
    # ------------------------------------------------------------------
    summary = f"Sent to {sent_count} recipients for {business_name}"
    print(f"[{timestamp()}] INFO briefing_agent: Complete. {summary}")
    return summary
