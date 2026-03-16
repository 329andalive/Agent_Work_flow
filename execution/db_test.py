"""
db_test.py — Confirm the Supabase data layer is working end-to-end

Checks:
  1. Connection to Supabase succeeds
  2. All 8 tables exist
  3. Seeds the client table if empty
  4. Fetches the Jeremy Holt client record
  5. Prints a clean summary

Run:
    python execution/db_test.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client
from execution.db_seed import seed
from execution.db_client import get_client_by_phone


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# All 8 tables that must exist
REQUIRED_TABLES = [
    "clients",
    "customers",
    "jobs",
    "proposals",
    "invoices",
    "messages",
    "follow_ups",
    "reviews",
]


def check_table_exists(supabase, table_name: str) -> bool:
    """
    Attempt a lightweight select on the table.
    If Supabase returns an error, the table doesn't exist.
    """
    try:
        supabase.table(table_name).select("id").limit(1).execute()
        return True
    except Exception as e:
        # Check for table-not-found type errors
        if "does not exist" in str(e) or "relation" in str(e):
            return False
        # Other errors (permissions, etc.) — table may exist but something else is wrong
        print(f"[{timestamp()}] WARN: Unexpected error checking table '{table_name}': {e}")
        return False


def main():
    print()
    print("=" * 55)
    print("  Trades AI — Supabase Connection Test")
    print("=" * 55)

    # ------------------------------------------------------------------
    # Step 1: Connect
    # ------------------------------------------------------------------
    print(f"\n[{timestamp()}] Step 1: Connecting to Supabase...")
    try:
        supabase = get_client()
        print(f"[{timestamp()}] ✓ Connected")
    except Exception as e:
        print(f"[{timestamp()}] ✗ Connection failed: {e}")
        print("\n  Check SUPABASE_URL and SUPABASE_SERVICE_KEY in .env")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 2: Confirm all 8 tables exist
    # ------------------------------------------------------------------
    print(f"\n[{timestamp()}] Step 2: Checking tables...")
    all_good = True
    for table in REQUIRED_TABLES:
        exists = check_table_exists(supabase, table)
        status = "✓" if exists else "✗ MISSING"
        print(f"  {status}  {table}")
        if not exists:
            all_good = False

    if not all_good:
        print(f"\n[{timestamp()}] ✗ Missing tables detected.")
        print("  Run the SQL in directives/supabase_schema.sql in the Supabase SQL editor.")
        sys.exit(1)

    print(f"[{timestamp()}] ✓ All 8 tables exist")

    # ------------------------------------------------------------------
    # Step 3: Seed if clients table is empty
    # ------------------------------------------------------------------
    print(f"\n[{timestamp()}] Step 3: Checking seed data...")
    try:
        result = supabase.table("clients").select("id").limit(1).execute()
        if not result.data:
            print(f"[{timestamp()}] clients table is empty — running seed...")
            seed()
        else:
            print(f"[{timestamp()}] ✓ clients table has data — skipping seed")
    except Exception as e:
        print(f"[{timestamp()}] ERROR checking clients table: {e}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 4: Fetch the Jeremy Holt record
    # ------------------------------------------------------------------
    print(f"\n[{timestamp()}] Step 4: Fetching test client...")
    client = get_client_by_phone("+12074190986")

    if not client:
        print(f"[{timestamp()}] ✗ Could not find client for +12074190986")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 5: Print summary
    # ------------------------------------------------------------------
    print()
    print("=" * 55)
    print("  ✓ ALL CHECKS PASSED")
    print("=" * 55)
    print(f"  Business:      {client['business_name']}")
    print(f"  Owner:         {client['owner_name']}")
    print(f"  Phone:         {client['phone']}")
    print(f"  Service area:  {client['service_area']}")
    print(f"  Vertical:      {client['trade_vertical']}")
    print(f"  Client ID:     {client['id']}")
    print(f"  Personality:   {client['personality'][:60]}...")
    print("=" * 55)
    print()


if __name__ == "__main__":
    main()
