"""
db_seed.py — Insert the test client record

Inserts a test client.
Safe to run multiple times — skips insert if the phone number already exists.

Run:
    python execution/db_seed.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Test client data — loaded from env vars or defaults to generic placeholders.
# Real client data lives in Supabase only — never hardcode here.
# ---------------------------------------------------------------------------
TEST_CLIENT = {
    "business_name":  os.environ.get("TEST_BUSINESS_NAME", "Test Trades Co"),
    "owner_name":     os.environ.get("TEST_OWNER_NAME", "Test Owner"),
    "phone":          os.environ.get("TELNYX_PHONE_NUMBER", "+15555550200"),
    "owner_mobile":   os.environ.get("TEST_OWNER_MOBILE", "+15555550100"),
    "service_area":   "Service Area",
    "trade_vertical": "sewer_drain",
    "active":         True,
    "personality": (
        "I am a trades business owner. I talk straight, I price fair, "
        "and I show up when I say I will.\n\n"
        "Hourly rate: $125/hr\n"
        "Overtime (after 8hrs or weekends): $175/hr\n"
        "Minimum charge: $150\n"
        "Standard payment terms: due on receipt for residential, net 15 for commercial accounts.\n"
    ),
}


def seed():
    """
    Insert the test client if they don't already exist.
    Uses the phone number as the uniqueness check.
    """
    supabase = get_client()

    # Check if the client already exists
    try:
        existing = (
            supabase.table("clients")
            .select("id, business_name")
            .eq("phone", TEST_CLIENT["phone"])
            .single()
            .execute()
        )
        if existing.data:
            client_id = existing.data["id"]
            print(f"[{timestamp()}] INFO db_seed: Client exists → {existing.data['business_name']} (id={client_id})")
            # Update personality in case it has been extended (e.g. rates added)
            supabase.table("clients").update({
                "personality":  TEST_CLIENT["personality"],
                "owner_mobile": TEST_CLIENT["owner_mobile"],
            }).eq("id", client_id).execute()
            print(f"[{timestamp()}] INFO db_seed: Personality and owner_mobile updated.")
            return client_id
    except Exception:
        # .single() raises when no row found — that means we should insert
        pass

    # Insert the new client
    try:
        result = supabase.table("clients").insert(TEST_CLIENT).execute()
        client_id = result.data[0]["id"]
        print(f"[{timestamp()}] INFO db_seed: Inserted client → {TEST_CLIENT['business_name']} (id={client_id})")
        return client_id

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_seed: Insert failed — {e}")
        return None


if __name__ == "__main__":
    print(f"[{timestamp()}] Starting seed...")
    client_id = seed()
    if client_id:
        print(f"[{timestamp()}] Seed complete. client_id={client_id}")
    else:
        print(f"[{timestamp()}] Seed failed.")
        sys.exit(1)
