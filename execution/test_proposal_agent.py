"""
test_proposal_agent.py — Dry-run test for the proposal agent

Simulates an inbound SMS from a customer without sending a real text.
Runs the full agent flow and prints every step so you can verify it works.

Set SEND_REAL_SMS = True to actually send the proposal via Telnyx.

Run:
    python execution/test_proposal_agent.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Test configuration — edit these to change what gets simulated
# ---------------------------------------------------------------------------
SEND_REAL_SMS = False   # ← Set to True to send a real SMS via Telnyx

test_client_phone   = "+15555550200"   # Test Owner's Telnyx number (the client)
test_customer_phone = "+12075550100"   # Fake customer number
test_message        = (
    "hey need my tank pumped been about 3 years "
    "3 bedroom place on route 9 in Bangor"
)

# ---------------------------------------------------------------------------
# Monkey-patch sms_send.send_sms to intercept outbound messages
# when SEND_REAL_SMS is False
# ---------------------------------------------------------------------------
if not SEND_REAL_SMS:
    import execution.sms_send as sms_send_module

    _captured_sms = []  # Holds intercepted outbound messages

    def _fake_send_sms(to_number, message_body, from_number=None):
        _captured_sms.append({"to": to_number, "body": message_body})
        print(f"\n{'='*55}")
        print("  [DRY RUN] SMS would be sent:")
        print(f"  To: {to_number}")
        print(f"  ---")
        print(f"  {message_body}")
        print(f"{'='*55}\n")
        return {"success": True, "message_id": "dry-run-id", "error": None}

    sms_send_module.send_sms = _fake_send_sms
    print("[DRY RUN MODE] No real SMS will be sent.\n")
else:
    print("[LIVE MODE] A real SMS will be sent via Telnyx.\n")


from datetime import datetime

def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main():
    print("=" * 55)
    print("  Proposal Agent — End-to-End Test")
    print("=" * 55)
    print(f"\nScenario:")
    print(f"  Client phone:    {test_client_phone}")
    print(f"  Customer phone:  {test_customer_phone}")
    print(f"  Message:         {test_message}")
    print()

    # ------------------------------------------------------------------
    # Step 1: Confirm the client is in Supabase
    # ------------------------------------------------------------------
    print(f"[{timestamp()}] Step 1: Loading client from Supabase...")
    from execution.db_client import get_client_by_phone
    client = get_client_by_phone(test_client_phone)
    if not client:
        print(f"\n  ✗ Client not found for {test_client_phone}")
        print("  Run: python execution/db_test.py  (seeds the Test Owner record)")
        sys.exit(1)

    print(f"  ✓ Client: {client['business_name']} | owner: {client['owner_name']}")
    print(f"  ✓ Personality loaded ({len(client.get('personality',''))} chars)\n")

    # ------------------------------------------------------------------
    # Step 2: Show what the agent will parse from the message
    # ------------------------------------------------------------------
    print(f"[{timestamp()}] Step 2: Parsing raw input...")
    from execution.proposal_agent import detect_job_type, extract_customer_name, extract_address
    job_type         = detect_job_type(test_message)
    customer_name    = extract_customer_name(test_message)
    customer_address = extract_address(test_message)
    print(f"  Job type:    {job_type}")
    print(f"  Customer:    {customer_name}")
    print(f"  Address:     '{customer_address}'\n")

    # ------------------------------------------------------------------
    # Step 3: Show the prompts that will be sent to Claude
    # ------------------------------------------------------------------
    print(f"[{timestamp()}] Step 3: Building Claude prompts...")
    from execution.proposal_agent import build_system_prompt, build_user_prompt
    system_prompt = build_system_prompt(client)
    user_prompt   = build_user_prompt(customer_name, customer_address, job_type, test_message, client["business_name"])

    print(f"\n  --- SYSTEM PROMPT ({len(system_prompt)} chars) ---")
    print(f"  {system_prompt[:300]}...")
    print(f"\n  --- USER PROMPT ({len(user_prompt)} chars) ---")
    print(f"  {user_prompt}")
    print()

    # ------------------------------------------------------------------
    # Step 4: Run the full agent
    # ------------------------------------------------------------------
    print(f"[{timestamp()}] Step 4: Running proposal_agent.run()...")
    print("-" * 55)
    from execution.proposal_agent import run
    proposal_text = run(
        client_phone=test_client_phone,
        customer_phone=test_customer_phone,
        raw_input=test_message,
    )
    print("-" * 55)

    # ------------------------------------------------------------------
    # Step 5: Final summary
    # ------------------------------------------------------------------
    print()
    if proposal_text:
        print("=" * 55)
        print("  ✓ PROPOSAL AGENT TEST PASSED")
        print("=" * 55)
        print(f"\nGenerated proposal ({len(proposal_text)} chars):\n")
        print(proposal_text)
        print()
        print("Supabase checks — open your Supabase dashboard and verify:")
        print("  1. clients table      → Test Owner record exists")
        print("  2. customers table    → new row for +12075550100")
        print("  3. jobs table         → new row, status='estimated'")
        print("  4. proposals table    → new row with proposal_text")
        print("  5. messages table     → 1 inbound + 1 outbound row")
        print("  6. follow_ups table   → new row, scheduled 3 days out")
    else:
        print("=" * 55)
        print("  ✗ PROPOSAL AGENT TEST FAILED")
        print("  proposal_text was None — check logs above for errors")
        print("=" * 55)
        sys.exit(1)


if __name__ == "__main__":
    main()
