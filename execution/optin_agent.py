"""
optin_agent.py — Handles customer SMS opt-in / opt-out replies

Two entry points:

  handle_yes(client, customer_phone)
      Customer replied YES to the opt-in request we sent.
      Sets consent=true, sends a brief confirmation, cancels any pending_consent followup.

  handle_stop(client, customer_phone)
      Customer replied STOP.
      Revokes consent and sends the required unsubscribe confirmation.
      (Telnyx also blocks delivery at the carrier level; this keeps our DB in sync.)

Usage:
    from execution.optin_agent import handle_yes, handle_stop
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_consent import set_consent, revoke_consent
from execution.db_connection import get_client as get_supabase
from execution.sms_send import send_sms
from execution.db_agent_activity import log_activity


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _cancel_pending_consent(client_id: str, customer_phone: str) -> None:
    """Mark any open pending_consent follow-ups as sent so they don't fire again."""
    try:
        supabase = get_supabase()

        # Find the customer id first
        cust = (
            supabase.table("customers")
            .select("id")
            .eq("client_id", client_id)
            .eq("customer_phone", customer_phone)
            .single()
            .execute()
        )
        if not cust.data:
            return

        customer_id = cust.data["id"]
        supabase.table("follow_ups").update({
            "status":    "sent",
            "sent_at":   datetime.now(timezone.utc).isoformat(),
            "sent_body": "customer_opted_in",
        }).eq("client_id", client_id).eq("customer_id", customer_id).eq("follow_up_type", "pending_consent").eq("status", "pending").execute()

    except Exception as e:
        print(f"[{_ts()}] WARN optin_agent: _cancel_pending_consent failed — {e}")


def handle_yes(client: dict, customer_phone: str) -> None:
    """
    Customer replied YES to our opt-in request.

    Args:
        client:         Full client dict (clients table row)
        customer_phone: Customer's E.164 phone number (the sender)
    """
    client_id    = client["id"]
    client_phone = client.get("phone") or client.get("telnyx_number")
    biz_name     = client.get("business_name", "us")

    print(f"[{_ts()}] INFO optin_agent: YES received from {customer_phone}")

    # Record consent
    set_consent(client_id, customer_phone, source="sms_yes")

    # Cancel the pending_consent followup so it doesn't fire again
    _cancel_pending_consent(client_id, customer_phone)

    # Send confirmation — TCR requires an explicit opt-in confirmation message
    confirm = (
        f"You're confirmed. {biz_name} will send you appointment and job updates by text. "
        f"Reply STOP at any time to unsubscribe."
    )
    send_sms(to_number=customer_phone, message_body=confirm, from_number=client_phone)

    print(f"[{_ts()}] INFO optin_agent: Consent confirmed for {customer_phone}")
    try:
        log_activity(
            client_phone=client_phone,
            agent_name="optin_agent",
            action_taken="consent_confirmed",
            input_summary=f"customer={customer_phone}",
            output_summary="sms_consent=True src=sms_yes",
            sms_sent=True,
        )
    except Exception:
        pass


def handle_stop(client: dict, customer_phone: str) -> None:
    """
    Customer replied STOP.
    Revoke consent in DB and send TCR-required unsubscribe confirmation.

    Args:
        client:         Full client dict (clients table row)
        customer_phone: Customer's E.164 phone number (the sender)
    """
    client_id    = client["id"]
    client_phone = client.get("phone") or client.get("telnyx_number")

    print(f"[{_ts()}] INFO optin_agent: STOP received from {customer_phone}")

    revoke_consent(client_id, customer_phone)

    # TCR requires a confirmation of unsubscribe be sent
    confirm = (
        "You have been unsubscribed and will receive no further messages. "
        "Reply START to re-subscribe."
    )
    send_sms(to_number=customer_phone, message_body=confirm, from_number=client_phone)

    print(f"[{_ts()}] INFO optin_agent: Consent revoked for {customer_phone}")
    try:
        log_activity(
            client_phone=client_phone,
            agent_name="optin_agent",
            action_taken="consent_revoked",
            input_summary=f"customer={customer_phone}",
            output_summary="sms_consent=False src=sms_stop",
            sms_sent=True,
        )
    except Exception:
        pass
