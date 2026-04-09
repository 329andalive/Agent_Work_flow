"""
sms_router.py — Inbound SMS routing (PWA-pivot edition, April 2026)

SMS is one-way notification only. The 8-step conversational router
was replaced with a 3-step handler:
    1. STOP / YES / START / UNSTOP — TCPA opt-in/opt-out (always first)
    2. CLOCK IN / CLOCK OUT — delegate to clock_agent (echoes back)
    3. Everything else — one-line PWA redirect (employees only, Rule #2)

Hard rules: #2 (no SMS to non-employees), #4 (multi-tenant lookups),
#5 (raw payload saved by sms_receive.py before this runs).
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_client import get_client_by_phone
from execution.db_employee import get_employee_by_phone

PWA_URL = os.environ.get("BOLTS11_PWA_URL", "https://app.bolts11.com/pwa/")

def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _safe(label, fn, *args, **kwargs):
    """Run fn; log + swallow exceptions. Returns fn's value, or None on failure."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        print(f"[{timestamp()}] ERROR sms_router: {label} failed — {e}")
        return None


def route_message(sms_data: dict) -> str:
    """Route one inbound SMS. Returns a short tag identifying the path taken."""
    from_number = sms_data.get("from_number", "unknown")
    to_number = sms_data.get("to_number", "unknown")
    body = sms_data.get("body", "") or ""
    print(f"[{timestamp()}] INFO sms_router: msg_id={sms_data.get('message_id', '?')} from={from_number}")

    client = get_client_by_phone(to_number)
    if not client:
        print(f"[{timestamp()}] WARN sms_router: No client for {to_number}")
        return "no_client"
    body_upper = body.strip().upper()

    # Step 1 — TCPA opt-in / opt-out (always first, applies to everyone)
    if body_upper == "STOP":
        from execution.optin_agent import handle_stop
        _safe("handle_stop", handle_stop, client, from_number)
        return "optin_stop"
    if body_upper in ("YES", "START", "UNSTOP"):
        from execution.db_consent import check_consent
        from execution.optin_agent import handle_yes
        if not _safe("check_consent", check_consent, client["id"], from_number):
            _safe("handle_yes", handle_yes, client, from_number)
        return "optin_yes"

    # Step 2 — CLOCK IN / CLOCK OUT (employees only — clock_agent echoes back)
    employee = get_employee_by_phone(client["id"], from_number)
    if employee and (body_upper.startswith("CLOCK IN") or body_upper.startswith("CLOCK OUT")):
        from execution.clock_agent import handle_clock
        _safe("handle_clock", handle_clock,
              client=client, employee=employee, raw_input=body, from_number=from_number)
        return "clock_agent"

    # Step 3 — Everything else → PWA redirect. Hard Rule #2: never SMS non-employees.
    if not employee:
        print(f"[{timestamp()}] INFO sms_router: Unknown sender {from_number} — ignored")
        return "unmatched"
    first = (employee.get("name") or "there").split()[0]
    msg = f"Hey {first}! Use the Bolts11 app for that.\nOpen it here: {PWA_URL}"
    from execution.sms_send import send_sms
    _safe("pwa_redirect_sms", send_sms,
          to_number=from_number, message_body=msg, from_number=client.get("phone", to_number))
    return "pwa_redirect"
