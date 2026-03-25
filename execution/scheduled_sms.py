"""
scheduled_sms.py — Scheduled SMS jobs: nudges, reminders, no-show marking

Three functions meant to be called by a cron job, Railway scheduler, or
the /api/admin/run-scheduled-sms endpoint for manual testing.

HARD RULE: Never send SMS without checking sms_consent first.

Usage:
    from execution.scheduled_sms import (
        send_class_nudges,
        send_appointment_reminders,
        mark_no_shows,
    )
"""

import os
import sys
from datetime import datetime, timezone, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase
from execution.sms_send import send_sms


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 1. send_class_nudges — weekly/biweekly "check the schedule" SMS
# ---------------------------------------------------------------------------

def send_class_nudges(client_phone: str) -> dict:
    """
    Send schedule nudge SMS to opted-in customers for a class board.

    Rules:
    - Only sends if client settings_json has nudge_enabled=true
    - Only sends if today matches nudge_day (e.g. 'monday')
    - Respects nudge_frequency ('weekly' or 'biweekly')
    - Never sends more than one nudge per customer per 6 days
    - HARD RULE: checks sms_consent before every send

    Returns:
        {"sent": int, "skipped": int, "errors": int}
    """
    sb = get_supabase()
    result = {"sent": 0, "skipped": 0, "errors": 0}

    # Load board + settings
    try:
        board_result = sb.table("class_boards").select(
            "token, settings_json"
        ).eq("client_phone", client_phone).eq("board_type", "class").limit(1).execute()
        if not board_result.data:
            print(f"[{timestamp()}] INFO scheduled_sms: No class board for {client_phone}")
            return result
        board = board_result.data[0]
    except Exception as e:
        print(f"[{timestamp()}] ERROR scheduled_sms: board lookup failed — {e}")
        return result

    settings = board.get("settings_json") or {}
    if isinstance(settings, str):
        import json
        try:
            settings = json.loads(settings)
        except Exception:
            settings = {}

    if not settings.get("nudge_enabled"):
        print(f"[{timestamp()}] INFO scheduled_sms: Nudges not enabled for {client_phone}")
        return result

    # Check nudge_day
    nudge_day = (settings.get("nudge_day") or "monday").lower()
    today_day = date.today().strftime("%A").lower()
    if today_day != nudge_day:
        print(f"[{timestamp()}] INFO scheduled_sms: Today is {today_day}, nudge_day is {nudge_day} — skipping")
        return result

    # Check biweekly: skip if even week number and frequency is biweekly
    nudge_freq = (settings.get("nudge_frequency") or "weekly").lower()
    if nudge_freq == "biweekly":
        week_num = date.today().isocalendar()[1]
        if week_num % 2 == 0:
            print(f"[{timestamp()}] INFO scheduled_sms: Biweekly skip — even week {week_num}")
            return result

    # Build booking URL
    board_token = board.get("token", "")
    base_url = os.environ.get("BOLTS11_BASE_URL", "https://bolts11.com")
    booking_url = f"{base_url}/book/{board_token}" if board_token else ""

    # Load business name
    business_name = "Your provider"
    try:
        c = sb.table("clients").select("business_name, id").eq("phone", client_phone).execute()
        if c.data:
            business_name = c.data[0].get("business_name", business_name)
            client_id = c.data[0]["id"]
        else:
            print(f"[{timestamp()}] WARN scheduled_sms: No client for {client_phone}")
            return result
    except Exception as e:
        print(f"[{timestamp()}] ERROR scheduled_sms: client lookup failed — {e}")
        return result

    # Load opted-in customers
    try:
        customers = sb.table("customers").select(
            "id, customer_phone, customer_name"
        ).eq("client_id", client_id).eq("sms_consent", True).execute()
        cust_list = customers.data or []
    except Exception as e:
        print(f"[{timestamp()}] ERROR scheduled_sms: customer query failed — {e}")
        return result

    if not cust_list:
        print(f"[{timestamp()}] INFO scheduled_sms: No opted-in customers for {client_phone}")
        return result

    # Check last nudge per customer (from sms_message_log)
    six_days_ago = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()

    for cust in cust_list:
        phone = cust.get("customer_phone")
        if not phone:
            result["skipped"] += 1
            continue

        # Check if nudge was sent in last 6 days
        try:
            recent = sb.table("sms_message_log").select("id").eq(
                "recipient_phone", phone
            ).eq("message_type", "schedule_nudge").gte(
                "sent_at", six_days_ago
            ).limit(1).execute()
            if recent.data:
                result["skipped"] += 1
                continue
        except Exception:
            pass  # If check fails, err on the side of sending

        # Send nudge
        try:
            sms_result = send_sms(
                to_number=phone,
                message_body=(
                    f"{business_name} schedule is updated — check what's coming up: "
                    f"{booking_url}\n"
                    f"Reply STOP to unsubscribe."
                ),
                from_number=client_phone,
                message_type="schedule_nudge",
            )
            if sms_result.get("success"):
                result["sent"] += 1
            else:
                result["errors"] += 1
        except Exception as e:
            print(f"[{timestamp()}] WARN scheduled_sms: nudge SMS failed for {phone} — {e}")
            result["errors"] += 1

    print(f"[{timestamp()}] INFO scheduled_sms: Nudges for {client_phone} — sent={result['sent']} skipped={result['skipped']} errors={result['errors']}")
    return result


# ---------------------------------------------------------------------------
# 2. send_appointment_reminders — tomorrow's bookings
# ---------------------------------------------------------------------------

def send_appointment_reminders() -> dict:
    """
    Send reminders for all bookings with slot_date = tomorrow.

    HARD RULE: checks sms_consent before every send.

    Returns:
        {"sent": int, "skipped": int, "errors": int}
    """
    sb = get_supabase()
    result = {"sent": 0, "skipped": 0, "errors": 0}
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    # Find all slots for tomorrow
    try:
        slots = sb.table("class_slots").select(
            "id, title, slot_date, start_time, client_phone, status"
        ).eq("slot_date", tomorrow).neq("status", "cancelled").execute()
        slot_list = slots.data or []
    except Exception as e:
        print(f"[{timestamp()}] ERROR scheduled_sms: tomorrow slots query failed — {e}")
        return result

    if not slot_list:
        print(f"[{timestamp()}] INFO scheduled_sms: No slots for tomorrow ({tomorrow})")
        return result

    # Cache business names
    biz_cache = {}

    for slot in slot_list:
        slot_id = slot["id"]
        slot_title = slot.get("title", "Class")
        slot_time = slot.get("start_time", "")
        slot_client_phone = slot.get("client_phone", "")

        if slot_client_phone not in biz_cache:
            try:
                c = sb.table("clients").select("business_name, id").eq("phone", slot_client_phone).execute()
                biz_cache[slot_client_phone] = c.data[0].get("business_name", "Your provider") if c.data else "Your provider"
            except Exception:
                biz_cache[slot_client_phone] = "Your provider"

        business_name = biz_cache[slot_client_phone]

        # Get enrolled bookings
        try:
            enrollments = sb.table("class_enrollments").select(
                "customer_phone, customer_id"
            ).eq("slot_id", slot_id).eq("status", "enrolled").execute()
            enr_list = enrollments.data or []
        except Exception as e:
            print(f"[{timestamp()}] WARN scheduled_sms: enrollment query failed for slot {slot_id[:8]} — {e}")
            continue

        for enr in enr_list:
            cust_phone = enr.get("customer_phone")
            if not cust_phone:
                result["skipped"] += 1
                continue

            # Check sms_consent
            has_consent = False
            if enr.get("customer_id"):
                try:
                    cust = sb.table("customers").select("sms_consent").eq("id", enr["customer_id"]).execute()
                    if cust.data and cust.data[0].get("sms_consent"):
                        has_consent = True
                except Exception:
                    pass

            if not has_consent:
                result["skipped"] += 1
                continue

            try:
                sms_result = send_sms(
                    to_number=cust_phone,
                    message_body=(
                        f"{business_name}: Reminder — {slot_title} tomorrow at {slot_time}. "
                        f"Reply CANCEL to cancel."
                    ),
                    from_number=slot_client_phone,
                    message_type="appt_reminder",
                )
                if sms_result.get("success"):
                    result["sent"] += 1
                else:
                    result["errors"] += 1
            except Exception as e:
                print(f"[{timestamp()}] WARN scheduled_sms: reminder SMS failed for {cust_phone} — {e}")
                result["errors"] += 1

    print(f"[{timestamp()}] INFO scheduled_sms: Reminders — sent={result['sent']} skipped={result['skipped']} errors={result['errors']}")
    return result


# ---------------------------------------------------------------------------
# 3. mark_no_shows — bookings for slots ended 2+ hours ago still 'enrolled'
# ---------------------------------------------------------------------------

def mark_no_shows() -> dict:
    """
    Find bookings for slots that ended more than 2 hours ago where
    status is still 'enrolled'. Mark as 'no_show', increment
    customers.no_show_count.

    Returns:
        {"marked": int}
    """
    sb = get_supabase()
    result = {"marked": 0}
    now = datetime.now(timezone.utc)
    today_str = date.today().isoformat()

    # Find today's and yesterday's slots that have ended
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()

    try:
        slots = sb.table("class_slots").select(
            "id, end_time, slot_date"
        ).in_("slot_date", [today_str, yesterday_str]).neq("status", "cancelled").execute()
        slot_list = slots.data or []
    except Exception as e:
        print(f"[{timestamp()}] ERROR scheduled_sms: no-show slot query failed — {e}")
        return result

    two_hours_ago = now - timedelta(hours=2)

    for slot in slot_list:
        end_time = slot.get("end_time")
        slot_date = slot.get("slot_date", "")
        if not end_time:
            continue

        # Parse end datetime
        try:
            end_dt = datetime.fromisoformat(f"{slot_date}T{end_time}:00+00:00")
        except (ValueError, TypeError):
            continue

        if end_dt > two_hours_ago:
            continue  # Slot hasn't been over for 2 hours yet

        slot_id = slot["id"]

        # Find enrolled bookings (should have been marked done by now)
        try:
            enrolled = sb.table("class_enrollments").select(
                "id, customer_id"
            ).eq("slot_id", slot_id).eq("status", "enrolled").execute()
            enr_list = enrolled.data or []
        except Exception:
            continue

        for enr in enr_list:
            # Mark as no_show
            try:
                sb.table("class_enrollments").update({
                    "status": "no_show",
                }).eq("id", enr["id"]).execute()

                # Increment customer no_show_count
                if enr.get("customer_id"):
                    try:
                        cust = sb.table("customers").select("no_show_count").eq("id", enr["customer_id"]).execute()
                        current = (cust.data[0].get("no_show_count") or 0) if cust.data else 0
                        sb.table("customers").update({
                            "no_show_count": current + 1,
                        }).eq("id", enr["customer_id"]).execute()
                    except Exception:
                        pass  # Non-fatal — column may not exist yet

                result["marked"] += 1
            except Exception as e:
                print(f"[{timestamp()}] WARN scheduled_sms: no-show update failed — {e}")

    if result["marked"]:
        print(f"[{timestamp()}] INFO scheduled_sms: Marked {result['marked']} no-shows")
    return result
