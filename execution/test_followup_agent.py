"""
test_followup_agent.py — Dry-run tests for the Follow Up Agent

Three test scenarios:
  1. Scheduled follow-up (cron path) — simulates a pending follow-up being sent
  2. Proposal acceptance — customer texts "yes" back
  3. Loss report + why answer — owner reports a lost job, then answers why

All SMS is suppressed (SEND_REAL_SMS=False). DB calls use real Supabase.

Usage:
    python execution/test_followup_agent.py
    python execution/test_followup_agent.py --scenario 1
    python execution/test_followup_agent.py --scenario 2
    python execution/test_followup_agent.py --scenario 3
"""

import os
import sys
import argparse
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Suppress real SMS ──────────────────────────────────────────────────────────
import execution.sms_send as _sms_module
_sent_messages = []

def _fake_send(to_number, message_body, from_number=None):
    _sent_messages.append({
        "to": to_number,
        "from": from_number,
        "body": message_body,
    })
    print(f"  [FAKE SMS] To: {to_number}")
    print(f"  [FAKE SMS] Body: {message_body}")
    return {"success": True, "message_id": "fake-msg-id", "error": None}

_sms_module.send_sms = _fake_send
# ──────────────────────────────────────────────────────────────────────────────

from execution.db_client import get_client_by_phone
from execution.db_customer import get_customer_by_phone, create_customer
from execution.db_proposals import save_proposal, update_proposal_status
from execution.db_followups import schedule_followup
from execution.db_jobs import create_job
from execution.sms_router import route_message
from execution.followup_agent import run_scheduled_followups
from execution.reporting_agent import get_closing_rate_summary


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

JEREMY_PHONE   = "+12074190986"   # Jeremy's Telnyx number (must match db_seed.py)
CUSTOMER_PHONE = "+12075559999"   # Fake customer number
CUSTOMER_NAME  = "Test Customer"
CUSTOMER_ADDR  = "123 Test Lane, Bangor, ME"


def _get_jeremy(verbose=True) -> dict:
    client = get_client_by_phone(JEREMY_PHONE)
    if not client:
        print(f"ERROR: No client found for {JEREMY_PHONE}. Run db_seed.py first.")
        sys.exit(1)
    if verbose:
        print(f"  Client: {client['business_name']} (id={client['id'][:8]}...)")
    return client


def _get_or_create_customer(client_id: str) -> dict:
    customer = get_customer_by_phone(client_id, CUSTOMER_PHONE)
    if not customer:
        create_customer(client_id, CUSTOMER_NAME, CUSTOMER_PHONE, CUSTOMER_ADDR, None)
        customer = get_customer_by_phone(client_id, CUSTOMER_PHONE)
    print(f"  Customer: {customer['name']} (id={customer['id'][:8]}...)")
    return customer


def _create_test_proposal(client_id: str, customer_id: str) -> tuple[str, str]:
    """Create a job + proposal in sent status. Returns (job_id, proposal_id)."""
    job_id = create_job(
        client_id=client_id,
        customer_id=customer_id,
        job_type="septic",
        raw_input="Pump out the 1000 gallon tank at 123 Test Lane",
        job_description="Septic pump-out at 123 Test Lane",
    )
    proposal_id = save_proposal(
        job_id=job_id,
        client_id=client_id,
        customer_id=customer_id,
        proposal_text="Hi, here's your estimate for the septic pump-out: $350. Let me know if you want to schedule.",
        amount=350.00,
    )
    update_proposal_status(proposal_id, "sent")
    print(f"  Created job={job_id[:8]}... proposal={proposal_id[:8]}... (status=sent)")
    return job_id, proposal_id


# ---------------------------------------------------------------------------
# Scenario 1: Scheduled follow-up (simulates cron run)
# ---------------------------------------------------------------------------

def test_scheduled_followup():
    print("\n" + "="*60)
    print("SCENARIO 1: Scheduled follow-up (cron path)")
    print("="*60)

    client   = _get_jeremy()
    customer = _get_or_create_customer(client["id"])
    job_id, proposal_id = _create_test_proposal(client["id"], customer["id"])

    # Schedule a follow-up that's due right now (past due)
    due_now = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    fu_id = schedule_followup(
        client_id=client["id"],
        customer_id=customer["id"],
        job_id=job_id,
        proposal_id=proposal_id,
        followup_type="estimate_followup",
        scheduled_for=due_now,
    )
    print(f"  Scheduled follow-up id={fu_id[:8]}... due at: {due_now[:19]}")

    print("\n  Running run_scheduled_followups()...")
    count = run_scheduled_followups()

    print(f"\n  Processed: {count} follow-up(s)")
    print(f"  SMS sent: {len(_sent_messages)}")
    for msg in _sent_messages:
        print(f"    → {msg['body'][:100]}")

    _sent_messages.clear()
    print("\n  SCENARIO 1: PASS" if count > 0 else "\n  SCENARIO 1: NOTE — check logs above")


# ---------------------------------------------------------------------------
# Scenario 2: Customer accepts via SMS router
# ---------------------------------------------------------------------------

def test_proposal_acceptance():
    print("\n" + "="*60)
    print("SCENARIO 2: Customer accepts proposal via SMS")
    print("="*60)

    client   = _get_jeremy()
    customer = _get_or_create_customer(client["id"])
    _create_test_proposal(client["id"], customer["id"])

    # Simulate inbound SMS: customer says yes
    sms_data = {
        "from_number": CUSTOMER_PHONE,
        "to_number":   JEREMY_PHONE,
        "body":        "Sounds good, book it",
        "message_id":  "test-accept-001",
    }

    print(f"\n  Simulating inbound SMS: \"{sms_data['body']}\"")
    agent = route_message(sms_data)

    print(f"\n  Routed to: {agent}")
    print(f"  SMS sent: {len(_sent_messages)}")
    for msg in _sent_messages:
        target = "Owner" if msg["to"] == JEREMY_PHONE else "Customer"
        print(f"    → [{target}]: {msg['body'][:120]}")

    _sent_messages.clear()
    print("\n  SCENARIO 2: PASS" if agent in ("proposal_response",) else f"\n  SCENARIO 2: NOTE — routed to {agent}")


# ---------------------------------------------------------------------------
# Scenario 3: Owner reports loss + answers why
# ---------------------------------------------------------------------------

def test_loss_report_and_why():
    print("\n" + "="*60)
    print("SCENARIO 3: Owner reports loss, then answers why")
    print("="*60)

    client   = _get_jeremy()
    customer = _get_or_create_customer(client["id"])
    _create_test_proposal(client["id"], customer["id"])

    # Simulate owner texting in a loss report
    loss_sms = {
        "from_number": JEREMY_PHONE,   # owner's personal phone (from)
        "to_number":   JEREMY_PHONE,   # owner's Telnyx number (to)
        "body":        "Lost the Anderson place — they went with someone else",
        "message_id":  "test-loss-001",
    }

    print(f"\n  Step A — Owner reports loss: \"{loss_sms['body']}\"")
    agent_a = route_message(loss_sms)
    print(f"  Routed to: {agent_a}")
    print(f"  SMS sent: {len(_sent_messages)}")
    for msg in _sent_messages:
        print(f"    → {msg['body'][:120]}")
    _sent_messages.clear()

    # Simulate owner answering the "why" question (numeric shortcode)
    why_sms = {
        "from_number": JEREMY_PHONE,
        "to_number":   JEREMY_PHONE,
        "body":        "3",   # competition
        "message_id":  "test-why-001",
    }

    print(f"\n  Step B — Owner answers why: \"{why_sms['body']}\" (3 = competitor)")
    agent_b = route_message(why_sms)
    print(f"  Routed to: {agent_b}")
    print(f"  SMS sent: {len(_sent_messages)}")
    for msg in _sent_messages:
        print(f"    → {msg['body'][:120]}")
    _sent_messages.clear()

    # Show monthly outcomes
    print("\n  Fetching monthly outcomes...")
    summary = get_closing_rate_summary(client["id"])
    print(f"  Monthly summary: {summary}")

    print(f"\n  SCENARIO 3: PASS" if agent_b == "loss_reason" else f"\n  SCENARIO 3: NOTE — step B routed to {agent_b}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Test the follow-up agent")
    parser.add_argument("--scenario", type=int, choices=[1, 2, 3], help="Run a specific scenario (1, 2, or 3)")
    args = parser.parse_args()

    print("\nFollow-Up Agent — Dry-Run Tests")
    print("SMS suppressed. DB calls are REAL.")
    print("Make sure SUPABASE_URL and SUPABASE_SERVICE_KEY are set in .env\n")

    if args.scenario == 1 or not args.scenario:
        test_scheduled_followup()

    if args.scenario == 2 or not args.scenario:
        test_proposal_acceptance()

    if args.scenario == 3 or not args.scenario:
        test_loss_report_and_why()

    print("\n" + "="*60)
    print("All scenarios complete.")
    print("="*60)


if __name__ == "__main__":
    main()
