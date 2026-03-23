"""
token_generator.py — Generates unique 8-char alphanumeric tokens for proposal/invoice links

Creates a short, URL-safe token and saves it to the invoice_links table in Supabase.
Tokens expire after 72 hours. Handles collisions by regenerating if a token already exists.

Usage:
    from execution.token_generator import generate_token
    token = generate_token(job_id="abc-123", client_phone="+12074190986", link_type="proposal")
    # → "a3Kf9mZx"
"""

import os
import sys
import string
import secrets
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase

# Token configuration
TOKEN_LENGTH = 8
TOKEN_CHARS = string.ascii_letters + string.digits  # a-z, A-Z, 0-9
EXPIRY_HOURS = 72
MAX_COLLISION_RETRIES = 5


def _generate_random_token() -> str:
    """Generate a cryptographically random 8-character alphanumeric token."""
    return "".join(secrets.choice(TOKEN_CHARS) for _ in range(TOKEN_LENGTH))


def generate_token(job_id: str, client_phone: str, link_type: str) -> str | None:
    """
    Generate a unique token and save it to the invoice_links table.

    Args:
        job_id:       The job this link is associated with
        client_phone: The business owner's phone (tenant identifier)
        link_type:    "proposal" or "invoice"

    Returns:
        The 8-character token string, or None on failure.
    """
    if link_type not in ("proposal", "invoice"):
        print(f"[token_generator] ERROR: Invalid link_type '{link_type}' — must be 'proposal' or 'invoice'")
        return None

    supabase = get_supabase()
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=EXPIRY_HOURS)).isoformat()

    for attempt in range(MAX_COLLISION_RETRIES):
        token = _generate_random_token()

        # Check if token already exists (collision check)
        existing = (
            supabase.table("invoice_links")
            .select("id")
            .eq("token", token)
            .execute()
        )

        if existing.data:
            print(f"[token_generator] WARN: Token collision on attempt {attempt + 1} — regenerating")
            continue

        # Insert the new token record
        try:
            result = supabase.table("invoice_links").insert({
                "token": token,
                "job_id": job_id,
                "client_phone": client_phone,
                "type": link_type,
                "expires_at": expires_at,
            }).execute()

            if result.data:
                print(f"[token_generator] INFO: Token created → {token} (type={link_type}, expires={expires_at})")
                return token
            else:
                print(f"[token_generator] ERROR: Insert returned no data")
                return None

        except Exception as e:
            # If insert fails due to unique constraint, retry
            if "duplicate" in str(e).lower() or "unique" in str(e).lower():
                print(f"[token_generator] WARN: Duplicate on insert attempt {attempt + 1} — regenerating")
                continue
            print(f"[token_generator] ERROR: Insert failed — {e}")
            return None

    print(f"[token_generator] ERROR: Exhausted {MAX_COLLISION_RETRIES} retries — could not generate unique token")
    return None


def get_link_by_token(token: str) -> dict | None:
    """
    Look up a token record from the invoice_links table.

    Args:
        token: The 8-character token string

    Returns:
        The full row as a dict, or None if not found.
    """
    try:
        supabase = get_supabase()
        result = (
            supabase.table("invoice_links")
            .select("*")
            .eq("token", token)
            .execute()
        )
        if result.data:
            return result.data[0]
        return None
    except Exception as e:
        print(f"[token_generator] ERROR: Lookup failed for token={token} — {e}")
        return None


def mark_viewed(token: str) -> None:
    """Update the viewed_at timestamp for a token."""
    try:
        supabase = get_supabase()
        supabase.table("invoice_links").update({
            "viewed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("token", token).execute()
    except Exception as e:
        print(f"[token_generator] WARN: Failed to mark token as viewed — {e}")


def is_expired(link_record: dict) -> bool:
    """
    Check if a token link has expired.

    Args:
        link_record: A row from invoice_links table

    Returns:
        True if the link is expired, False if still valid.
    """
    if link_record.get("expired"):
        return True

    expires_at_str = link_record.get("expires_at")
    if not expires_at_str:
        return True

    try:
        # Parse ISO timestamp — handle both offset-aware and naive
        expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) >= expires_at
    except (ValueError, TypeError):
        return True


# ---------------------------------------------------------------------------
# Square payment link helpers
# Used when an invoice token needs a Square Payment Link attached.
# ---------------------------------------------------------------------------

def attach_payment_link(
    token: str,
    payment_link_url: str,
    square_order_id: str = None,
    square_payment_link_id: str = None,
) -> bool:
    """
    Attach a Square Payment Link URL to an existing invoice_links record.

    Args:
        token:                    The 8-char token already in invoice_links
        payment_link_url:         Square checkout URL (e.g. "https://square.link/u/xxx")
        square_order_id:          Square order ID for webhook matching
        square_payment_link_id:   Square payment link ID

    Returns:
        True on success, False on failure.
    """
    try:
        supabase = get_supabase()
        update = {"payment_link_url": payment_link_url}
        if square_order_id:
            update["square_order_id"] = square_order_id
        if square_payment_link_id:
            update["square_payment_link_id"] = square_payment_link_id

        supabase.table("invoice_links").update(update).eq("token", token).execute()
        print(f"[token_generator] INFO: Attached payment link to token={token}")
        return True
    except Exception as e:
        print(f"[token_generator] ERROR: attach_payment_link failed — {e}")
        return False


def get_link_by_square_order(order_id: str) -> dict | None:
    """
    Reverse lookup: find an invoice_links record by Square order_id.
    Used by the Square webhook handler to identify which invoice was paid.

    Args:
        order_id: The square_order_id stored when the payment link was created

    Returns:
        The invoice_links row as a dict, or None if not found.
    """
    try:
        supabase = get_supabase()
        result = (
            supabase.table("invoice_links")
            .select("*")
            .eq("square_order_id", order_id)
            .execute()
        )
        if result.data:
            return result.data[0]
        return None
    except Exception as e:
        print(f"[token_generator] ERROR: get_link_by_square_order failed — {e}")
        return None


def mark_invoice_paid(invoice_id: str, square_payment_id: str = None) -> bool:
    """
    Mark an invoice as paid in the invoices table.
    Called by the Square webhook handler when payment is confirmed.

    Uses a two-pass strategy: first attempts full update including
    square_payment_id audit column; if that fails (schema not yet
    migrated), retries with only status + paid_at so the payment
    is never silently dropped.

    Args:
        invoice_id: UUID of the invoice
        square_payment_id: Square payment ID for audit trail (optional)
    Returns:
        True on success, False on unrecoverable failure.
    """
    try:
        supabase = get_supabase()
        now = datetime.now(timezone.utc).isoformat()
        update = {"status": "paid", "paid_at": now}
        if square_payment_id:
            update["square_payment_id"] = square_payment_id
        supabase.table("invoices").update(update).eq("id", invoice_id).execute()
        print(f"[token_generator] INFO: Invoice {invoice_id} marked paid (square_payment_id={square_payment_id})")
        return True
    except Exception as e:
        # If full update failed and square_payment_id was in the payload,
        # retry with only the core fields — never drop a real payment.
        if square_payment_id and "square_payment_id" in str(e).lower():
            print(f"[token_generator] WARN: square_payment_id column missing — retrying without it")
            try:
                supabase = get_supabase()
                supabase.table("invoices").update({
                    "status": "paid",
                    "paid_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", invoice_id).execute()
                print(f"[token_generator] INFO: Invoice {invoice_id} marked paid (fallback — no square_payment_id)")
                return True
            except Exception as e2:
                print(f"[token_generator] ERROR: mark_invoice_paid fallback also failed — {e2}")
                return False
        print(f"[token_generator] ERROR: mark_invoice_paid failed — {e}")
        return False


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    print("Testing token generation...")
    test_token = generate_token(
        job_id="test-job-001",
        client_phone="+12074190986",
        link_type="proposal",
    )
    if test_token:
        print(f"Generated token: {test_token}")

        # Test lookup
        record = get_link_by_token(test_token)
        print(f"Lookup result: {record}")

        # Test expiry check
        print(f"Is expired: {is_expired(record)}")

        # Test mark viewed
        mark_viewed(test_token)
        record = get_link_by_token(test_token)
        print(f"After marking viewed: viewed_at={record.get('viewed_at')}")
    else:
        print("Token generation failed!")
