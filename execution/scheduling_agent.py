"""
scheduling_agent.py — Parses scheduling SMS requests and creates schedule records

Flow:
  1. Use Claude (haiku) to parse the raw SMS into structured JSON
  2. Resolve assigned employee by first name from employees table
  3. Look up or create customer by name / address
  4. Create a job record (status='scheduled')
  5. Insert into schedule table
  6. Send confirmation SMS back to sender
  7. Log the inbound event to messages table

Usage:
    from execution.scheduling_agent import handle_scheduling
    result = handle_scheduling(client, employee, raw_input, from_number)

Where:
    client      — full dict from clients table (has id, business_name, personality, etc.)
    employee    — full dict from employees table, or None if the owner is texting
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
from execution.db_jobs import create_job, update_job_status
from execution.db_messages import log_message
from execution.sms_send import send_sms


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Step 1 — Parse the raw SMS with Claude
# ---------------------------------------------------------------------------

_PARSE_SYSTEM = """You extract scheduling information from SMS messages sent by trade business owners.

Return ONLY valid JSON. No markdown, no explanation, no code fences.

Schema:
{
  "customer_name": string or null,
  "address": string or null,
  "job_type": string or null,
  "scheduled_date": "YYYY-MM-DD" or null,
  "scheduled_time": "HH:MM" (24hr) or null,
  "assigned_employee_name": string (first name only) or null,
  "notes": string or null
}

Rules:
- Resolve relative dates ("Friday", "tomorrow", "next Monday") using today's date provided below.
- scheduled_time must be 24-hour format. "8am" → "08:00", "2pm" → "14:00".
- job_type should be a short plain phrase: "pump job", "septic inspection", "drain cleaning".
- If a field is not mentioned, return null for it.
- assigned_employee_name should be the first name only."""


def _parse_with_claude(raw_input: str, today_iso: str) -> dict | None:
    """
    Ask Claude (haiku) to extract scheduling fields from the raw SMS.
    Returns a parsed dict on success, or None if parsing fails.
    """
    user_prompt = f"Today's date is {today_iso}.\n\nSMS message:\n{raw_input}"
    raw = call_claude(_PARSE_SYSTEM, user_prompt, model="haiku", max_tokens=512)

    if not raw:
        print(f"[{timestamp()}] ERROR scheduling_agent: Claude returned no text")
        return None

    # Strip any accidental whitespace/newlines before parsing
    raw = raw.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[{timestamp()}] ERROR scheduling_agent: JSON parse failed — {e} | raw='{raw[:200]}'")
        return None

    print(
        f"[{timestamp()}] INFO scheduling_agent: Parsed → "
        f"job_type={parsed.get('job_type')} "
        f"date={parsed.get('scheduled_date')} "
        f"time={parsed.get('scheduled_time')} "
        f"employee={parsed.get('assigned_employee_name')} "
        f"address={parsed.get('address')}"
    )
    return parsed


# ---------------------------------------------------------------------------
# Step 2 — Resolve assigned employee by first name
# ---------------------------------------------------------------------------

def _resolve_employee(client_id: str, name: str | None) -> dict | None:
    """
    Find an active employee whose name contains the given first name (case-insensitive).
    Returns the full employee row, or None if not found or name is null.
    """
    if not name:
        return None

    try:
        supabase = get_supabase()
        result = (
            supabase.table("employees")
            .select("*")
            .eq("client_id", client_id)
            .eq("active", True)
            .execute()
        )
        employees = result.data or []

        name_lower = name.strip().lower()
        for emp in employees:
            # Match on first name — "Jake Smith".lower().startswith("jake")
            emp_name_lower = emp.get("name", "").lower()
            if emp_name_lower.startswith(name_lower) or name_lower in emp_name_lower:
                print(
                    f"[{timestamp()}] INFO scheduling_agent: "
                    f"Resolved employee → {emp['name']} (id={emp['id']})"
                )
                return emp

        print(f"[{timestamp()}] WARN scheduling_agent: No employee found matching name='{name}'")
        return None

    except Exception as e:
        print(f"[{timestamp()}] ERROR scheduling_agent: Employee lookup failed — {e}")
        return None


# ---------------------------------------------------------------------------
# Step 3 — Look up or create customer by name / address
# ---------------------------------------------------------------------------

def _resolve_customer(client_id: str, name: str | None, address: str | None) -> str | None:
    """
    Find an existing customer by address (partial match) or name, within this client.
    Creates a new customer record if none is found.
    Returns customer_id UUID string, or None on failure.
    """
    supabase = get_supabase()

    # Try address match first — more specific than name
    if address:
        try:
            result = (
                supabase.table("customers")
                .select("id, customer_name")
                .eq("client_id", client_id)
                .ilike("customer_address", f"%{address}%")
                .limit(1)
                .execute()
            )
            if result.data:
                row = result.data[0]
                print(
                    f"[{timestamp()}] INFO scheduling_agent: "
                    f"Found customer by address → {row['customer_name']} (id={row['id']})"
                )
                return row["id"]
        except Exception as e:
            print(f"[{timestamp()}] WARN scheduling_agent: Address customer lookup failed — {e}")

    # Try name match
    if name:
        try:
            result = (
                supabase.table("customers")
                .select("id, customer_name")
                .eq("client_id", client_id)
                .ilike("customer_name", f"%{name}%")
                .limit(1)
                .execute()
            )
            if result.data:
                row = result.data[0]
                print(
                    f"[{timestamp()}] INFO scheduling_agent: "
                    f"Found customer by name → {row['customer_name']} (id={row['id']})"
                )
                return row["id"]
        except Exception as e:
            print(f"[{timestamp()}] WARN scheduling_agent: Name customer lookup failed — {e}")

    # Not found — create a new record (no phone available from scheduling message)
    try:
        record = {
            "client_id":        client_id,
            "customer_name":    name or "Unknown",
            "customer_address": address,
        }
        result = supabase.table("customers").insert(record).execute()
        customer_id = result.data[0]["id"]
        print(
            f"[{timestamp()}] INFO scheduling_agent: "
            f"Created customer id={customer_id} name={name or 'Unknown'}"
        )
        return customer_id

    except Exception as e:
        print(f"[{timestamp()}] ERROR scheduling_agent: create_customer failed — {e}")
        return None


# ---------------------------------------------------------------------------
# Step 5 — Insert schedule record
# ---------------------------------------------------------------------------

def _create_schedule(
    client_id: str,
    job_id: str,
    customer_id: str,
    scheduled_date: str | None,
    scheduled_time: str | None,
    assigned_employee_ids: list,
    notes: str | None,
) -> str | None:
    """
    Insert a row into the schedule table.
    Returns the new schedule_id UUID, or None on failure.
    """
    try:
        supabase = get_supabase()
        record = {
            "client_id":             client_id,
            "job_id":                job_id,
            "customer_id":           customer_id,
            "scheduled_date":        scheduled_date,
            "scheduled_time":        scheduled_time,
            "assigned_employee_ids": assigned_employee_ids,
            "notes":                 notes,
            "status":                "scheduled",
        }
        result = supabase.table("schedule").insert(record).execute()
        schedule_id = result.data[0]["id"]
        print(f"[{timestamp()}] INFO scheduling_agent: Created schedule id={schedule_id}")
        return schedule_id

    except Exception as e:
        print(f"[{timestamp()}] ERROR scheduling_agent: create_schedule failed — {e}")
        return None


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_date(iso_date: str | None) -> str:
    """'2026-03-20' → 'Friday Mar 20'"""
    if not iso_date:
        return "TBD"
    try:
        d = date.fromisoformat(iso_date)
        return d.strftime("%A %b %-d")   # "Friday Mar 20"
    except ValueError:
        return iso_date


def _fmt_time(hhmm: str | None) -> str:
    """'08:00' → '8:00am', '14:30' → '2:30pm'"""
    if not hhmm:
        return "TBD"
    try:
        t = datetime.strptime(hhmm, "%H:%M")
        return t.strftime("%-I:%M%p").lower()  # "8:00am"
    except ValueError:
        return hhmm


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def handle_scheduling(
    client: dict,
    employee: dict | None,
    raw_input: str,
    from_number: str,
) -> str:
    """
    Handle an inbound scheduling SMS.

    Args:
        client:      Full client dict (clients table row)
        employee:    Full employee dict, or None if the owner is texting
        raw_input:   Raw SMS body
        from_number: Sender's E.164 phone number — confirmation reply goes here

    Returns:
        Status string for logging ("ok", "missing_date", "parse_error", "error")
    """
    client_id    = client["id"]
    client_phone = client.get("phone") or client.get("telnyx_number")
    today_iso    = date.today().isoformat()

    sender_name = (employee or {}).get("name", "there")
    print(f"[{timestamp()}] INFO scheduling_agent: Starting | client={client_id} sender={from_number}")

    # ------------------------------------------------------------------
    # Step 1 — Parse with Claude
    # ------------------------------------------------------------------
    parsed = _parse_with_claude(raw_input, today_iso)

    if not parsed:
        send_sms(
            to_number=from_number,
            message_body=(
                "Sorry, I couldn't parse that schedule request. "
                "Try: 'Schedule [job type] at [address] on [date] at [time]'"
            ),
            from_number=client_phone,
        )
        return "parse_error"

    job_type      = parsed.get("job_type") or "job"
    address       = parsed.get("address")
    customer_name = parsed.get("customer_name")
    sched_date    = parsed.get("scheduled_date")
    sched_time    = parsed.get("scheduled_time")
    emp_name      = parsed.get("assigned_employee_name")
    notes         = parsed.get("notes")

    # ------------------------------------------------------------------
    # Step 1b — Ask for date if missing
    # ------------------------------------------------------------------
    if not sched_date:
        send_sms(
            to_number=from_number,
            message_body=f"Got it — what date and time should I schedule this {job_type} for?",
            from_number=client_phone,
        )
        print(f"[{timestamp()}] INFO scheduling_agent: Missing date — asked sender for it")
        return "missing_date"

    # ------------------------------------------------------------------
    # Step 2 — Resolve assigned employee
    # ------------------------------------------------------------------
    assigned_employee    = _resolve_employee(client_id, emp_name)
    assigned_employee_ids = [assigned_employee["id"]] if assigned_employee else []

    # ------------------------------------------------------------------
    # Step 3 — Resolve or create customer
    # ------------------------------------------------------------------
    customer_id = _resolve_customer(client_id, customer_name, address)

    if not customer_id:
        send_sms(
            to_number=from_number,
            message_body="Something went wrong saving the customer. Please try again.",
            from_number=client_phone,
        )
        return "error"

    # ------------------------------------------------------------------
    # Step 4 — Create job record (status: scheduled)
    # ------------------------------------------------------------------
    job_id = create_job(
        client_id=client_id,
        customer_id=customer_id,
        job_type=job_type,
        raw_input=raw_input,
        job_description=f"{job_type} at {address or 'TBD'}",
    )

    if not job_id:
        send_sms(
            to_number=from_number,
            message_body="Something went wrong creating the job. Please try again.",
            from_number=client_phone,
        )
        return "error"

    # create_job defaults to status='new' — update to 'scheduled'
    update_job_status(job_id, "scheduled")

    # ------------------------------------------------------------------
    # Step 5 — Create schedule record
    # ------------------------------------------------------------------
    schedule_id = _create_schedule(
        client_id=client_id,
        job_id=job_id,
        customer_id=customer_id,
        scheduled_date=sched_date,
        scheduled_time=sched_time,
        assigned_employee_ids=assigned_employee_ids,
        notes=notes,
    )

    if not schedule_id:
        # Job was created but schedule insert failed — log it, still confirm
        print(f"[{timestamp()}] WARN scheduling_agent: schedule insert failed but job {job_id} was created")

    # ------------------------------------------------------------------
    # Step 6 — Send confirmation SMS
    # ------------------------------------------------------------------
    display_date     = _fmt_date(sched_date)
    display_time     = _fmt_time(sched_time)
    display_emp      = assigned_employee["name"].split()[0] if assigned_employee else None
    display_address  = address or "address TBD"
    display_job      = job_type.title()

    if display_emp:
        confirmation = (
            f"Scheduled! {display_job} at {display_address} on {display_date} "
            f"at {display_time}. Assigned to {display_emp}. "
            f"Reply 'jobs today' to see full schedule."
        )
    else:
        confirmation = (
            f"Scheduled! {display_job} at {display_address} on {display_date} "
            f"at {display_time}. No tech assigned yet. "
            f"Reply 'jobs today' to see full schedule."
        )

    sms_result = send_sms(
        to_number=from_number,
        message_body=confirmation,
        from_number=client_phone,
    )
    if not sms_result["success"]:
        print(f"[{timestamp()}] ERROR scheduling_agent: Confirmation SMS failed — {sms_result['error']}")

    # ------------------------------------------------------------------
    # Step 7 — Log the inbound message
    # ------------------------------------------------------------------
    try:
        log_message(
            client_id=client_id,
            direction="inbound",
            from_number=from_number,
            to_number=client_phone,
            body=raw_input,
            agent_used="scheduling_agent",
            job_id=job_id,
        )
    except Exception as e:
        print(f"[{timestamp()}] WARN scheduling_agent: log_message failed — {e}")

    print(
        f"[{timestamp()}] INFO scheduling_agent: Complete. "
        f"job_id={job_id} schedule_id={schedule_id}"
    )
    return "ok"
