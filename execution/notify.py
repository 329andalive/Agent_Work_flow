"""
notify.py — Unified notification router

Every outbound message in the system goes through notify() instead of
calling send_sms() directly. This enforces three layers of permission:

  Layer 1 — Client switches: sms_outbound_enabled / email_outbound_enabled
  Layer 2 — Recipient type: employee (internal) vs customer (external)
  Layer 3 — Customer consent: customers.sms_consent (CTIA compliance)

If SMS is blocked (no 10DLC, no consent, employee opted out), the
router falls back to email via Resend. If email is also blocked
(no address on file, email disabled), it logs a delivery_blocked
event and surfaces a needs_attention card.

Usage:
    from execution.notify import notify

    # The router figures out SMS vs email automatically
    notify(
        client_id="uuid",
        to_phone="+12075551234",
        message=message,
        subject="Invoice from Your Business",      # email subject (optional)
        html_body="<h1>Invoice</h1>...",            # rich email (optional)
        channel="auto",                             # auto | sms | email
    )
"""

import os
import sys
import re
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# Cache client delivery settings for the request lifecycle
_client_cache = {}


def _get_client_settings(client_id: str) -> dict:
    """Load client delivery switches. Cached per client_id."""
    if client_id in _client_cache:
        return _client_cache[client_id]

    try:
        from execution.db_connection import get_client as get_supabase
        sb = get_supabase()
        result = sb.table("clients").select(
            "id, phone, owner_mobile, business_name, "
            "sms_outbound_enabled, email_outbound_enabled"
        ).eq("id", client_id).execute()
        if result.data:
            settings = result.data[0]
            _client_cache[client_id] = settings
            return settings
    except Exception as e:
        print(f"[{timestamp()}] WARN notify: client settings load failed — {e}")

    # Safe defaults: email only
    return {
        "id": client_id,
        "sms_outbound_enabled": False,
        "email_outbound_enabled": True,
    }


def _lookup_recipient(client_id: str, phone: str) -> dict:
    """
    Determine if a phone number belongs to an employee or customer.

    Returns:
        {
            "type": "employee" | "customer" | "unknown",
            "name": str,
            "email": str or None,
            "sms_consent": bool (customers only),
            "sms_opted_out": bool (employees only),
        }
    """
    if not phone:
        return {"type": "unknown", "name": "", "email": None, "sms_consent": False, "sms_opted_out": False}

    # Normalize phone for comparison
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        normalized = f"+1{digits}"
    elif len(digits) == 11 and digits.startswith("1"):
        normalized = f"+{digits}"
    else:
        normalized = phone

    try:
        from execution.db_connection import get_client as get_supabase
        sb = get_supabase()

        # Check employees first (internal team)
        emp_result = sb.table("employees").select(
            "id, name, phone, email, sms_opted_out"
        ).eq("client_id", client_id).eq("active", True).execute()

        for emp in (emp_result.data or []):
            emp_phone = emp.get("phone", "")
            emp_digits = re.sub(r"\D", "", emp_phone)
            if len(emp_digits) == 10:
                emp_normalized = f"+1{emp_digits}"
            elif len(emp_digits) == 11 and emp_digits.startswith("1"):
                emp_normalized = f"+{emp_digits}"
            else:
                emp_normalized = emp_phone

            if emp_normalized == normalized:
                return {
                    "type": "employee",
                    "name": emp.get("name", ""),
                    "email": emp.get("email"),
                    "sms_consent": True,  # employees don't need CTIA consent
                    "sms_opted_out": emp.get("sms_opted_out", False),
                }

        # Check customers
        cust_result = sb.table("customers").select(
            "id, customer_name, customer_phone, customer_email, sms_consent"
        ).eq("client_id", client_id).execute()

        for cust in (cust_result.data or []):
            cust_phone = cust.get("customer_phone", "")
            cust_digits = re.sub(r"\D", "", cust_phone)
            if len(cust_digits) == 10:
                cust_normalized = f"+1{cust_digits}"
            elif len(cust_digits) == 11 and cust_digits.startswith("1"):
                cust_normalized = f"+{cust_digits}"
            else:
                cust_normalized = cust_phone

            if cust_normalized == normalized:
                return {
                    "type": "customer",
                    "name": cust.get("customer_name", ""),
                    "email": cust.get("customer_email"),
                    "sms_consent": cust.get("sms_consent", False),
                    "sms_opted_out": False,
                }

    except Exception as e:
        print(f"[{timestamp()}] WARN notify: recipient lookup failed — {e}")

    return {"type": "unknown", "name": "", "email": None, "sms_consent": False, "sms_opted_out": False}


def _can_sms(client_settings: dict, recipient: dict) -> bool:
    """
    Check all three layers:
      Layer 1: Client has 10DLC approved (sms_outbound_enabled)
      Layer 2: Recipient type (employee vs customer)
      Layer 3: Customer consent / employee opt-out
    """
    # Layer 1: Client-level switch
    if not client_settings.get("sms_outbound_enabled", False):
        return False

    # Layer 2 + 3: Recipient-specific checks
    rtype = recipient.get("type", "unknown")

    if rtype == "employee":
        # Internal team — no CTIA consent needed, but respect opt-out
        return not recipient.get("sms_opted_out", False)

    if rtype == "customer":
        # External customer — requires explicit consent
        return recipient.get("sms_consent", False)

    # Unknown recipient — don't SMS
    return False


def _can_email(client_settings: dict, recipient: dict) -> bool:
    """Check if email delivery is possible."""
    if not client_settings.get("email_outbound_enabled", True):
        return False
    if not recipient.get("email"):
        return False
    return True


def _log_blocked(client_id: str, to_phone: str, reason: str, message: str):
    """Log a blocked delivery and surface a needs_attention card."""
    print(f"[{timestamp()}] WARN notify: delivery blocked for {to_phone} — {reason}")
    try:
        from execution.db_connection import get_client as get_supabase
        sb = get_supabase()

        # Log to agent_activity
        sb.table("agent_activity").insert({
            "client_phone": "",  # we have client_id, not phone
            "agent_name": "notify",
            "action_taken": "delivery_blocked",
            "input_summary": f"to={to_phone} reason={reason}",
            "output_summary": message[:200],
            "sms_sent": False,
        }).execute()

        # Surface needs_attention card
        sb.table("needs_attention").insert({
            "client_phone": "",
            "card_type": "delivery_blocked",
            "priority": "medium",
            "raw_context": f"to={to_phone} reason={reason}",
            "claude_suggestion": f"Could not deliver message to {to_phone}. {reason}. "
                                 "Add an email address or enable SMS for this recipient.",
            "status": "open",
        }).execute()

    except Exception:
        pass  # Logging failure is non-fatal


def notify(
    client_id: str,
    to_phone: str,
    message: str,
    subject: str = None,
    html_body: str = None,
    to_email: str = None,
    to_name: str = None,
    channel: str = "auto",
    message_type: str = "notification",
) -> dict:
    """
    Send a notification through the best available channel.

    Args:
        client_id:    UUID of the sending client (business)
        to_phone:     Recipient phone number (used for lookup + SMS)
        message:      Plain text message body
        subject:      Email subject line (optional, auto-generated if missing)
        html_body:    Rich HTML email body (optional, falls back to plain text)
        to_email:     Override email address (skips lookup)
        to_name:      Override recipient name (skips lookup)
        channel:      "auto" (default), "sms", or "email"
        message_type: Logging category (notification, invoice, proposal, etc.)

    Returns:
        {"success": bool, "channel": "sms"|"email"|"blocked", "error": str|None}
    """
    # Load client settings and recipient info
    client = _get_client_settings(client_id)
    recipient = _lookup_recipient(client_id, to_phone)

    # Use overrides if provided
    if to_email:
        recipient["email"] = to_email
    if to_name:
        recipient["name"] = to_name

    business_name = client.get("business_name", "Bolts11")

    # ── Try SMS first (if channel allows) ──
    if channel in ("auto", "sms") and _can_sms(client, recipient):
        try:
            from execution.sms_send import send_sms
            client_phone = client.get("phone", "")
            result = send_sms(
                to_number=to_phone,
                message_body=message,
                from_number=client_phone,
                message_type=message_type,
            )
            if result.get("success"):
                return {"success": True, "channel": "sms", "error": None}
            else:
                print(f"[{timestamp()}] WARN notify: SMS failed — {result.get('error')}. Trying email fallback.")
        except Exception as e:
            print(f"[{timestamp()}] WARN notify: SMS send error — {e}. Trying email fallback.")

    # ── Try email (if channel allows) ──
    if channel in ("auto", "email") and _can_email(client, recipient):
        try:
            from execution.resend_agent import _send, _wrap

            email_subject = subject or f"Message from {business_name}"
            email_body = html_body or _wrap(
                f"<p>{message.replace(chr(10), '<br>')}</p>"
            )
            email_to = recipient.get("email", "")

            result = _send(
                to=[email_to],
                subject=email_subject,
                html=email_body if html_body else _wrap(
                    f"<h1>{business_name}</h1>"
                    f"<p>{message.replace(chr(10), '<br>')}</p>"
                ),
            )
            if result.get("success"):
                print(f"[{timestamp()}] INFO notify: Email sent to {email_to}")
                return {"success": True, "channel": "email", "error": None}
            else:
                print(f"[{timestamp()}] WARN notify: Email failed — {result.get('error')}")
        except Exception as e:
            print(f"[{timestamp()}] WARN notify: Email send error — {e}")

    # ── Both channels failed ──
    reason = _get_block_reason(client, recipient, channel)
    _log_blocked(client_id, to_phone, reason, message)

    return {"success": False, "channel": "blocked", "error": reason}


def _get_block_reason(client: dict, recipient: dict, channel: str) -> str:
    """Generate a human-readable reason for why delivery was blocked."""
    reasons = []

    if channel in ("auto", "sms"):
        if not client.get("sms_outbound_enabled"):
            reasons.append("SMS disabled (no 10DLC)")
        elif recipient.get("type") == "customer" and not recipient.get("sms_consent"):
            reasons.append("Customer has not opted in to SMS")
        elif recipient.get("type") == "employee" and recipient.get("sms_opted_out"):
            reasons.append("Employee opted out of SMS")

    if channel in ("auto", "email"):
        if not client.get("email_outbound_enabled"):
            reasons.append("Email disabled")
        elif not recipient.get("email"):
            reasons.append("No email address on file")

    return "; ".join(reasons) if reasons else "No delivery channel available"


def notify_document(
    client_id: str,
    to_phone: str,
    doc_type: str,
    doc_id: str,
    amount: float,
    customer_name: str,
    view_url: str,
    pay_url: str = None,
    to_email: str = None,
) -> dict:
    """
    Send a proposal or invoice to a customer through the best channel.
    Uses structured email templates for email delivery.

    This is the main entry point for /doc/send and /doc/approve flows.
    """
    client = _get_client_settings(client_id)
    recipient = _lookup_recipient(client_id, to_phone)
    if to_email:
        recipient["email"] = to_email

    business_name = client.get("business_name", "Bolts11")
    label = "Estimate" if doc_type == "proposal" else "Invoice"

    # ── Try SMS ──
    if _can_sms(client, recipient):
        try:
            from execution.sms_send import send_sms
            if doc_type == "proposal":
                sms_body = (
                    f"{business_name} sent you an estimate for ${amount:,.2f}.\n"
                    f"View here: {view_url}\n"
                    f"Reply YES to approve or NO to decline."
                )
            else:
                sms_body = (
                    f"{business_name} sent you an invoice for ${amount:,.2f}.\n"
                    f"View and pay here: {view_url}"
                )
                if pay_url:
                    sms_body += f"\nPay now: {pay_url}"

            result = send_sms(
                to_number=to_phone,
                message_body=sms_body,
                from_number=client.get("phone", ""),
                message_type=doc_type,
            )
            if result.get("success"):
                return {"success": True, "channel": "sms", "error": None}
        except Exception as e:
            print(f"[{timestamp()}] WARN notify: document SMS failed — {e}")

    # ── Try email ──
    if _can_email(client, recipient):
        try:
            from execution.resend_agent import send_document_email
            result = send_document_email(
                to_email=recipient["email"],
                to_name=customer_name,
                doc_type=doc_type,
                doc_number=doc_id[:8].upper(),
                business_name=business_name,
                amount=amount,
                view_url=view_url,
                pay_url=pay_url,
            )
            if result.get("success"):
                print(f"[{timestamp()}] INFO notify: {label} emailed to {recipient['email']}")
                return {"success": True, "channel": "email", "error": None}
        except Exception as e:
            print(f"[{timestamp()}] WARN notify: document email failed — {e}")

    # ── Blocked ──
    reason = _get_block_reason(client, recipient, "auto")
    _log_blocked(client_id, to_phone, reason, f"{label} for {customer_name} ${amount:.2f}")
    return {"success": False, "channel": "blocked", "error": reason}
