"""
dedup_customers.py — Find and remove duplicate customer rows in Supabase.

Duplicates are identified by (client_id, customer_phone). The oldest row
(earliest created_at) is kept; newer duplicates are deleted.

Usage:
    python scripts/dedup_customers.py --dry-run     # Show what would be deleted
    python scripts/dedup_customers.py               # Actually delete duplicates
"""

import os
import sys
import argparse
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from execution.db_connection import get_client


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run(dry_run=True):
    supabase = get_client()

    print(f"[{timestamp()}] Fetching all customers...")
    result = supabase.table("customers").select(
        "id, client_id, customer_name, customer_phone, created_at"
    ).order("created_at").execute()

    rows = result.data or []
    print(f"[{timestamp()}] Total customer rows: {len(rows)}")

    # Group by (client_id, customer_phone)
    groups = defaultdict(list)
    for row in rows:
        phone = row.get("customer_phone") or ""
        if not phone:
            continue
        key = (row["client_id"], phone)
        groups[key].append(row)

    # Find groups with more than one row
    to_delete = []
    for key, dupes in groups.items():
        if len(dupes) <= 1:
            continue
        # Keep the first (oldest by created_at, already sorted)
        keep = dupes[0]
        for dup in dupes[1:]:
            to_delete.append(dup)

    if not to_delete:
        print(f"[{timestamp()}] No duplicates found.")
        return

    print(f"[{timestamp()}] Found {len(to_delete)} duplicate rows to remove:")
    print(f"{'':>4}{'ID':>40}  {'Name':<25} {'Phone':<16} {'Client ID':<38}")
    print(f"{'':>4}{'-'*40}  {'-'*25} {'-'*16} {'-'*38}")
    for d in to_delete:
        print(f"{'DEL':>4} {d['id']:>40}  {(d.get('customer_name') or '—'):<25} {(d.get('customer_phone') or '—'):<16} {d['client_id']:<38}")

    if dry_run:
        print(f"\n[{timestamp()}] DRY RUN — no rows deleted. Run without --dry-run to delete.")
        return

    # Delete duplicates
    deleted = 0
    for d in to_delete:
        try:
            supabase.table("customers").delete().eq("id", d["id"]).execute()
            deleted += 1
        except Exception as e:
            print(f"[{timestamp()}] ERROR deleting {d['id']}: {e}")

    print(f"\n[{timestamp()}] Deleted {deleted}/{len(to_delete)} duplicate rows.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deduplicate customer rows in Supabase")
    parser.add_argument("--dry-run", action="store_true", help="Show duplicates without deleting")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
