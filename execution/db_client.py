"""
db_client.py — Client table queries

A "client" is the business owner using the system (e.g. Jeremy Holt,
owner of Holt Sewer & Drain). Every inbound SMS gets matched to a
client by phone number — this is step 1 of every agent flow.

Usage:
    from execution.db_client import get_client_by_phone, get_personality
"""

import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _normalize_phone(phone: str) -> str:
    """Normalize any phone format to E.164 (+12075558806)."""
    if not phone:
        return phone
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 10:
        return f'+1{digits}'
    if len(digits) == 11 and digits.startswith('1'):
        return f'+{digits}'
    return f'+{digits}'

from execution.db_connection import get_client


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_client_by_phone(phone: str) -> dict | None:
    """
    Look up a client by their phone number.
    Called by sms_router.py on every inbound SMS to identify the business owner.

    Args:
        phone: E.164 phone number (e.g. "+12074190986")

    Returns:
        Full client record as a dict, or None if not found / on error.
    """
    try:
        supabase = get_client()
        result = (
            supabase.table("clients")
            .select("*")
            .eq("phone", _normalize_phone(phone))
            .eq("active", True)
            .single()
            .execute()
        )
        if result.data:
            print(f"[{timestamp()}] INFO db_client: Found client → {result.data['business_name']}")
        return result.data

    except Exception as e:
        # .single() raises if no row found — treat as not found, not a crash
        print(f"[{timestamp()}] INFO db_client: No client found for phone={phone} ({e})")
        return None


def get_personality(phone: str) -> str | None:
    """
    Return just the personality text for a client.
    Agents use this to speak in the owner's voice when generating messages.

    Args:
        phone: E.164 phone number

    Returns:
        Personality text string, or None if not found.
    """
    try:
        client = get_client_by_phone(phone)
        if client:
            return client.get("personality")
        return None

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_client: get_personality failed — {e}")
        return None


def list_all_clients() -> list:
    """
    Return all active client records.
    Useful for admin views and batch operations.

    Returns:
        List of client dicts, or empty list on error.
    """
    try:
        supabase = get_client()
        result = (
            supabase.table("clients")
            .select("*")
            .eq("active", True)
            .order("business_name")
            .execute()
        )
        print(f"[{timestamp()}] INFO db_client: list_all_clients → {len(result.data)} clients")
        return result.data or []

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_client: list_all_clients failed — {e}")
        return []
