"""
sms_router.py — Inbound SMS routing (Telnyx-outbound-blocked edition)

Important constraint: Telnyx outbound to workers is blocked at the
carrier level. This router NEVER calls send_sms() — every code path
either updates DB state or logs and returns silently.

Inbound SMS to the Telnyx brand number is still useful for one thing:
TCPA opt-in/opt-out tracking. Workers themselves should clock in via
the PWA (`/pwa/clock`); inbound CLOCK IN texts are ignored.

Routing order:
    1. STOP / YES / START / UNSTOP — TCPA opt-in/opt-out (DB-only;
       optin_agent's outbound confirmation will silently fail at the
       carrier, but the consent state is still recorded correctly)
    2. Everything else — log and ignore. The PWA owns every other
       worker interaction. Hard Rule #2 forbids customer-facing SMS,
       so we never reply to non-employees either.

Hard rules: #2 (no SMS to anyone, ever, from this router), #4 (multi-
tenant lookups), #5 (raw payload saved by sms_receive.py before this).
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_client import get_client_by_phone


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

    # Step 1 — TCPA opt-in / opt-out (DB state only; outbound SMS will fail at carrier)
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

    # Step 2 — Everything else: log and ignore. The PWA owns clock-in,
    # job updates, AI chat, everything. Outbound SMS is dead at the carrier
    # so we cannot redirect via SMS even if we wanted to.
    print(f"[{timestamp()}] INFO sms_router: Ignored (PWA owns this) — body[:60]={body[:60]!r}")
    return "ignored"
