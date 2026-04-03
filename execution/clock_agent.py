"""
clock_agent.py — Field tech clock-in / clock-out via SMS

Flow (clock in):
  1. Detect action from raw SMS ("clock in" → 'in', "clock out" → 'out')
  2. Guard: already clocked in? → reply and exit
  3. Auto-match employee to today's scheduled job
  4. Insert open time_entries row
  5. Update matched schedule row → 'in_progress'
  6. Confirm to employee, notify owner

Flow (clock out):
  1. Detect action
  2. Find the most recent open time_entries row
  3. Close it: set clock_out, duration_minutes, status='closed'
  4. Update matched schedule row → 'completed'
  5. Confirm to employee, notify owner

Usage:
    from execution.clock_agent import handle_clock
    result = handle_clock(client, employee, raw_input, from_number)
"""

import os
import sys
from datetime import datetime, timezone

import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase
from execution.db_messages import log_message
from execution.sms_send import send_sms
from execution.db_agent_activity import log_activity


DEFAULT_TIMEZONE = "America/New_York"


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Timezone helpers
# ---------------------------------------------------------------------------

def _get_tz(client: dict) -> pytz.BaseTzInfo:
    """Return client's pytz timezone, falling back to America/New_York."""
    tz_name = client.get("timezone") or DEFAULT_TIMEZONE
    try:
        return pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        print(f"[{timestamp()}] WARN clock_agent: Unknown timezone '{tz_name}' — using {DEFAULT_TIMEZONE}")
        return pytz.timezone(DEFAULT_TIMEZONE)


def _fmt_local_time(utc_dt: datetime, tz: pytz.BaseTzInfo) -> str:
    """Convert a UTC datetime to local time string like '8:02am'."""
    local_dt = utc_dt.astimezone(tz)
    return local_dt.strftime("%-I:%M%p").lower()   # "8:02am"


def _fmt_duration(minutes: int) -> str:
    """Format integer minutes as '45 min' or '2h 15min'."""
    if minutes < 60:
        return f"{minutes} min"
    hours   = minutes // 60
    mins    = minutes %  60
    if mins == 0:
        return f"{hours}h"
    return f"{hours}h {mins}min"


# ---------------------------------------------------------------------------
# Step 1 — Detect action
# ---------------------------------------------------------------------------

_IN_KEYWORDS  = {"clock in", "clocking in", "on site", "starting", "in", "arrived", "here", "start"}
_OUT_KEYWORDS = {"clock out", "clocking out", "headed out", "leaving", "out", "done", "finished"}


def _detect_action(raw: str) -> str | None:
    """
    Return 'in', 'out', or None if the message doesn't match either direction.
    Checks multi-word phrases first so "clock out" beats bare "out".
    """
    text = raw.strip().lower()
    # Check multi-word phrases first (most specific)
    for kw in sorted(_IN_KEYWORDS,  key=len, reverse=True):
        if kw in text:
            return "in"
    for kw in sorted(_OUT_KEYWORDS, key=len, reverse=True):
        if kw in text:
            return "out"
    return None


# ---------------------------------------------------------------------------
# Step 2A helpers — clock-in path
# ---------------------------------------------------------------------------

def _find_open_entry(client_id: str, employee_id: str) -> dict | None:
    """Return the open time_entries row for this employee, or None."""
    try:
        supabase = get_supabase()
        result = (
            supabase.table("time_entries")
            .select("*")
            .eq("client_id", client_id)
            .eq("employee_id", employee_id)
            .eq("status", "open")
            .limit(1)
            .execute()
        )
        return (result.data or [None])[0]
    except Exception as e:
        print(f"[{timestamp()}] ERROR clock_agent: open-entry lookup failed — {e}")
        return None


def _match_schedule(client_id: str, employee_id: str, today_iso: str) -> dict | None:
    """
    Find the first un-cancelled schedule row today that has this employee assigned.
    Returns the full row or None.
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
        for row in rows:
            ids = row.get("assigned_employee_ids") or []
            if employee_id in ids:
                return row
        return None
    except Exception as e:
        print(f"[{timestamp()}] ERROR clock_agent: schedule match failed — {e}")
        return None


def _insert_time_entry(
    client_id: str,
    employee_id: str,
    schedule_id: str | None,
    job_id: str | None,
    clock_in_utc: datetime,
) -> str | None:
    """
    Insert an open time_entries row.
    Returns the new entry id, or None on failure.
    """
    try:
        supabase = get_supabase()
        record = {
            "client_id":   client_id,
            "employee_id": employee_id,
            "schedule_id": schedule_id,
            "job_id":      job_id,
            "clock_in":    clock_in_utc.isoformat(),
            "status":      "open",
        }
        result = supabase.table("time_entries").insert(record).execute()
        entry_id = result.data[0]["id"]
        print(f"[{timestamp()}] INFO clock_agent: Created time_entry id={entry_id}")
        return entry_id
    except Exception as e:
        print(f"[{timestamp()}] ERROR clock_agent: time_entry insert failed — {e}")
        return None


def _update_schedule_status(schedule_id: str | None, status: str) -> None:
    """Update a schedule row's status field. Silently skips if schedule_id is None."""
    if not schedule_id:
        return
    try:
        supabase = get_supabase()
        supabase.table("schedule").update({"status": status}).eq("id", schedule_id).execute()
        print(f"[{timestamp()}] INFO clock_agent: Schedule {schedule_id} → {status}")
    except Exception as e:
        print(f"[{timestamp()}] WARN clock_agent: schedule status update failed — {e}")


# ---------------------------------------------------------------------------
# Step 2B helpers — clock-out path
# ---------------------------------------------------------------------------

def _find_open_entry_latest(client_id: str, employee_id: str) -> dict | None:
    """Return the most recently opened open time_entry for this employee."""
    try:
        supabase = get_supabase()
        result = (
            supabase.table("time_entries")
            .select("*")
            .eq("client_id", client_id)
            .eq("employee_id", employee_id)
            .eq("status", "open")
            .order("clock_in", desc=True)
            .limit(1)
            .execute()
        )
        return (result.data or [None])[0]
    except Exception as e:
        print(f"[{timestamp()}] ERROR clock_agent: latest-open-entry lookup failed — {e}")
        return None


def _close_time_entry(entry_id: str, clock_out_utc: datetime, duration_minutes: int) -> bool:
    """Close a time_entries row with clock_out and duration. Returns True on success."""
    try:
        supabase = get_supabase()
        supabase.table("time_entries").update({
            "clock_out":         clock_out_utc.isoformat(),
            "duration_minutes":  duration_minutes,
            "status":            "closed",
            "updated_at":        clock_out_utc.isoformat(),
        }).eq("id", entry_id).execute()
        print(f"[{timestamp()}] INFO clock_agent: Closed time_entry id={entry_id} duration={duration_minutes}min")
        return True
    except Exception as e:
        print(f"[{timestamp()}] ERROR clock_agent: time_entry close failed — {e}")
        return False


# ---------------------------------------------------------------------------
# Job label helper (for SMS copy)
# ---------------------------------------------------------------------------

def _job_label(schedule_row: dict | None) -> str | None:
    """
    Return a short job label string like "Pump Job at 42 Oak St", or None.
    Fetches the linked job and customer records.
    """
    if not schedule_row:
        return None
    try:
        supabase = get_supabase()
        job_id      = schedule_row.get("job_id")
        customer_id = schedule_row.get("customer_id")

        job_type = None
        if job_id:
            j = supabase.table("jobs").select("job_type").eq("id", job_id).single().execute()
            job_type = (j.data or {}).get("job_type")

        address = None
        if customer_id:
            c = supabase.table("customers").select("customer_address").eq("id", customer_id).single().execute()
            address = (c.data or {}).get("customer_address")

        if job_type and address:
            return f"{job_type.title()} at {address}"
        if job_type:
            return job_type.title()
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def handle_clock(
    client: dict,
    employee: dict,
    raw_input: str,
    from_number: str,
) -> str:
    """
    Handle a clock-in or clock-out SMS from a field employee.

    Args:
        client:      Full client dict (clients table row)
        employee:    Full employee dict (employees table row)
        raw_input:   Raw SMS body
        from_number: Sender's E.164 phone number — reply goes here

    Returns:
        Status string for logging ("clock_in", "clock_out", "already_in",
        "not_clocked_in", "unknown_action", "error")
    """
    client_id    = client["id"]
    client_phone = client.get("phone") or client.get("telnyx_number")
    owner_phone  = client.get("owner_mobile") or client.get("phone")
    employee_id  = employee.get("id")
    emp_name     = employee.get("name", "there")
    emp_first    = emp_name.split()[0]

    if not employee_id:
        print(f"[{timestamp()}] ERROR clock_agent: No employee UUID — cannot clock in/out")
        send_sms(
            to_number=from_number,
            message_body="Clock in/out failed — you're not set up as a worker yet. Ask your dispatcher to add you.",
            from_number=client_phone,
        )
        return "error"

    tz          = _get_tz(client)
    now_utc     = datetime.now(timezone.utc)
    today_iso   = now_utc.astimezone(tz).date().isoformat()
    local_time  = _fmt_local_time(now_utc, tz)

    print(
        f"[{timestamp()}] INFO clock_agent: Starting | "
        f"client={client_id} employee={emp_name} sender={from_number}"
    )

    # ------------------------------------------------------------------
    # Step 1 — Detect action
    # ------------------------------------------------------------------
    action = _detect_action(raw_input)
    if action is None:
        send_sms(
            to_number=from_number,
            message_body="Reply 'clock in' to start your shift or 'clock out' to end it.",
            from_number=client_phone,
        )
        print(f"[{timestamp()}] WARN clock_agent: Could not detect action from: '{raw_input[:80]}'")
        return "unknown_action"

    print(f"[{timestamp()}] INFO clock_agent: Action detected → clock {action} for {emp_name}")

    # ==================================================================
    # CLOCK IN
    # ==================================================================
    if action == "in":

        # Guard — already clocked in?
        open_entry = _find_open_entry(client_id, employee_id)
        if open_entry:
            send_sms(
                to_number=from_number,
                message_body=(
                    f"You're already clocked in. "
                    "Reply 'clock out' to end your current shift first."
                ),
                from_number=client_phone,
            )
            print(f"[{timestamp()}] WARN clock_agent: {emp_name} already clocked in (entry={open_entry['id']})")
            return "already_in"

        # Match to today's schedule
        schedule_row  = _match_schedule(client_id, employee_id, today_iso)
        schedule_id   = schedule_row["id"]   if schedule_row else None
        job_id        = schedule_row.get("job_id") if schedule_row else None

        if schedule_row:
            print(f"[{timestamp()}] INFO clock_agent: Matched schedule id={schedule_id} job_id={job_id}")
        else:
            print(f"[{timestamp()}] WARN clock_agent: No scheduled job found for {emp_name} today ({today_iso})")

        # Insert open time entry
        entry_id = _insert_time_entry(client_id, employee_id, schedule_id, job_id, now_utc)
        if not entry_id:
            send_sms(
                to_number=from_number,
                message_body=(
                    "Something went wrong recording your time. "
                    "Please try again or contact your supervisor."
                ),
                from_number=client_phone,
            )
            return "error"

        # Update schedule → in_progress
        _update_schedule_status(schedule_id, "in_progress")

        # Confirm to employee
        job_label = _job_label(schedule_row)
        if job_label:
            employee_msg = (
                f"Clocked in at {local_time}. "
                f"Matched to: {job_label}. "
                "Reply 'clock out' when done."
            )
            owner_msg = f"{emp_first} clocked in at {local_time}. Job: {job_label}."
        else:
            employee_msg = (
                f"Clocked in at {local_time}. "
                "No scheduled job found for today — time will still be recorded."
            )
            owner_msg = f"{emp_first} clocked in at {local_time}. No job matched."

        send_sms(to_number=from_number,   message_body=employee_msg, from_number=client_phone)
        print(f"[{timestamp()}] INFO clock_agent: Confirmed clock-in to {from_number}")

        # Send today's dispatch route and start first job
        try:
            from execution.dispatch_chain import get_todays_route, build_route_sms, start_first_job
            route = get_todays_route(client_id, employee_id)
            if route:
                route_msg = build_route_sms(route, emp_name, client.get("business_name", ""))
                send_sms(to_number=from_number, message_body=route_msg, from_number=client_phone)
                print(f"[{timestamp()}] INFO clock_agent: Sent dispatch route ({len(route)} jobs) to {emp_first}")

                # Auto-start the first job
                started = start_first_job(client_id, employee_id, entry_id)
                if started:
                    print(f"[{timestamp()}] INFO clock_agent: Auto-started first job {started.get('id', '')[:8]}")
        except Exception as e:
            print(f"[{timestamp()}] WARN clock_agent: Dispatch route send failed — {e}")

        # Notify owner (failure is non-fatal)
        if owner_phone and owner_phone != client_phone:
            owner_result = send_sms(to_number=owner_phone, message_body=owner_msg, from_number=client_phone)
            if not owner_result["success"]:
                print(f"[{timestamp()}] WARN clock_agent: Owner notify failed — {owner_result['error']}")
            else:
                print(f"[{timestamp()}] INFO clock_agent: Owner notified at {owner_phone}")

        # Log inbound event
        try:
            log_message(
                client_id=client_id,
                direction="inbound",
                from_number=from_number,
                to_number=client_phone,
                body=raw_input,
                agent_used="clock_agent",
                job_id=job_id,
            )
        except Exception as e:
            print(f"[{timestamp()}] WARN clock_agent: log_message failed — {e}")

        print(f"[{timestamp()}] INFO clock_agent: Complete. clock_in entry={entry_id}")
        try:
            log_activity(client_phone=client_phone, agent_name="clock_agent",
                action_taken="clock_in", input_summary=raw_input[:120],
                output_summary=f"Clocked in{' — ' + job_label if job_label else ''}", sms_sent=True)
        except Exception:
            pass
        return "clock_in"

    # ==================================================================
    # CLOCK OUT
    # ==================================================================
    # action == "out"

    # Find the most recent open entry
    open_entry = _find_open_entry_latest(client_id, employee_id)
    if not open_entry:
        send_sms(
            to_number=from_number,
            message_body="You're not clocked in. Reply 'clock in' to start.",
            from_number=client_phone,
        )
        print(f"[{timestamp()}] WARN clock_agent: {emp_name} tried to clock out but has no open entry")
        return "not_clocked_in"

    # Calculate duration
    clock_in_str = open_entry.get("clock_in")
    try:
        clock_in_utc = datetime.fromisoformat(clock_in_str)
        if clock_in_utc.tzinfo is None:
            clock_in_utc = clock_in_utc.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as e:
        print(f"[{timestamp()}] ERROR clock_agent: Could not parse clock_in timestamp '{clock_in_str}' — {e}")
        clock_in_utc = now_utc   # fallback: zero duration

    duration_minutes = max(0, round((now_utc - clock_in_utc).total_seconds() / 60))
    duration_str     = _fmt_duration(duration_minutes)

    # Close the time entry
    closed = _close_time_entry(open_entry["id"], now_utc, duration_minutes)
    if not closed:
        send_sms(
            to_number=from_number,
            message_body=(
                "Something went wrong recording your time. "
                "Please try again or contact your supervisor."
            ),
            from_number=client_phone,
        )
        return "error"

    # Update schedule → completed
    schedule_id = open_entry.get("schedule_id")
    _update_schedule_status(schedule_id, "completed")

    # Rebuild job label from the linked schedule row (if any)
    schedule_row = None
    if schedule_id:
        try:
            supabase = get_supabase()
            r = supabase.table("schedule").select("*").eq("id", schedule_id).single().execute()
            schedule_row = r.data
        except Exception:
            pass

    job_label = _job_label(schedule_row)

    # Confirm to employee
    employee_msg = (
        f"Clocked out at {local_time}. "
        f"Time on job: {duration_str}. "
        "Good work today!"
    )

    if job_label:
        owner_msg = (
            f"{emp_first} clocked out at {local_time}. "
            f"Job: {job_label}. Time: {duration_str}."
        )
    else:
        owner_msg = (
            f"{emp_first} clocked out at {local_time}. "
            f"Time recorded: {duration_str}."
        )

    send_sms(to_number=from_number,   message_body=employee_msg, from_number=client_phone)
    print(f"[{timestamp()}] INFO clock_agent: Confirmed clock-out to {from_number}")

    # Notify owner (failure is non-fatal)
    if owner_phone and owner_phone != client_phone:
        owner_result = send_sms(to_number=owner_phone, message_body=owner_msg, from_number=client_phone)
        if not owner_result["success"]:
            print(f"[{timestamp()}] WARN clock_agent: Owner notify failed — {owner_result['error']}")
        else:
            print(f"[{timestamp()}] INFO clock_agent: Owner notified at {owner_phone}")

    # Log inbound event
    job_id = open_entry.get("job_id")
    try:
        log_message(
            client_id=client_id,
            direction="inbound",
            from_number=from_number,
            to_number=client_phone,
            body=raw_input,
            agent_used="clock_agent",
            job_id=job_id,
        )
    except Exception as e:
        print(f"[{timestamp()}] WARN clock_agent: log_message failed — {e}")

    print(
        f"[{timestamp()}] INFO clock_agent: Complete. "
        f"clock_out entry={open_entry['id']} duration={duration_minutes}min"
    )
    try:
        log_activity(client_phone=client_phone, agent_name="clock_agent",
            action_taken="clock_out", input_summary=raw_input[:120],
            output_summary=f"Clocked out — {duration_minutes} minutes", sms_sent=True)
    except Exception:
        pass
    return "clock_out"
