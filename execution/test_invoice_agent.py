"""
test_invoice_agent.py — Dry-run test for invoice_agent + job_cost_agent

Scenario:
  Jeremy just finished a baffle replacement for the Andersons.
  There was a prior proposal for 2 hours at $125/hr + $85 parts.
  Actual job took 3.5 hours and $95 in parts.

  This test:
    1. Seeds the test client (updates personality with rate fields)
    2. Ensures the test customer exists
    3. Creates a realistic "estimated" job with a proposal (simulates prior proposal_agent run)
    4. Runs invoice_agent.run() in dry-run mode (no real SMS)
    5. Prints every step in detail

Set SEND_REAL_SMS = True to actually send via Telnyx.

Run:
    python execution/test_invoice_agent.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Test configuration
# ---------------------------------------------------------------------------
SEND_REAL_SMS = False  # ← Set to True to send a real SMS via Telnyx

test_client_phone   = "+15555550200"   # Test Owner's Telnyx number
test_customer_phone = "+12075550100"   # Fake customer (Anderson)
test_message = (
    "done at the Anderson place, replaced baffle, "
    "took 3.5 hours, used a new baffle kit came to 95 in parts"
)

# ---------------------------------------------------------------------------
# Intercept outbound SMS when dry run
# ---------------------------------------------------------------------------
if not SEND_REAL_SMS:
    import execution.sms_send as sms_send_module

    _captured = []

    def _fake_send(to_number, message_body, from_number=None):
        _captured.append({"to": to_number, "body": message_body})
        print(f"\n{'='*60}")
        print("  [DRY RUN] SMS would be sent to owner:")
        print(f"  To: {to_number}")
        print("  " + "-"*56)
        for line in message_body.split("\n"):
            print(f"  {line}")
        print(f"{'='*60}\n")
        return {"success": True, "message_id": "dry-run-id", "error": None}

    sms_send_module.send_sms = _fake_send
    print("[DRY RUN MODE] No real SMS will be sent.\n")
else:
    print("[LIVE MODE] A real SMS will be sent via Telnyx.\n")


def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main():
    print("=" * 60)
    print("  Invoice Agent + Job Cost Agent — End-to-End Test")
    print("=" * 60)
    print(f"\nScenario:")
    print(f"  Client:    {test_client_phone} (Test Owner)")
    print(f"  Customer:  {test_customer_phone} (Anderson)")
    print(f"  Message:   {test_message}")
    print()

    # ------------------------------------------------------------------
    # Step 1: Seed / update the client record (adds rate fields to personality)
    # ------------------------------------------------------------------
    print(f"[{ts()}] Step 1: Seeding / updating client record...")
    from execution.db_seed import seed
    client_id = seed()
    if not client_id:
        print("  ✗ Seed failed — run: python execution/db_test.py")
        sys.exit(1)

    from execution.db_client import get_client_by_phone
    client = get_client_by_phone(test_client_phone)
    print(f"  ✓ Client: {client['business_name']}")

    from execution.invoice_agent import extract_hourly_rate, extract_payment_terms
    hourly_rate   = extract_hourly_rate(client["personality"])
    payment_terms = extract_payment_terms(client["personality"])
    print(f"  ✓ Hourly rate parsed from personality: ${hourly_rate}/hr")
    print(f"  ✓ Payment terms: {payment_terms}\n")

    # ------------------------------------------------------------------
    # Step 2: Ensure test customer exists
    # ------------------------------------------------------------------
    print(f"[{ts()}] Step 2: Setting up test customer...")
    from execution.db_customer import get_customer_by_phone, create_customer
    customer = get_customer_by_phone(client_id, test_customer_phone)
    if customer:
        customer_id = customer["id"]
        print(f"  ✓ Customer exists: {customer['customer_name']} (id={customer_id})")
    else:
        customer_id = create_customer(
            client_id=client_id,
            name="Mike Anderson",
            phone=test_customer_phone,
            address="Route 9, Bangor, ME",
        )
        print(f"  ✓ Created customer: Mike Anderson (id={customer_id})")
    print()

    # ------------------------------------------------------------------
    # Step 3: Create a realistic "estimated" job + proposal
    #         (simulates what proposal_agent would have already done)
    # ------------------------------------------------------------------
    print(f"[{ts()}] Step 3: Setting up prior job and proposal...")
    from execution.db_jobs import create_job, get_jobs_by_client
    from execution.db_proposals import save_proposal, update_proposal_status

    # Check if an estimated job already exists for this customer
    all_jobs = get_jobs_by_client(client_id)
    existing_job = next(
        (j for j in all_jobs if j.get("customer_id") == customer_id and j.get("status") in ("estimated", "new")),
        None
    )

    if existing_job:
        job_id = existing_job["id"]
        print(f"  ✓ Found existing open job (id={job_id}) status={existing_job['status']}")
    else:
        job_id = create_job(
            client_id=client_id,
            customer_id=customer_id,
            job_type="repair",
            raw_input="baffle replacement Route 9 Anderson place",
            job_description="Outlet baffle replacement — Anderson residence",
        )
        print(f"  ✓ Created job (id={job_id})")

        # Set the estimated hours so job cost comparison works
        from execution.db_connection import get_client as get_sb
        supabase = get_sb()
        supabase.table("jobs").update({
            "estimated_hours":  2.0,
            "estimated_amount": 335.00,  # 2hrs x $125 + $85 parts
            "status":           "estimated",
        }).eq("id", job_id).execute()

        # Save a prior proposal
        proposal_id = save_proposal(
            job_id=job_id,
            client_id=client_id,
            customer_id=customer_id,
            proposal_text=(
                "Mike, Jeremy here from Test Trades Co.\n\n"
                "For the baffle replacement at your place on Route 9:\n"
                "Labor: 2 hrs x $125 = $250.00\n"
                "Baffle and fittings: ~$85.00\n"
                "Estimate: $335.00\n\n"
                "I can get there Tuesday. Just confirm and I'll be there.\n\n"
                "Test Owner\nTest Trades Co\n207-419-0986"
            ),
            amount=335.00,
        )
        update_proposal_status(proposal_id, "sent")
        print(f"  ✓ Created prior proposal (id={proposal_id}) — est. $335 / 2hrs")
    print()

    # ------------------------------------------------------------------
    # Step 4: Show what the agent will parse
    # ------------------------------------------------------------------
    print(f"[{ts()}] Step 4: Parsing completion message...")
    from execution.invoice_agent import parse_hours, parse_materials
    parsed_hours = parse_hours(test_message)
    materials_desc, materials_cost = parse_materials(test_message)
    print(f"  Hours parsed:     {parsed_hours}")
    print(f"  Materials desc:   '{materials_desc}'")
    print(f"  Materials cost:   ${materials_cost}")

    labor_total   = round((parsed_hours or 0) * hourly_rate, 2)
    actual_amount = round(labor_total + materials_cost, 2)
    print(f"  Labor total:      {parsed_hours} x ${hourly_rate} = ${labor_total}")
    print(f"  Total invoiced:   ${actual_amount}")
    print()

    # ------------------------------------------------------------------
    # Step 5: Run the full invoice agent
    # ------------------------------------------------------------------
    print(f"[{ts()}] Step 5: Running invoice_agent.run()...")
    print("-" * 60)
    from execution.invoice_agent import run
    combined_sms = run(
        client_phone=test_client_phone,
        customer_phone=test_customer_phone,
        raw_input=test_message,
    )
    print("-" * 60)
    print()

    # ------------------------------------------------------------------
    # Step 6: Show job cost breakdown details
    # ------------------------------------------------------------------
    print(f"[{ts()}] Step 6: Job cost breakdown...")
    from execution.db_jobs import calculate_job_cost
    costing = calculate_job_cost(job_id)
    if costing:
        print(f"  Contract type:      {costing['contract_type']}")
        print(f"  Estimated hours:    {costing['estimated_hours']}")
        print(f"  Actual hours:       {costing['actual_hours']}")
        print(f"  Hour variance:      {costing['hour_variance']} hrs")
        print(f"  Estimated amount:   ${costing['estimated_amount']}")
        print(f"  Actual amount:      ${costing['actual_amount']}")
        print(f"  Labor cost:         ${costing['labor_cost']}")
        print(f"  Job margin:         ${costing['job_margin']}")
        print(f"  Result:             {costing['result'].upper()}")
        print(f"  Summary:            {costing['summary']}")
    else:
        print("  Could not calculate job cost — check logs above")
    print()

    # ------------------------------------------------------------------
    # Step 7: Final result
    # ------------------------------------------------------------------
    if combined_sms:
        print("=" * 60)
        print("  ✓ INVOICE AGENT TEST PASSED")
        print("=" * 60)
        print()
        print("Supabase tables to verify:")
        print(f"  jobs table       → id={job_id} | status=invoiced | actual_hours={parsed_hours}")
        print(f"  invoices table   → new row for job_id={job_id} | amount=${actual_amount}")
        print(f"  job_costs table  → new row | result={costing['result'] if costing else 'unknown'}")
        print(f"  messages table   → 1 inbound + 1 outbound row")
        print(f"  follow_ups table → new row | type=payment_chase | 7 days out")
    else:
        print("=" * 60)
        print("  ✗ INVOICE AGENT TEST FAILED — combined_sms was None")
        print("  Check logs above for the error.")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
