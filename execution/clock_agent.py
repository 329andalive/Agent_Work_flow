"""
clock_agent.py — Field tech clock-in / clock-out via SMS

Flow:
  1. Detect whether the message is a clock-in or clock-out
  2. Send a confirmation SMS back to the employee
  3. Log the event to the messages table

Usage:
    from execution.clock_agent import run
    run(client_phone="+12074190986", employee={"name": "Mike", "phone": "+12075550123"}, raw_input="on site")
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.sms_send import send_sms
from execution.db_messages import log_message
from execution.db_client import get_client_by_phone


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Clock-in / clock-out keyword detection
# ---------------------------------------------------------------------------
CLOCK_IN_KEYWORDS  = ["on site", "starting", "clocking in", "clock in", "start", "here", "arrived"]
CLOCK_OUT_KEYWORDS = ["done", "finished", "leaving", "clocking out", "clock out", "headed out", "wrapping up"]


def detect_clock_direction(text: str) -> str:
    """
    Return 'in' or 'out' based on keywords in the message.
    Defaults to 'in' if ambiguous.
    """
    text_lower = text.lower()
    for kw in CLOCK_OUT_KEYWORDS:
        if kw in text_lower:
            return "out"
    return "in"


def run(client_phone: str, employee: dict, raw_input: str) -> None:
    """
    Handle a clock-in or clock-out event from a field employee.

    Args:
        client_phone: The business's Telnyx number (from_number for SMS)
        employee:     Employee dict from db_employee (must include 'name' and 'phone')
        raw_input:    Raw SMS text from the employee
    """
    name           = employee.get("name", "there")
    employee_phone = employee.get("phone")
    direction      = detect_clock_direction(raw_input)

    now_utc   = datetime.now(timezone.utc)
    time_str  = now_utc.strftime("%-I:%M %p UTC")   # e.g. "7:32 AM UTC"
    event_str = "in" if direction == "in" else "out"

    print(f"[{timestamp()}] INFO clock_agent: Clock-{event_str} | employee={name} | phone={employee_phone}")

    # ------------------------------------------------------------------
    # Confirmation SMS to the employee
    # ------------------------------------------------------------------
    if direction == "in":
        message = f"Got it {name}, clocked in at {time_str}."
    else:
        message = f"Got it {name}, clocked out at {time_str}. Have a good one."

    sms_result = send_sms(
        to_number=employee_phone,
        message_body=message,
        from_number=client_phone,
    )
    if not sms_result["success"]:
        print(f"[{timestamp()}] ERROR clock_agent: SMS failed — {sms_result['error']}")

    # ------------------------------------------------------------------
    # Log the event — resolve client_id from client_phone
    # ------------------------------------------------------------------
    try:
        client = get_client_by_phone(client_phone)
        if client:
            log_message(
                client_id=client["id"],
                direction="inbound",
                from_number=employee_phone,
                to_number=client_phone,
                body=raw_input,
                agent_used="clock_agent",
            )
    except Exception as e:
        print(f"[{timestamp()}] WARN clock_agent: log_message failed — {e}")
