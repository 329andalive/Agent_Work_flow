"""
db_consent.py — Customer SMS consent helpers (TCR / 10DLC compliance)

Three operations:
  check_consent  — returns True if the customer has opted in
  set_consent    — records opt-in (source: 'web_form', 'sms_yes', 'manual', etc.)
  revoke_consent — records opt-out (STOP reply)

Every write is idempotent — safe to call multiple times.

Usage:
    from execution.db_consent import check_consent, set_consent, revoke_consent
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def check_consent(client_id: str, customer_phone: str) -> bool:
    """
    Return True if this customer has active SMS consent for this client.
    Fails safe — returns False on any DB error (no consent assumed).

    Args:
        client_id:      UUID of the client (business owner)
        customer_phone: Customer's E.164 phone number
    """
    try:
        supabase = get_supabase()
        result = (
            supabase.table("customers")
            .select("sms_consent")
            .eq("client_id", client_id)
            .eq("customer_phone", customer_phone)
            .single()
            .execute()
        )
        if result.data:
            return bool(result.data.get("sms_consent", False))
        return False

    except Exception as e:
        print(f"[{_ts()}] WARN db_consent: check_consent failed for {customer_phone} — {e}")
        return False


def set_consent(client_id: str, customer_phone: str, source: str = "unknown") -> bool:
    """
    Mark a customer as having given SMS consent.
    Creates the customer row if it doesn't exist yet (upsert via update).

    Args:
        client_id:      UUID of the client
        customer_phone: Customer's E.164 phone number
        source:         Where consent was captured ('web_form', 'sms_yes', 'manual')

    Returns:
        True on success, False on failure.
    """
    try:
        supabase = get_supabase()
        now_iso = datetime.now(timezone.utc).isoformat()

        # Try to update an existing record first
        result = (
            supabase.table("customers")
            .update({
                "sms_consent":     True,
                "sms_consent_at":  now_iso,
                "sms_consent_src": source,
            })
            .eq("client_id", client_id)
            .eq("customer_phone", customer_phone)
            .execute()
        )

        if result.data:
            print(f"[{_ts()}] INFO db_consent: Consent set for {customer_phone} (src={source})")
            return True

        # No row matched — customer doesn't exist yet, create a minimal record
        supabase.table("customers").insert({
            "client_id":       client_id,
            "customer_phone":  customer_phone,
            "customer_name":   "Customer",
            "sms_consent":     True,
            "sms_consent_at":  now_iso,
            "sms_consent_src": source,
        }).execute()
        print(f"[{_ts()}] INFO db_consent: New customer row created with consent for {customer_phone}")
        return True

    except Exception as e:
        print(f"[{_ts()}] ERROR db_consent: set_consent failed for {customer_phone} — {e}")
        return False


def revoke_consent(client_id: str, customer_phone: str) -> bool:
    """
    Mark a customer as having revoked SMS consent (STOP reply).
    No-ops gracefully if the customer isn't found.

    Args:
        client_id:      UUID of the client
        customer_phone: Customer's E.164 phone number

    Returns:
        True on success, False on failure.
    """
    try:
        supabase = get_supabase()
        now_iso = datetime.now(timezone.utc).isoformat()

        supabase.table("customers").update({
            "sms_consent":     False,
            "sms_consent_at":  now_iso,
            "sms_consent_src": "sms_stop",
        }).eq("client_id", client_id).eq("customer_phone", customer_phone).execute()

        print(f"[{_ts()}] INFO db_consent: Consent revoked for {customer_phone}")
        return True

    except Exception as e:
        print(f"[{_ts()}] ERROR db_consent: revoke_consent failed for {customer_phone} — {e}")
        return False
