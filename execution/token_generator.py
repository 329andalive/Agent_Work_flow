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
