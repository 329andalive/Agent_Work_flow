"""
sms_send.py — Outbound SMS via Telnyx REST API v2

Usage:
    from execution.sms_send import send_sms
    result = send_sms(to_number="+15555555555", message_body="Hello!")

Or run directly:
    python execution/sms_send.py
"""

import os
import requests
from datetime import datetime
from dotenv import load_dotenv

# Load credentials from .env
load_dotenv()

TELNYX_API_KEY = os.getenv("TELNYX_API_KEY")
TELNYX_PHONE_NUMBER = os.getenv("TELNYX_PHONE_NUMBER")

TELNYX_API_URL = "https://api.telnyx.com/v2/messages"
MAX_SMS_LENGTH = 1600  # Telnyx soft limit before splitting


def timestamp():
    """Return a formatted timestamp string for log lines."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def split_message(text, max_length=MAX_SMS_LENGTH):
    """
    Split a long message into chunks under max_length characters.
    Returns a list of strings. Single messages return a list with one item.
    """
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        chunks.append(text[:max_length])
        text = text[max_length:]
    return chunks


def _log_sms(client_phone: str, recipient_phone: str, message_type: str,
             body: str, telnyx_message_id: str = None, status: str = "sent") -> None:
    """
    Log an outbound SMS to the sms_message_log table.
    Never raises — a failed log must never block SMS delivery.
    """
    try:
        from execution.db_connection import get_client as get_supabase
        sb = get_supabase()
        sb.table("sms_message_log").insert({
            "client_phone": client_phone,
            "recipient_phone": recipient_phone,
            "message_type": message_type,
            "body": (body or "")[:4000],
            "telnyx_message_id": telnyx_message_id,
            "status": status,
        }).execute()
    except Exception as e:
        # Log but never block — SMS delivery is more important than the audit log
        print(f"[{timestamp()}] WARN sms_send: sms_message_log insert failed — {e}")


def send_sms(to_number: str, message_body: str, from_number: str = None,
             message_type: str = "invoice") -> dict:
    """
    Send an outbound SMS via Telnyx and log to sms_message_log.

    Args:
        to_number:    Recipient phone number in E.164 format (e.g. "+15555555555")
        message_body: Text content of the message
        from_number:  Sender number. Defaults to TELNYX_PHONE_NUMBER from .env
        message_type: Category for logging. Valid values: route, schedule_nudge,
                      booking_confirm, appt_reminder, cancellation, invoice,
                      review_ask, waitlist_notify, no_show_followup,
                      carry_forward_notify, wave_assignment.
                      Defaults to 'invoice' for backward compatibility.

    Returns:
        dict with keys:
            success (bool)
            message_id (str or None)
            error (str or None)
    """
    # Fall back to the .env default if no from_number provided
    sender = from_number or TELNYX_PHONE_NUMBER

    # Guard against missing credentials
    if not TELNYX_API_KEY:
        print(f"[{timestamp()}] ERROR sms_send: TELNYX_API_KEY not set in .env")
        return {"success": False, "message_id": None, "error": "Missing TELNYX_API_KEY"}

    if not sender:
        print(f"[{timestamp()}] ERROR sms_send: No from_number provided and TELNYX_PHONE_NUMBER not set in .env")
        return {"success": False, "message_id": None, "error": "Missing from_number"}

    # Split long messages into chunks and send each one
    chunks = split_message(message_body)
    if len(chunks) > 1:
        print(f"[{timestamp()}] INFO sms_send: Message is {len(message_body)} chars — splitting into {len(chunks)} parts")

    results = []
    for i, chunk in enumerate(chunks):
        part_label = f"(part {i+1}/{len(chunks)})" if len(chunks) > 1 else ""
        result = _send_single(to_number, chunk, sender, part_label)
        results.append(result)
        # If any chunk fails, log failure and stop
        if not result["success"]:
            _log_sms(sender, to_number, message_type, chunk, None, "failed")
            return result
        # Log successful send — never block on log failure
        _log_sms(sender, to_number, message_type, chunk, result.get("message_id"), "sent")

    # Return the last (or only) result — all chunks succeeded
    return results[-1]


def _send_single(to_number: str, body: str, from_number: str, label: str = "") -> dict:
    """
    Internal: send one SMS chunk via the Telnyx API.
    Not intended to be called directly — use send_sms() instead.
    """
    headers = {
        "Authorization": f"Bearer {TELNYX_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": from_number,
        "to": to_number,
        "text": body,
    }

    try:
        print(f"[{timestamp()}] INFO sms_send: Sending SMS {label} → {to_number} ({len(body)} chars)")
        response = requests.post(TELNYX_API_URL, json=payload, headers=headers, timeout=10)
        response.raise_for_status()

        data = response.json()
        message_id = data.get("data", {}).get("id")
        print(f"[{timestamp()}] INFO sms_send: Sent successfully. message_id={message_id}")
        return {"success": True, "message_id": message_id, "error": None}

    except requests.exceptions.HTTPError as e:
        # Parse Telnyx error details if available
        try:
            error_detail = e.response.json()
        except Exception:
            error_detail = str(e)
        print(f"[{timestamp()}] ERROR sms_send: HTTP error sending SMS → {error_detail}")
        return {"success": False, "message_id": None, "error": str(error_detail)}

    except requests.exceptions.ConnectionError:
        print(f"[{timestamp()}] ERROR sms_send: Connection error — check network/API endpoint")
        return {"success": False, "message_id": None, "error": "Connection error"}

    except requests.exceptions.Timeout:
        print(f"[{timestamp()}] ERROR sms_send: Request timed out after 10s")
        return {"success": False, "message_id": None, "error": "Request timed out"}

    except Exception as e:
        print(f"[{timestamp()}] ERROR sms_send: Unexpected error — {e}")
        return {"success": False, "message_id": None, "error": str(e)}
