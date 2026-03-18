"""
noshow_agent.py — No-show detection and foreman response handling

Two entry points:

  check_noshows(client)
      Called by cron.py. Scans today's schedule for jobs that are 60+ min
      past their start time with no clock-in. Fires SMS alerts to foremans
      and the owner. Returns a summary string for cron logging.

  handle_noshow_response(client, employee, raw_input, from_number)
      Called by sms_router.py when a foreman or owner replies "on it" or
      "reassign" to a no-show alert. Generates a customer-facing SMS via
      Claude and resolves the open noshow_alert record.

Usage:
    from execution.noshow_agent import check_noshows, handle_noshow_response
"""

import os
import sys
from datetime import datetime, timezone

import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.call_claude import call_claude
from execution.db_connection import get_client as get_supabase
from execution.sms_send import send_sms
from execution.db_agent_activity import log_activity


DEFAULT_TIMEZONE   = "America/New_York"
NOSHOW_THRESHOLD   = 60   # minutes past scheduled start before alert fires


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
        print(
            f"[{timestamp()}] WARN noshow_agent: Unknown timezone '{tz_name}' "
            f"— using {DEFAULT_TIMEZONE}"
        )
        return pytz.timezone(DEFAULT_TIMEZONE)


def _fmt_time(hhmm: str | None) -> str:
    """'08:00' → '8:00am', '14:30' → '2:30pm', None → 'TBD'"""
    if not hhmm:
        return "TBD"
    try:
        t = datetime.strptime(hhmm, "%H:%M")
        return t.strftime("%-I:%M%p").lower()
    except ValueError:
        return hhmm


# ---------------------------------------------------------------------------
# Shared enrichment helpers (same pattern as briefing_agent.py)
# ---------------------------------------------------------------------------

def _fetch_customer(customer_id: str) -> dict | None:
    if not customer_id:
        return None
    try:
        supabase = get_supabase()
        result = (
            supabase.table("customers")
            .select("customer_name, customer_address, customer_phone")
            .eq("id", customer_id)
            .single()
            .execute()
        )
        return result.data
    except Exception:
        return None


def _fetch_job(job_id: str) -> dict | None:
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
    """Return first names for a list of employee UUIDs. Silently skips failures."""
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
        print(f"[{timestamp()}] WARN noshow_agent: Employee name lookup failed — {e}")
        return []


def _get_alert_recipients(client: dict) -> list[str]:
    """
    Build deduplicated list of phones to receive no-show alerts.
    Includes all active foreman + owner employees, plus client['owner_mobile'].
    Same pattern as briefing_agent._get_recipients().
    """
    phones: set[str] = set()

    owner_mobile = client.get("owner_mobile")
    if owner_mobile:
        phones.add(owner_mobile)

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
    except Exception as e:
        print(f"[{timestamp()}] WARN noshow_agent: Recipient query failed — {e}")

    return list(phones)


# ---------------------------------------------------------------------------
# check_noshows helpers
# ---------------------------------------------------------------------------

def _open_alert_exists(schedule_id: str) -> bool:
    """Return True if an open noshow_alert already exists for this schedule row."""
    try:
        supabase = get_supabase()
        result = (
            supabase.table("noshow_alerts")
            .select("id")
            .eq("schedule_id", schedule_id)
            .eq("status", "open")
            .limit(1)
            .execute()
        )
        return bool(result.data)
    except Exception as e:
        print(f"[{timestamp()}] WARN noshow_agent: Alert existence check failed — {e}")
        return False   # Fail open — proceed to alert


def _clock_in_exists(client_id: str, schedule_id: str) -> bool:
    """Return True if any time_entry (open or closed) exists for this schedule row."""
    try:
        supabase = get_supabase()
        result = (
            supabase.table("time_entries")
            .select("id")
            .eq("client_id", client_id)
            .eq("schedule_id", schedule_id)
            .in_("status", ["open", "closed"])
            .limit(1)
            .execute()
        )
        return bool(result.data)
    except Exception as e:
        print(f"[{timestamp()}] WARN noshow_agent: Clock-in check failed — {e}")
        return False   # Fail open — proceed to alert


def _insert_noshow_alert(
    client_id: str,
    schedule_id: str,
    employee_id: str | None,
) -> str | None:
    """
    Insert a noshow_alert row. Returns the new alert id, or None on failure.
    """
    try:
        supabase = get_supabase()
        record = {
            "client_id":          client_id,
            "schedule_id":        schedule_id,
            "employee_id":        employee_id,
            "triggered_by":       "cron",
            "status":             "open",
            "customer_notified":  False,
        }
        result = supabase.table("noshow_alerts").insert(record).execute()
        alert_id = result.data[0]["id"]
        print(f"[{timestamp()}] INFO noshow_agent: Inserted noshow_alert id={alert_id}")
        return alert_id
    except Exception as e:
        print(f"[{timestamp()}] ERROR noshow_agent: noshow_alert insert failed — {e}")
        return None


def _set_schedule_status(schedule_id: str, status: str) -> None:
    """Update a schedule row's status. Logs errors but never raises."""
    try:
        supabase = get_supabase()
        supabase.table("schedule").update({"status": status}).eq("id", schedule_id).execute()
        print(f"[{timestamp()}] INFO noshow_agent: Schedule {schedule_id} → {status}")
    except Exception as e:
        print(f"[{timestamp()}] WARN noshow_agent: Schedule status update failed — {e}")


# ---------------------------------------------------------------------------
# FUNCTION 1: check_noshows
# ---------------------------------------------------------------------------

def check_noshows(client: dict) -> str:
    """
    Scan today's schedule for jobs that are 60+ min past start with no clock-in.
    Fires SMS alerts and inserts noshow_alert rows. Called by cron.py.

    Returns a summary string for the cron log.
    """
    client_id     = client["id"]
    business_name = client.get("business_name", "unknown")
    client_phone  = client.get("phone")   # Telnyx from-number

    print(
        f"[{timestamp()}] INFO noshow_agent: Starting check | "
        f"client={client_id} ({business_name})"
    )

    # ------------------------------------------------------------------
    # Step 1 — Get current local time
    # ------------------------------------------------------------------
    tz        = _get_tz(client)
    local_now = datetime.now(tz)
    today_iso = local_now.date().isoformat()
    now_minutes = local_now.hour * 60 + local_now.minute   # minutes since midnight

    # ------------------------------------------------------------------
    # Step 2 — Fetch today's 'scheduled' rows that have a time set
    # ------------------------------------------------------------------
    try:
        supabase = get_supabase()
        result = (
            supabase.table("schedule")
            .select("*")
            .eq("client_id", client_id)
            .eq("scheduled_date", today_iso)
            .eq("status", "scheduled")
            .not_.is_("scheduled_time", "null")
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        print(f"[{timestamp()}] ERROR noshow_agent: Schedule query failed — {e}")
        return f"Error — skipped {business_name}"

    print(f"[{timestamp()}] INFO noshow_agent: {len(rows)} 'scheduled' rows with times today")

    alerts_fired = 0

    for row in rows:
        schedule_id    = row["id"]
        scheduled_time = row.get("scheduled_time")   # "HH:MM" string

        # Calculate minutes_late
        try:
            h, m    = map(int, scheduled_time.split(":"))
            sched_minutes = h * 60 + m
            minutes_late  = now_minutes - sched_minutes
        except (ValueError, AttributeError):
            print(
                f"[{timestamp()}] WARN noshow_agent: "
                f"Cannot parse scheduled_time='{scheduled_time}' for schedule {schedule_id}"
            )
            continue

        if minutes_late < NOSHOW_THRESHOLD:
            continue   # Not late enough yet

        print(
            f"[{timestamp()}] INFO noshow_agent: Late row schedule_id={schedule_id} "
            f"minutes_late={minutes_late}"
        )

        # ------------------------------------------------------------------
        # Step 3 — Skip if alert already exists
        # ------------------------------------------------------------------
        if _open_alert_exists(schedule_id):
            print(f"[{timestamp()}] INFO noshow_agent: Alert already open for {schedule_id} — skipping")
            continue

        # ------------------------------------------------------------------
        # Step 4 — Skip if tech has already clocked in
        # ------------------------------------------------------------------
        if _clock_in_exists(client_id, schedule_id):
            print(f"[{timestamp()}] INFO noshow_agent: Clock-in found for {schedule_id} — not a no-show")
            continue

        # ------------------------------------------------------------------
        # Step 5 — Enrich
        # ------------------------------------------------------------------
        emp_ids    = row.get("assigned_employee_ids") or []
        emp_names  = _fetch_employee_names(client_id, emp_ids)
        emp_label  = ", ".join(emp_names) if emp_names else "Unassigned tech"

        customer   = _fetch_customer(row.get("customer_id"))
        job        = _fetch_job(row.get("job_id"))
        job_type   = (job or {}).get("job_type") or "job"
        address    = (customer or {}).get("customer_address") or "address on file"
        time_disp  = _fmt_time(scheduled_time)

        # ------------------------------------------------------------------
        # Step 6 — Insert noshow_alert
        # ------------------------------------------------------------------
        first_emp_id = emp_ids[0] if emp_ids else None
        alert_id = _insert_noshow_alert(client_id, schedule_id, first_emp_id)
        if not alert_id:
            # Already logged inside helper — skip to next row
            continue

        # ------------------------------------------------------------------
        # Step 7 — Send alerts to foremans + owner
        # ------------------------------------------------------------------
        alert_msg = (
            f"No-show alert: {emp_label} hasn't clocked in for "
            f"{job_type.title()} at {address} (scheduled {time_disp}). "
            f"That's {minutes_late} min ago. "
            f"Reply 'on it' or 'reassign' to notify the customer."
        )

        recipients = _get_alert_recipients(client)
        for phone in recipients:
            r = send_sms(to_number=phone, message_body=alert_msg, from_number=client_phone)
            if r["success"]:
                print(f"[{timestamp()}] INFO noshow_agent: Alert sent to {phone}")
            else:
                print(f"[{timestamp()}] WARN noshow_agent: Alert SMS to {phone} failed — {r['error']}")

        # ------------------------------------------------------------------
        # Step 8 — Update schedule → needs_attention
        # ------------------------------------------------------------------
        _set_schedule_status(schedule_id, "needs_attention")
        alerts_fired += 1

    summary = (
        f"Fired {alerts_fired} no-show alerts for {business_name}"
        if alerts_fired
        else f"No no-shows detected for {business_name}"
    )
    print(f"[{timestamp()}] INFO noshow_agent: Complete. {summary}")
    try:
        log_activity(
            client_phone=client.get("phone", ""),
            agent_name="noshow_agent",
            action_taken="noshow_check",
            input_summary=today_iso,
            output_summary=summary,
            sms_sent=alerts_fired > 0,
        )
    except Exception:
        pass
    return summary


# ---------------------------------------------------------------------------
# FUNCTION 2: handle_noshow_response
# ---------------------------------------------------------------------------

_RESPONSE_ON_IT_KEYWORDS  = {"on it", "on my way", "got it", "handling it"}
_RESPONSE_REASSIGN_KEYWORDS = {"reassign", "re-assign", "find someone", "send someone"}


def _detect_response(raw: str) -> str | None:
    """Return 'on_it', 'reassign', or None."""
    text = raw.strip().lower()
    for kw in sorted(_RESPONSE_ON_IT_KEYWORDS,  key=len, reverse=True):
        if kw in text:
            return "on_it"
    for kw in sorted(_RESPONSE_REASSIGN_KEYWORDS, key=len, reverse=True):
        if kw in text:
            return "reassign"
    return None


_ON_IT_SYSTEM = """You are a professional SMS assistant for a trade business.
Write a short, friendly SMS to a customer explaining their technician
is on the way and will arrive shortly. Use the business name provided.
Do not mention the technician's name. Keep it under 160 characters.
Sound human, not robotic. Return only the message text, no quotes."""

_REASSIGN_SYSTEM = """You are a professional SMS assistant for a trade business.
Write a short, friendly SMS to a customer explaining there's been a
scheduling change and a different technician will be arriving.
Give a vague but reassuring ETA ("later this morning" or "this afternoon").
Use the business name provided. Keep it under 160 characters.
Sound human, not robotic. Return only the message text, no quotes."""


def _generate_customer_msg(
    system_prompt: str,
    business_name: str,
    job_type: str,
    address: str | None,
    fallback: str,
) -> str:
    """
    Call Claude (haiku) to generate a customer-facing delay SMS.
    Returns fallback string if Claude fails.
    """
    user_prompt = (
        f"Business: {business_name}\n"
        f"Job type: {job_type}\n"
    )
    if address:
        user_prompt += f"Address: {address}\n"

    raw = call_claude(system_prompt, user_prompt, model="haiku", max_tokens=200)
    if raw and raw.strip():
        return raw.strip()

    print(f"[{timestamp()}] WARN noshow_agent: Claude generation failed — using fallback message")
    return fallback


def _resolve_open_alert(client_id: str) -> dict | None:
    """Return the most recent open noshow_alert for this client, or None."""
    try:
        supabase = get_supabase()
        result = (
            supabase.table("noshow_alerts")
            .select("*")
            .eq("client_id", client_id)
            .eq("status", "open")
            .order("alert_sent_at", desc=True)
            .limit(1)
            .execute()
        )
        return (result.data or [None])[0]
    except Exception as e:
        print(f"[{timestamp()}] ERROR noshow_agent: Alert lookup failed — {e}")
        return None


def _resolve_alert(
    alert_id: str,
    resolved_by: str,
    now_utc: datetime,
) -> None:
    """Mark a noshow_alert as resolved."""
    try:
        supabase = get_supabase()
        supabase.table("noshow_alerts").update({
            "resolved_by":       resolved_by,
            "resolved_at":       now_utc.isoformat(),
            "customer_notified": True,
            "status":            "resolved",
        }).eq("id", alert_id).execute()
        print(f"[{timestamp()}] INFO noshow_agent: Alert {alert_id} resolved by '{resolved_by}'")
    except Exception as e:
        print(f"[{timestamp()}] ERROR noshow_agent: Alert resolve failed — {e}")


def handle_noshow_response(
    client: dict,
    employee: dict,
    raw_input: str,
    from_number: str,
) -> str:
    """
    Handle a foreman/owner SMS reply to a no-show alert ("on it" / "reassign").

    Args:
        client:      Full client dict
        employee:    Full employee dict
        raw_input:   Raw SMS body
        from_number: Sender's phone number (for reply)

    Returns:
        Status string for logging ("on_it", "reassign", "unknown_response",
        "no_open_alert", "error")
    """
    client_id     = client["id"]
    business_name = client.get("business_name", "your business")
    client_phone  = client.get("phone")
    sender_name   = (employee or {}).get("name", "there")

    print(
        f"[{timestamp()}] INFO noshow_agent: Response received | "
        f"client={client_id} sender={from_number} input='{raw_input[:80]}'"
    )

    # ------------------------------------------------------------------
    # Step 1 — Detect response type
    # ------------------------------------------------------------------
    response = _detect_response(raw_input)
    if response is None:
        send_sms(
            to_number=from_number,
            message_body=(
                "Reply 'on it' if you're handling it, "
                "or 'reassign' if you need someone else sent."
            ),
            from_number=client_phone,
        )
        print(f"[{timestamp()}] WARN noshow_agent: Unrecognized response from {from_number}: '{raw_input[:80]}'")
        return "unknown_response"

    print(f"[{timestamp()}] INFO noshow_agent: Response type → {response}")

    # ------------------------------------------------------------------
    # Step 2 — Find the open noshow_alert
    # ------------------------------------------------------------------
    alert = _resolve_open_alert(client_id)
    if not alert:
        send_sms(
            to_number=from_number,
            message_body="No open no-show alerts found for your account.",
            from_number=client_phone,
        )
        print(f"[{timestamp()}] WARN noshow_agent: No open alert found for client {client_id}")
        return "no_open_alert"

    alert_id    = alert["id"]
    schedule_id = alert.get("schedule_id")
    print(f"[{timestamp()}] INFO noshow_agent: Found open alert id={alert_id} schedule_id={schedule_id}")

    # ------------------------------------------------------------------
    # Step 3 — Fetch context for the alert
    # ------------------------------------------------------------------
    schedule_row = None
    if schedule_id:
        try:
            supabase = get_supabase()
            r = supabase.table("schedule").select("*").eq("id", schedule_id).single().execute()
            schedule_row = r.data
        except Exception as e:
            print(f"[{timestamp()}] WARN noshow_agent: Schedule fetch failed — {e}")

    customer   = _fetch_customer((schedule_row or {}).get("customer_id"))
    job        = _fetch_job((schedule_row or {}).get("job_id"))
    job_type   = (job or {}).get("job_type") or "job"
    address    = (customer or {}).get("customer_address")
    cust_phone = (customer or {}).get("customer_phone")

    now_utc = datetime.now(timezone.utc)

    # ==================================================================
    # STEP 4A — "on it"
    # ==================================================================
    if response == "on_it":
        fallback_msg = (
            f"Hi, this is {business_name}. Your technician is on the way "
            f"and will arrive shortly. We apologize for the delay."
        )
        customer_msg = _generate_customer_msg(
            system_prompt=_ON_IT_SYSTEM,
            business_name=business_name,
            job_type=job_type,
            address=address,
            fallback=fallback_msg,
        )

        # Send customer notification
        if cust_phone:
            cust_result = send_sms(
                to_number=cust_phone,
                message_body=customer_msg,
                from_number=client_phone,
            )
            if cust_result["success"]:
                print(f"[{timestamp()}] INFO noshow_agent: Customer notified at {cust_phone}")
            else:
                print(f"[{timestamp()}] ERROR noshow_agent: Customer SMS failed — {cust_result['error']}")
        else:
            print(f"[{timestamp()}] WARN noshow_agent: No customer phone — skipping customer notification")

        # Reply to foreman / owner
        send_sms(
            to_number=from_number,
            message_body="Got it. Customer notified. Keep us posted on arrival.",
            from_number=client_phone,
        )

        # Resolve alert
        _resolve_alert(alert_id, "on_it", now_utc)

        # Reset schedule → scheduled (tech is on the way)
        _set_schedule_status(schedule_id, "scheduled")

        print(f"[{timestamp()}] INFO noshow_agent: Complete. on_it response processed.")
        try:
            log_activity(client_phone=client_phone, agent_name="noshow_agent",
                action_taken="noshow_on_it", input_summary=raw_input[:120],
                output_summary=f"alert_id={alert_id} customer_notified={bool(cust_phone)}", sms_sent=True)
        except Exception:
            pass
        return "on_it"

    # ==================================================================
    # STEP 4B — "reassign"
    # ==================================================================
    fallback_msg = (
        f"Hi, this is {business_name}. We've had a scheduling change "
        f"and a technician will be with you later today."
    )
    customer_msg = _generate_customer_msg(
        system_prompt=_REASSIGN_SYSTEM,
        business_name=business_name,
        job_type=job_type,
        address=None,   # Don't expose address in reassign message
        fallback=fallback_msg,
    )

    # Send customer notification
    if cust_phone:
        cust_result = send_sms(
            to_number=cust_phone,
            message_body=customer_msg,
            from_number=client_phone,
        )
        if cust_result["success"]:
            print(f"[{timestamp()}] INFO noshow_agent: Customer notified of reassignment at {cust_phone}")
        else:
            print(f"[{timestamp()}] ERROR noshow_agent: Customer SMS failed — {cust_result['error']}")
    else:
        print(f"[{timestamp()}] WARN noshow_agent: No customer phone — skipping customer notification")

    # Reply to foreman / owner
    send_sms(
        to_number=from_number,
        message_body=(
            "Got it. Customer notified of reassignment. "
            "Update the schedule with the new tech when confirmed."
        ),
        from_number=client_phone,
    )

    # Resolve alert (schedule stays 'needs_attention' — reassignment pending)
    _resolve_alert(alert_id, "reassign", now_utc)

    print(f"[{timestamp()}] INFO noshow_agent: Complete. reassign response processed.")
    try:
        log_activity(client_phone=client_phone, agent_name="noshow_agent",
            action_taken="noshow_reassign", input_summary=raw_input[:120],
            output_summary=f"alert_id={alert_id} customer_notified={bool(cust_phone)}", sms_sent=True)
    except Exception:
        pass
    return "reassign"
