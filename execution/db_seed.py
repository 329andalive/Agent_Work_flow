"""
db_seed.py — Insert the test client record

Inserts Jeremy Holt / Holt Sewer & Drain as the first client.
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
# Test client data — Jeremy Holt, Holt Sewer & Drain
# ---------------------------------------------------------------------------
TEST_CLIENT = {
    "business_name":  "Holt Sewer & Drain",
    "owner_name":     "Jeremy Holt",
    "phone":          "+12074190986",
    "service_area":   "Rural Maine",
    "trade_vertical": "sewer_drain",
    "active":         True,
    "personality": (
        "I am Jeremy Holt, owner of Holt Sewer and Drain serving rural Maine. "
        "I have been in the trades my whole life. I talk straight, I price fair, "
        "and I show up when I say I will. My customers are mostly farmers, camp owners, "
        "and rural homeowners who have been burned by contractors before. I earn their "
        "trust by being straight with them. My estimates are detailed and honest. "
        "I do not use fancy words. I say what the job is, what it costs, and when I can do it.\n\n"
        "Hourly rate: $125/hr\n"
        "Overtime (after 8hrs or weekends): $175/hr\n"
        "Minimum charge: $150\n"
        "I do not charge travel in my local area.\n"
        "Standard payment terms: due on receipt for residential, net 15 for commercial accounts.\n"
        "Payment methods accepted: check, cash, or Venmo @HoltSewer."
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
            supabase.table("clients").update(
                {"personality": TEST_CLIENT["personality"]}
            ).eq("id", client_id).execute()
            print(f"[{timestamp()}] INFO db_seed: Personality updated with rate fields.")
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
