"""
job_list_agent.py — Returns a formatted SMS list of scheduled jobs for a date range

Flow:
  1. Use Claude (haiku) to parse the raw SMS into a date range + label
  2. Query schedule table for this client within that range
  3. Enrich each row with customer address, job type, assigned employee names
  4. Format and send an SMS list to the sender
  5. Log the inbound event to messages table

Usage:
    from execution.job_list_agent import handle_job_list
    result = handle_job_list(client, employee, raw_input, from_number)

Where:
    client      — full dict from clients table (has id, business_name, etc.)
    employee    — full dict from employees table, or None if owner
    raw_input   — raw SMS body
    from_number — sender's E.164 phone number (for reply)
"""

import json
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.call_claude import call_claude
from execution.db_connection import get_client as get_supabase
from execution.db_messages import log_message
from execution.sms_send import send_sms
from execution.db_agent_activity import log_activity


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


MAX_JOBS_IN_SMS = 10


# ---------------------------------------------------------------------------
# Step 1 — Parse date range with Claude (haiku)
# ---------------------------------------------------------------------------

_PARSE_SYSTEM = """You extract a date range from an SMS message asking about a job schedule.

Return ONLY valid JSON. No markdown, no explanation, no code fences.

Schema:
{
  "date_from": "YYYY-MM-DD",
  "date_to": "YYYY-MM-DD",
  "label": string
}

Rules:
- Resolve relative dates using today's date provided below.
- "jobs today" or "schedule today" → date_from = date_to = today, label = "today"
- "schedule tomorrow" → date_from = date_to = tomorrow, label = "tomorrow"
- "jobs this week" → date_from = this Monday, date_to = this Sunday, label = "this week"
- "jobs friday" or "what's on for friday" → date_from = date_to = the coming Friday (or today if today is Friday), label = "Friday"
- "schedule march 20" or "jobs march 20" → date_from = date_to = that date, label = "Mar 20"
- If you cannot determine a date, return date_from = date_to = today, label = "today"
- label should be a short human-readable string like "today", "tomorrow", "this week", "Friday", "Mar 20"
"""


def _parse_date_range(raw_input: str, today_iso: str) -> dict:
    """
    Ask Claude (haiku) for the date range implied by the message.
    Falls back to today on any parse failure — never blocks the response.
    """
    fallback = {"date_from": today_iso, "date_to": today_iso, "label": "today"}

    user_prompt = f"Today's date is {today_iso}.\n\nSMS message:\n{raw_input}"
    raw = call_claude(_PARSE_SYSTEM, user_prompt, model="haiku", max_tokens=256)

    if not raw:
        print(f"[{timestamp()}] WARN job_list_agent: Claude returned no text — falling back to today")
        return fallback

    try:
        parsed = json.loads(raw.strip())
        # Validate required keys exist and are non-empty strings
        if parsed.get("date_from") and parsed.get("date_to"):
            print(
                f"[{timestamp()}] INFO job_list_agent: Date range → "
                f"{parsed['date_from']} to {parsed['date_to']} ({parsed.get('label', '?')})"
            )
            return parsed
        print(f"[{timestamp()}] WARN job_list_agent: Claude JSON missing date fields — falling back to today")
        return fallback

    except json.JSONDecodeError as e:
        print(f"[{timestamp()}] WARN job_list_agent: JSON parse failed ({e}) — falling back to today")
        return fallback


# ---------------------------------------------------------------------------
# Step 2 — Query schedule + enrich rows
# ---------------------------------------------------------------------------

def _fetch_schedule_rows(client_id: str, date_from: str, date_to: str) -> list:
    """
    Query the schedule table for a client within the date range.
    Returns raw schedule rows ordered by date asc, time asc.
    """
    try:
        supabase = get_supabase()
        result = (
            supabase.table("schedule")
            .select("*")
            .eq("client_id", client_id)
            .gte("scheduled_date", date_from)
            .lte("scheduled_date", date_to)
            .neq("status", "cancelled")
            .order("scheduled_date", desc=False)
            .order("scheduled_time", desc=False)
            .execute()
        )
        rows = result.data or []
        print(f"[{timestamp()}] INFO job_list_agent: Found {len(rows)} schedule rows")
        return rows

    except Exception as e:
        print(f"[{timestamp()}] ERROR job_list_agent: Schedule query failed — {e}")
        return None   # None signals a DB error (vs [] which means genuinely empty)


def _fetch_customer(customer_id: str) -> dict | None:
    """Fetch a customer row by id. Returns None on failure."""
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
    """Fetch a job row by id. Returns None on failure."""
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
        # Return first name only ("Jake Smith" → "Jake")
        return [row["name"].split()[0] for row in (result.data or [])]
    except Exception as e:
        print(f"[{timestamp()}] WARN job_list_agent: Employee name lookup failed — {e}")
        return []


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_time(hhmm: str | None) -> str:
    """'08:00' → '8:00am', '14:30' → '2:30pm', None → 'TBD'"""
    if not hhmm:
        return "TBD"
    try:
        t = datetime.strptime(hhmm, "%H:%M")
        return t.strftime("%-I:%M%p").lower()  # "8:00am"
    except ValueError:
        return hhmm


def _fmt_date_header(date_from: str, date_to: str, label: str) -> str:
    """
    Build the header line, e.g. "Jobs for Today (Mar 17)" or "Jobs for This Week (Mar 17-23)".
    """
    try:
        d_from = date.fromisoformat(date_from)
        d_to   = date.fromisoformat(date_to)
        label_cap = label.title()
        if date_from == date_to:
            return f"Jobs for {label_cap} ({d_from.strftime('%b %-d')}):"
        else:
            return (
                f"Jobs for {label_cap} "
                f"({d_from.strftime('%b %-d')}-{d_to.strftime('%-d')}):"
            )
    except ValueError:
        return f"Jobs for {label.title()}:"


def _build_sms(client_id: str, rows: list, date_from: str, date_to: str, label: str) -> str:
    """
    Build the full SMS body from enriched schedule rows.
    Caps at MAX_JOBS_IN_SMS lines.
    """
    if not rows:
        return f"No jobs scheduled for {label}."

    lines = [_fmt_date_header(date_from, date_to, label)]

    displayed = 0
    for row in rows[:MAX_JOBS_IN_SMS]:
        # Enrich — pull address from customer, job_type from job
        customer    = _fetch_customer(row.get("customer_id"))
        job         = _fetch_job(row.get("job_id"))
        emp_names   = _fetch_employee_names(client_id, row.get("assigned_employee_ids") or [])

        job_type  = (job or {}).get("job_type") or "Job"
        address   = (customer or {}).get("customer_address") or "address TBD"
        time_str  = _fmt_time(row.get("scheduled_time"))
        names_str = ", ".join(emp_names) if emp_names else "unassigned"

        lines.append(f"{time_str} — {job_type.title()} at {address} ({names_str})")
        displayed += 1

    total = len(rows)
    if total > MAX_JOBS_IN_SMS:
        lines.append(f"...and {total - MAX_JOBS_IN_SMS} more.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def handle_job_list(
    client: dict,
    employee: dict | None,
    raw_input: str,
    from_number: str,
) -> str:
    """
    Handle an inbound job-list SMS query.

    Args:
        client:      Full client dict (clients table row)
        employee:    Full employee dict, or None if owner is texting
        raw_input:   Raw SMS body
        from_number: Sender's E.164 phone number — reply goes here

    Returns:
        Status string for logging ("ok", "no_jobs", "db_error", "error")
    """
    client_id    = client["id"]
    client_phone = client.get("phone") or client.get("telnyx_number")
    today_iso    = date.today().isoformat()

    print(f"[{timestamp()}] INFO job_list_agent: Starting | client={client_id} sender={from_number}")

    # ------------------------------------------------------------------
    # Step 1 — Parse date range
    # ------------------------------------------------------------------
    date_range = _parse_date_range(raw_input, today_iso)
    date_from  = date_range["date_from"]
    date_to    = date_range["date_to"]
    label      = date_range.get("label", "today")

    # ------------------------------------------------------------------
    # Step 2 — Query schedule
    # ------------------------------------------------------------------
    rows = _fetch_schedule_rows(client_id, date_from, date_to)

    if rows is None:
        # DB error — _fetch_schedule_rows already logged it
        send_sms(
            to_number=from_number,
            message_body="Sorry, couldn't pull the schedule right now. Try again.",
            from_number=client_phone,
        )
        return "db_error"

    # ------------------------------------------------------------------
    # Step 3 — Format SMS
    # ------------------------------------------------------------------
    sms_body = _build_sms(client_id, rows, date_from, date_to, label)

    # ------------------------------------------------------------------
    # Step 4 — Send SMS
    # ------------------------------------------------------------------
    sms_result = send_sms(
        to_number=from_number,
        message_body=sms_body,
        from_number=client_phone,
    )
    if not sms_result["success"]:
        print(f"[{timestamp()}] ERROR job_list_agent: SMS send failed — {sms_result['error']}")

    # ------------------------------------------------------------------
    # Step 5 — Log the inbound message
    # ------------------------------------------------------------------
    try:
        log_message(
            client_id=client_id,
            direction="inbound",
            from_number=from_number,
            to_number=client_phone,
            body=raw_input,
            agent_used="job_list_agent",
        )
    except Exception as e:
        print(f"[{timestamp()}] WARN job_list_agent: log_message failed — {e}")

    status = "no_jobs" if not rows else "ok"
    print(f"[{timestamp()}] INFO job_list_agent: Complete. jobs_returned={len(rows)} status={status}")
    try:
        log_activity(
            client_phone=client_phone,
            agent_name="job_list_agent",
            action_taken="job_list_queried",
            input_summary=raw_input[:120],
            output_summary=f"{len(rows)} jobs {date_from} to {date_to}",
            sms_sent=sms_result.get("success", False),
        )
    except Exception:
        pass
    return status
