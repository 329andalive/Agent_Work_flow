"""
import_customers.py — Bulk import customers from a CSV file into Supabase

Usage:
    python scripts/import_customers.py scripts/test_customers.csv

CSV must have columns: First Name, Last Name, Phone Number, Address, City, State, Zip Code
Phone numbers are normalized to E.164 format (+1XXXXXXXXXX).
Duplicate phones (same client_id + customer_phone) are skipped.
"""

import os
import sys
import csv
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from execution.db_connection import get_client as get_supabase

# Set via env var or pass as CLI arg — never hardcode client IDs
CLIENT_ID = os.environ.get("CLIENT_ID", "")


def normalize_phone(raw: str) -> str:
    """Normalize phone to E.164 format."""
    digits = re.sub(r'\D', '', raw)
    if len(digits) == 10:
        return f"+1{digits}"
    elif len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return f"+{digits}" if digits else ""


def main(csv_path: str):
    if not os.path.exists(csv_path):
        print(f"ERROR: File not found: {csv_path}")
        sys.exit(1)

    sb = get_supabase()
    ok = 0
    skipped = 0
    errors = 0

    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            first = row.get('First Name', '').strip()
            last = row.get('Last Name', '').strip()
            phone_raw = row.get('Phone Number', '').strip()
            address = row.get('Address', '').strip()
            city = row.get('City', '').strip()
            state = row.get('State', '').strip()
            zipcode = row.get('Zip Code', '').strip()

            name = f"{first} {last}".strip()
            phone = normalize_phone(phone_raw)
            full_address = f"{address}, {city}, {state} {zipcode}".strip(', ')

            if not name or not phone:
                print(f"SKIP: missing name or phone — {row}")
                skipped += 1
                continue

            # Check for duplicate
            try:
                existing = sb.table("customers").select("id").eq("client_id", CLIENT_ID).eq("customer_phone", phone).execute()
                if existing.data:
                    print(f"SKIP: duplicate phone {phone} — {name}")
                    skipped += 1
                    continue
            except Exception as e:
                print(f"WARN: dupe check failed for {name} — {e}")

            # Insert
            try:
                sb.table("customers").insert({
                    "client_id": CLIENT_ID,
                    "customer_name": name,
                    "customer_phone": phone,
                    "customer_address": full_address,
                    "sms_consent": False,
                }).execute()
                print(f"OK: {name} — {phone} — {full_address}")
                ok += 1
            except Exception as e:
                print(f"ERROR: {name} — {e}")
                errors += 1

    print(f"\nDone: {ok} imported, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/import_customers.py <csv_file>")
        sys.exit(1)
    main(sys.argv[1])
