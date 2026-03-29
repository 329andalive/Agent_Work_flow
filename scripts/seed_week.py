"""
seed_week.py — Insert 40 test jobs (8/day, Mon-Fri) for B&B Septic.

One-time seed script for testing dispatch, scheduling, and invoice flows
against a realistic week of sewer & drain work.

Usage:
    python scripts/seed_week.py            # insert into Supabase
    python scripts/seed_week.py --dry-run  # preview without writing
"""

import os
import sys
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Project root on path so we can import execution modules
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# ---------------------------------------------------------------------------
# Constants — B&B Septic
# ---------------------------------------------------------------------------
CLIENT_ID = "8aafcd73-b41c-4f1a-bd01-3e7955798367"
CLIENT_PHONE = "+12074190986"

CUSTOMERS = [
    {"id": "2bcbe487-3447-488b-87cc-b75ae9b9dd21", "name": "Arthur Crockett",   "address": "310 Northport Avenue, Belfast, ME"},
    {"id": "daaf75d5-764d-4bc3-81ac-e7788c908591", "name": "Beverly Whitaker",  "address": "8 Fogg Road, Brooks, ME"},
    {"id": "bb27b047-ad3b-4b0c-bb30-7c5e38d2644f", "name": "Brenda Elwell",    "address": "66 Main Street, Lincolnville, ME"},
    {"id": "47c17d0b-32ff-4d91-9e4a-e3c3715af7e8", "name": "Carol Tweedie",    "address": "204 Monroe Road, Monroe, ME"},
    {"id": "7fbba1f0-c341-4a83-b780-f4b4c585d405", "name": "Cheryl Overlock",  "address": "21 Liberty Road, Morrill, ME"},
    {"id": "ecb2fc02-a9ef-40bc-8abd-dcc4435fda19", "name": "Dale Wentworth",   "address": "227 Route 1, Searsport, ME"},
    {"id": "be7ad6c7-517d-4341-aae6-87f52446aab6", "name": "Dennis Knowlton",  "address": "38 Halldale Road, Montville, ME"},
    {"id": "89fad4e0-840d-45df-bad8-5b9aac86a7b4", "name": "Donna Littlefield","address": "9 Church Street, Stockton Springs, ME"},
    {"id": "0e210c7e-6732-4aec-acc5-06f1caee4288", "name": "Gail Patterson",   "address": "29 Palermo Center Road, Palermo, ME"},
    {"id": "5f1cc9d4-5de6-48b6-8958-29dca62cc5cf", "name": "Glenn Pendleton",  "address": "12 Harbor View Lane, Islesboro, ME"},
    {"id": "468b4862-4d4d-490a-a421-5f90c10790c1", "name": "Heather Moody",    "address": "77 Cook Road, Thorndike, ME"},
    {"id": "7117419f-1a83-4f6d-b6ac-80c22fce5f28", "name": "Howard Bryant",    "address": "142 Youngtown Road, Lincolnville, ME"},
    {"id": "b453bd21-956c-4c9c-8c7f-bebd47dec615", "name": "Janet Sprague",    "address": "91 Lebanon Road, Winterport, ME"},
    {"id": "2d0ef957-bc70-4695-9d7e-a9342562b484", "name": "Kevin Peavey",     "address": "31 Pond Road, Brooks, ME"},
    {"id": "46d71bca-1786-46fa-8cab-2165756a562e", "name": "Linda Staples",    "address": "53 School Street, Unity, ME"},
    {"id": "a4e8a25a-f54c-4df7-8b13-85ef45502ecf", "name": "Norman Harriman",  "address": "116 Loggin Road, Frankfort, ME"},
    {"id": "5b357137-a9fd-4646-999c-94412be037c2", "name": "Philip Robbins",   "address": "45 Mortland Road, Searsport, ME"},
    {"id": "3d22abea-3ff9-43e2-9e6a-57f914d5133e", "name": "Roberta Cross",    "address": "120 Depot Street, Unity, ME"},
    {"id": "83909b5d-85d8-4af6-a99d-f4d0988797c8", "name": "Russell Hamlin",   "address": "402 Ridge Road, Freedom, ME"},
    {"id": "ef2fe217-eda4-4956-8f93-9604df457afa", "name": "Sharon Nickerson", "address": "85 Stream Road, Winterport, ME"},
    {"id": "82104ec6-2047-458e-80a2-e0422a6ede08", "name": "Tammy Richards",   "address": "33 Cape Jellison Road, Stockton Springs, ME"},
    {"id": "3090e7a9-f48d-4c1e-9e87-04c9fcd0ab2d", "name": "Travis Seekins",   "address": "18 Bartlett Hill Road, Troy, ME"},
    {"id": "40d78963-c098-4511-8f04-f1e17d066db3", "name": "Wayne Clement",    "address": "7 Pearl Street, Belfast, ME"},
]

JOB_TEMPLATES = [
    # Monday — 8 jobs
    {"day": 0, "slot": 0, "job_type": "pump",    "description": "Septic pump-out — 1,000 gal tank",           "amount": 325.00, "customer_idx": 0},
    {"day": 0, "slot": 1, "job_type": "pump",    "description": "Septic pump-out — 1,500 gal tank",           "amount": 375.00, "customer_idx": 1},
    {"day": 0, "slot": 2, "job_type": "inspect", "description": "Tank inspection — visual check",              "amount": 175.00, "customer_idx": 2},
    {"day": 0, "slot": 3, "job_type": "repair",  "description": "Outlet baffle replacement",                   "amount": 225.00, "customer_idx": 3},
    {"day": 0, "slot": 4, "job_type": "pump",    "description": "Septic pump-out — 1,000 gal tank",           "amount": 300.00, "customer_idx": 4},
    {"day": 0, "slot": 5, "job_type": "locate",  "description": "Tank locate and mark — no riser",            "amount": 175.00, "customer_idx": 5},
    {"day": 0, "slot": 6, "job_type": "pump",    "description": "Septic pump-out — 2,000 gal tank",           "amount": 450.00, "customer_idx": 6},
    {"day": 0, "slot": 7, "job_type": "camera",  "description": "Camera inspection — main line",              "amount": 275.00, "customer_idx": 7},
    # Tuesday — 8 jobs
    {"day": 1, "slot": 0, "job_type": "pump",    "description": "Septic pump-out — 1,000 gal tank",           "amount": 325.00, "customer_idx": 8},
    {"day": 1, "slot": 1, "job_type": "repair",  "description": "Inlet baffle replacement",                    "amount": 200.00, "customer_idx": 9},
    {"day": 1, "slot": 2, "job_type": "pump",    "description": "Septic pump-out — 1,500 gal tank",           "amount": 400.00, "customer_idx": 10},
    {"day": 1, "slot": 3, "job_type": "jetting", "description": "Hydro jetting — main line blockage",         "amount": 350.00, "customer_idx": 11},
    {"day": 1, "slot": 4, "job_type": "pump",    "description": "Septic pump-out — 1,000 gal tank",           "amount": 300.00, "customer_idx": 12},
    {"day": 1, "slot": 5, "job_type": "inspect", "description": "Full inspection with written report",         "amount": 375.00, "customer_idx": 13},
    {"day": 1, "slot": 6, "job_type": "repair",  "description": "Riser installation — access improvement",    "amount": 250.00, "customer_idx": 14},
    {"day": 1, "slot": 7, "job_type": "pump",    "description": "Septic pump-out — 1,000 gal tank",           "amount": 325.00, "customer_idx": 15},
    # Wednesday — 8 jobs
    {"day": 2, "slot": 0, "job_type": "pump",    "description": "Septic pump-out — 1,000 gal tank",           "amount": 325.00, "customer_idx": 16},
    {"day": 2, "slot": 1, "job_type": "camera",  "description": "Camera inspection — suspected root intrusion","amount": 300.00, "customer_idx": 17},
    {"day": 2, "slot": 2, "job_type": "pump",    "description": "Septic pump-out — 1,500 gal tank",           "amount": 375.00, "customer_idx": 18},
    {"day": 2, "slot": 3, "job_type": "repair",  "description": "Outlet baffle replacement + riser install",  "amount": 425.00, "customer_idx": 19},
    {"day": 2, "slot": 4, "job_type": "pump",    "description": "Septic pump-out — 1,000 gal tank",           "amount": 300.00, "customer_idx": 20},
    {"day": 2, "slot": 5, "job_type": "locate",  "description": "Tank locate — new property owner",           "amount": 175.00, "customer_idx": 21},
    {"day": 2, "slot": 6, "job_type": "pump",    "description": "Septic pump-out — 2,000 gal tank",           "amount": 450.00, "customer_idx": 22},
    {"day": 2, "slot": 7, "job_type": "inspect", "description": "Pre-sale inspection — visual + report",      "amount": 400.00, "customer_idx": 0},
    # Thursday — 8 jobs
    {"day": 3, "slot": 0, "job_type": "pump",    "description": "Septic pump-out — 1,000 gal tank",           "amount": 325.00, "customer_idx": 1},
    {"day": 3, "slot": 1, "job_type": "jetting", "description": "Hydro jetting — grease buildup",             "amount": 325.00, "customer_idx": 2},
    {"day": 3, "slot": 2, "job_type": "pump",    "description": "Septic pump-out — 1,500 gal tank",           "amount": 400.00, "customer_idx": 3},
    {"day": 3, "slot": 3, "job_type": "repair",  "description": "Baffle replacement — outlet side",           "amount": 200.00, "customer_idx": 4},
    {"day": 3, "slot": 4, "job_type": "pump",    "description": "Septic pump-out — 1,000 gal tank",           "amount": 300.00, "customer_idx": 5},
    {"day": 3, "slot": 5, "job_type": "camera",  "description": "Camera — locate blockage before jetting",    "amount": 250.00, "customer_idx": 6},
    {"day": 3, "slot": 6, "job_type": "pump",    "description": "Septic pump-out — 1,000 gal tank",           "amount": 325.00, "customer_idx": 7},
    {"day": 3, "slot": 7, "job_type": "inspect", "description": "Tank inspection — homeowner concern",         "amount": 175.00, "customer_idx": 8},
    # Friday — 8 jobs
    {"day": 4, "slot": 0, "job_type": "pump",    "description": "Septic pump-out — 1,000 gal tank",           "amount": 325.00, "customer_idx": 9},
    {"day": 4, "slot": 1, "job_type": "emergency","description": "Emergency pump-out — system backing up",    "amount": 550.00, "customer_idx": 10},
    {"day": 4, "slot": 2, "job_type": "pump",    "description": "Septic pump-out — 1,500 gal tank",           "amount": 375.00, "customer_idx": 11},
    {"day": 4, "slot": 3, "job_type": "repair",  "description": "Inlet and outlet baffle replacement",        "amount": 375.00, "customer_idx": 12},
    {"day": 4, "slot": 4, "job_type": "pump",    "description": "Septic pump-out — 1,000 gal tank",           "amount": 300.00, "customer_idx": 13},
    {"day": 4, "slot": 5, "job_type": "locate",  "description": "Tank locate and mark — new install prep",    "amount": 200.00, "customer_idx": 14},
    {"day": 4, "slot": 6, "job_type": "pump",    "description": "Septic pump-out — 2,000 gal tank",           "amount": 450.00, "customer_idx": 15},
    {"day": 4, "slot": 7, "job_type": "camera",  "description": "Camera inspection — post-repair verify",     "amount": 250.00, "customer_idx": 16},
]

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def next_monday(from_date: date) -> date:
    """Return the date of next Monday from the given date."""
    days_ahead = 0 - from_date.weekday()  # 0 = Monday
    if days_ahead <= 0:
        days_ahead += 7
    return from_date + timedelta(days=days_ahead)


def main():
    dry_run = "--dry-run" in sys.argv

    monday = next_monday(date.today())
    total_amount = sum(j["amount"] for j in JOB_TEMPLATES)

    print(f"{'DRY RUN — ' if dry_run else ''}Seeding 40 jobs for week of {monday.isoformat()}")
    print(f"Total estimated revenue: ${total_amount:,.2f}")
    print("-" * 60)

    if not dry_run:
        from supabase import create_client
        sb = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )

    inserted = 0
    failed = 0

    for job in JOB_TEMPLATES:
        customer = CUSTOMERS[job["customer_idx"]]
        scheduled_date = monday + timedelta(days=job["day"])
        day_name = DAY_NAMES[job["day"]]

        record = {
            "client_id": CLIENT_ID,
            "client_phone": CLIENT_PHONE,
            "customer_id": customer["id"],
            "job_type": job["job_type"],
            "job_description": job["description"],
            "job_address": customer["address"],
            "status": "scheduled",
            "scheduled_date": scheduled_date.isoformat(),
            "estimated_amount": job["amount"],
            "agent_used": "seed_script",
            "raw_input": f"Seeded: {job['description']} — {customer['name']}",
        }

        label = (
            f"{day_name} Slot {job['slot']}: "
            f"{customer['name']} — {job['description']} "
            f"@ ${job['amount']:.2f}"
        )

        if dry_run:
            print(f"  [DRY] {label}")
            inserted += 1
            continue

        try:
            sb.table("jobs").insert(record).execute()
            print(f"  OK  {label}")
            inserted += 1
        except Exception as e:
            print(f"  FAIL {label} — {e}")
            failed += 1

    print("-" * 60)
    if dry_run:
        print(f"DRY RUN complete. {inserted} jobs would be inserted across 5 days.")
    else:
        print(f"Seeded {inserted} jobs across 5 days. Week of {monday.isoformat()}")
        if failed:
            print(f"  {failed} inserts failed — check errors above.")


if __name__ == "__main__":
    main()
