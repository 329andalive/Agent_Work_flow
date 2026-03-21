"""
proposal_agent.py — Generates professional septic service proposals via SMS

Flow:
  1. Load client record from Supabase by client_phone
  2. Parse raw_input for customer name, address, job type, and details
  3. Look up customer by phone — create new record if first contact
  4. Create a new job record (status: new)
  5. Build a Claude prompt using the client's Personality Layer
  6. Call Claude via call_claude.py → get proposal text
  7. Save proposal to Supabase
  8. Send proposal back via SMS to client_phone
  9. Log both inbound and outbound messages
  10. Schedule a 3-day follow-up
  11. Update job status to "estimated"

Usage:
    from execution.proposal_agent import run
    result = run(client_phone="+12074190986",
                 customer_phone="+12075550100",
                 raw_input="need tank pumped 3 bedroom route 9")
"""

import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_client import get_client_by_phone
from execution.db_agent_activity import log_activity
from execution.db_customer import get_customer_by_phone, create_customer
from execution.db_jobs import create_job, update_job_status
from execution.db_proposals import save_proposal, update_proposal_status
from execution.db_messages import log_message
from execution.db_followups import schedule_followup
from execution.call_claude import call_claude
from execution.sms_send import send_sms


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Job type keyword detection
# Scans the raw message for job type signals. Returns the most specific
# match — emergency overrides everything, pump/inspect/repair as appropriate.
# ---------------------------------------------------------------------------
JOB_TYPE_KEYWORDS = {
    "emergency": ["emergency", "backup", "overflow", "flooding", "smell", "alarm", "urgent", "asap"],
    "repair":    ["repair", "fix", "broken", "cracked", "collapsed", "failing", "failed",
                  "replacement", "replace", "baffle", "install", "installation", "new system"],
    "locate":    ["locate", "find", "where is", "mark", "lost lid", "lost cover"],
    "inspect":   ["inspect", "inspection", "check", "evaluate", "assessment", "look at"],
    "pump":      ["pump", "pumped", "pumping", "empty", "emptied", "full", "due", "years"],
}

DEFAULT_JOB_TYPE = "pump"


def detect_job_type(text: str) -> str:
    """Scan message for job type keywords. Emergency overrides all others."""
    text_lower = text.lower()
    for job_type, keywords in JOB_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return job_type
    return DEFAULT_JOB_TYPE


def extract_customer_name(text: str) -> str:
    """
    Try to pull a customer name from the message.

    Handles two patterns:
      1. Trigger phrases: "for Mike", "customer is Sarah", "name is John"
      2. Leading name: "CAROL Duggan needs a pump out at..."
         — owner names the customer first, then describes the job
    Returns "Customer" if nothing found — agents can ask later.
    """
    import re

    text_lower = text.lower()

    # Pattern 1 — trigger phrases
    name_triggers = ["for ", "customer is ", "name is ", "my name is ", "it's ", "its "]
    for trigger in name_triggers:
        idx = text_lower.find(trigger)
        if idx != -1:
            after = text[idx + len(trigger):].strip()
            name = after.split()[0].strip(".,!?").title()
            # Reject prices ($275), numbers, and single chars — not a name
            if len(name) > 1 and name[0].isalpha():
                return name

    # Pattern 2 — "FirstName [LastName] needs/wants/has/is ..."
    # Handles all-caps names like "CAROL Duggan"
    leading = re.match(
        r"^([A-Za-z]+(?:\s+[A-Za-z]+)?)\s+(needs|wants|has|is\s+requesting|called|requesting)\b",
        text.strip(),
        re.IGNORECASE,
    )
    if leading:
        candidate = leading.group(1).strip()
        # Reject single common words that aren't names
        if candidate.lower() not in {"i", "we", "he", "she", "they", "it", "customer", "owner"}:
            return candidate.title()

    return "Customer"


def extract_address(text: str) -> str:
    """
    Try to pull a location or address from the message.
    Looks for route numbers, road names, town names.
    Returns empty string if nothing found.
    """
    import re
    # Match "route X", "rt X", "road", "street", "lane", "drive", "way"
    patterns = [
        r"route\s+\d+",
        r"rt\.?\s+\d+",
        r"\d+\s+\w+\s+(road|rd|street|st|lane|ln|drive|dr|way|ave|avenue)",
        r"on\s+(.{3,40}?)\s+(in|near|by|at)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0).strip()

    # Look for "in [Town]" or "near [Town]"
    location_match = re.search(r"\b(in|near|at)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\b", text)
    if location_match:
        return location_match.group(0)

    return ""


def build_system_prompt(client: dict) -> str:
    """
    Build the system prompt using the client's Personality Layer.
    This is what makes every proposal sound like the owner wrote it.
    """
    return (
        f"You are the AI back office assistant for {client['business_name']}, "
        f"owned by {client['owner_name']}.\n"
        f"Read this Personality Layer completely before doing anything. "
        f"This is who you are writing for:\n\n"
        f"{client['personality']}\n\n"
        f"CRITICAL RULES:\n"
        f"Every word must sound like {client['owner_name']} wrote it personally\n"
        f"Use the same vocabulary a rural tradesperson would use\n"
        f"Never use corporate language or filler phrases\n"
        f"Never say 'I hope this finds you well'\n"
        f"Never say 'please do not hesitate to contact us'\n"
        f"Be direct, honest, and specific\n"
        f"Include real numbers and real timelines\n"
        f"The customer should feel like they are dealing with a real person who knows what they are doing\n"
        f"Format for SMS — no markdown, no bullet symbols, use plain line breaks only\n"
        f"Keep under 1500 characters so it fits in SMS"
    )


def build_user_prompt(customer_name: str, customer_address: str, job_type: str, raw_input: str, business_name: str) -> str:
    """
    Build the user prompt with job details for Claude to work from.
    """
    address_line = customer_address if customer_address else "address not provided — ask before finalizing price"
    return (
        f"A customer needs a septic service proposal. Here are the details:\n"
        f"Customer: {customer_name}\n"
        f"Location: {address_line}\n"
        f"Job type: {job_type}\n"
        f"Details: {raw_input}\n\n"
        f"Write a complete proposal this owner can send directly to the customer. Include:\n"
        f"What the job is in plain language\n"
        f"What the work involves\n"
        f"A realistic price range based on typical {job_type} costs in rural Maine\n"
        f"Timeline for when the work can be done\n"
        f"A clear call to action — how to confirm the job\n"
        f"The owner's name and business name at the end\n\n"
        f"Do not add any explanation or commentary.\n"
        f"Output the proposal text only."
    )


FALLBACK_MESSAGE = (
    "Something went wrong generating your proposal. "
    "Call Jeremy at 207-653-8819"
)


def run(client_phone: str, customer_phone: str, raw_input: str) -> str | None:
    """
    Main entry point for the proposal agent.

    Args:
        client_phone:   The business owner's Telnyx number (e.g. "+12074190986")
        customer_phone: The end customer's phone number (e.g. "+12076538819")
        raw_input:      The raw SMS text describing the job

    Returns:
        The generated proposal text, or None on failure.
    """
    print(f"[{timestamp()}] INFO proposal_agent: Starting run | client={client_phone} customer={customer_phone}")

    # ------------------------------------------------------------------
    # Step 1: Load client record — who is the business owner?
    # ------------------------------------------------------------------
    client = get_client_by_phone(client_phone)
    if not client:
        print(f"[{timestamp()}] ERROR proposal_agent: No client found for {client_phone}")
        send_sms(to_number=client_phone, message_body=FALLBACK_MESSAGE)
        return None

    client_id    = client["id"]
    owner_mobile = client.get("owner_mobile") or client_phone
    print(f"[{timestamp()}] INFO proposal_agent: Client → {client['business_name']} (id={client_id})")

    # ------------------------------------------------------------------
    # Step 2: Log the inbound message to message history
    # ------------------------------------------------------------------
    try:
        log_message(
            client_id=client_id,
            direction="inbound",
            from_number=customer_phone,
            to_number=client_phone,
            body=raw_input,
        )
    except Exception as e:
        print(f"[{timestamp()}] WARN proposal_agent: Failed to log inbound message — {e}")

    # ------------------------------------------------------------------
    # Step 3: Parse the raw input for key details
    # ------------------------------------------------------------------
    job_type        = detect_job_type(raw_input)
    customer_name   = extract_customer_name(raw_input)
    customer_address = extract_address(raw_input)

    print(f"[{timestamp()}] INFO proposal_agent: Parsed → job_type={job_type} name={customer_name} address='{customer_address}'")

    # ------------------------------------------------------------------
    # Step 4: Look up or create the customer record
    # ------------------------------------------------------------------
    try:
        customer = get_customer_by_phone(client_id, customer_phone)
        if customer:
            customer_id = customer["id"]
            print(f"[{timestamp()}] INFO proposal_agent: Existing customer → {customer['customer_name']} (id={customer_id})")
        else:
            customer_id = create_customer(
                client_id=client_id,
                name=customer_name,
                phone=customer_phone,
                address=customer_address if customer_address else None,
            )
            print(f"[{timestamp()}] INFO proposal_agent: Created new customer (id={customer_id})")
    except Exception as e:
        print(f"[{timestamp()}] ERROR proposal_agent: Customer lookup/create failed — {e}")
        send_sms(to_number=client_phone, message_body=FALLBACK_MESSAGE)
        return None

    # ------------------------------------------------------------------
    # Step 5: Create a new job record
    # ------------------------------------------------------------------
    try:
        job_id = create_job(
            client_id=client_id,
            customer_id=customer_id,
            job_type=job_type,
            raw_input=raw_input,
        )
        print(f"[{timestamp()}] INFO proposal_agent: Created job (id={job_id})")
    except Exception as e:
        print(f"[{timestamp()}] ERROR proposal_agent: create_job failed — {e}")
        send_sms(to_number=client_phone, message_body=FALLBACK_MESSAGE)
        return None

    # ------------------------------------------------------------------
    # Step 6: Build prompts and call Claude
    # Inject style overrides from owner's past edits if available
    # ------------------------------------------------------------------
    system_prompt = build_system_prompt(client)

    # Check for learned style preferences from past edits
    try:
        from execution.db_document import get_prompt_override
        override = get_prompt_override(client_id)
        if override and override.get("estimate_style_notes"):
            style_guidance = override["estimate_style_notes"]
            system_prompt = (
                f"Style guidance from owner's past edits: {style_guidance}\n\n"
                + system_prompt
            )
            print(f"[{timestamp()}] INFO proposal_agent: Injected estimate style override")
    except Exception as e:
        print(f"[{timestamp()}] WARN proposal_agent: Could not load prompt override — {e}")

    user_prompt   = build_user_prompt(customer_name, customer_address, job_type, raw_input, client["business_name"])

    print(f"[{timestamp()}] INFO proposal_agent: Calling Claude (sonnet) to generate proposal...")
    proposal_text = call_claude(system_prompt, user_prompt, model="sonnet")

    if not proposal_text:
        print(f"[{timestamp()}] ERROR proposal_agent: Claude returned no text — sending fallback")
        send_sms(to_number=client_phone, message_body=FALLBACK_MESSAGE)
        update_job_status(job_id, "new")  # leave as new so it can be retried
        return None

    print(f"[{timestamp()}] INFO proposal_agent: Proposal generated ({len(proposal_text)} chars)")

    # ------------------------------------------------------------------
    # Step 7: Save proposal to Supabase
    # ------------------------------------------------------------------
    try:
        # Parse a rough dollar estimate from the proposal for the amount field
        # We store 0 if we can't parse one — the full text has the real range
        amount = 0.00
        import re
        price_match = re.search(r"\$(\d{2,4})", proposal_text)
        if price_match:
            amount = float(price_match.group(1))

        proposal_id = save_proposal(
            job_id=job_id,
            client_id=client_id,
            customer_id=customer_id,
            proposal_text=proposal_text,
            amount=amount,
        )
        print(f"[{timestamp()}] INFO proposal_agent: Saved proposal (id={proposal_id})")
    except Exception as e:
        print(f"[{timestamp()}] WARN proposal_agent: save_proposal failed — {e}. Continuing anyway.")
        proposal_id = None

    # ------------------------------------------------------------------
    # Step 8: Build the edit URL for the owner.
    # The proposal row has an edit_token (auto-generated by DB default).
    # Owner gets a link to review/edit before sending to customer.
    # Fallback: send raw text if edit_token not available.
    # ------------------------------------------------------------------
    base_url = os.environ.get("BOLTS11_BASE_URL", "https://bolts11.com")

    # Fetch the saved proposal to get edit_token
    edit_url = None
    if proposal_id:
        try:
            from execution.db_connection import get_client as get_supabase
            supabase = get_supabase()
            prop_row = supabase.table("proposals").select("edit_token").eq("id", proposal_id).execute()
            if prop_row.data and prop_row.data[0].get("edit_token"):
                edit_token = prop_row.data[0]["edit_token"]
                edit_url = f"{base_url}/doc/edit/{edit_token}?type=proposal"
                print(f"[{timestamp()}] INFO proposal_agent: Edit URL → {edit_url}")
        except Exception as e:
            print(f"[{timestamp()}] WARN proposal_agent: Could not fetch edit_token — {e}")

    if edit_url:
        sms_body = f"{customer_name} estimate ready — ${amount:.0f}\nEdit & send: {edit_url}"
    else:
        print(f"[{timestamp()}] WARN proposal_agent: No edit URL — sending raw text fallback")
        sms_body = f"New proposal for {customer_name}:\n\n{proposal_text}"

    print(f"[{timestamp()}] INFO proposal_agent: Sending proposal link via SMS to {owner_mobile}")
    sms_result = send_sms(to_number=owner_mobile, message_body=sms_body, from_number=client_phone)

    if not sms_result["success"]:
        print(f"[{timestamp()}] ERROR proposal_agent: SMS send failed — {sms_result['error']}")
    else:
        print(f"[{timestamp()}] INFO proposal_agent: SMS sent (telnyx_id={sms_result['message_id']})")

    # ------------------------------------------------------------------
    # Step 9: Log the outbound message
    # ------------------------------------------------------------------
    try:
        log_message(
            client_id=client_id,
            direction="outbound",
            from_number=client_phone,
            to_number=owner_mobile,
            body=sms_body,
            agent_used="proposal_agent",
            job_id=job_id,
            telnyx_message_id=sms_result.get("message_id"),
        )
    except Exception as e:
        print(f"[{timestamp()}] WARN proposal_agent: Failed to log outbound message — {e}")

    # ------------------------------------------------------------------
    # Step 10: Schedule a 3-day follow-up
    # ------------------------------------------------------------------
    try:
        if proposal_id:
            follow_up_time = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
            schedule_followup(
                client_id=client_id,
                customer_id=customer_id,
                job_id=job_id,
                proposal_id=proposal_id,
                followup_type="estimate_followup",
                scheduled_for=follow_up_time,
            )
            print(f"[{timestamp()}] INFO proposal_agent: Follow-up scheduled for {follow_up_time}")
    except Exception as e:
        print(f"[{timestamp()}] WARN proposal_agent: schedule_followup failed — {e}")

    # ------------------------------------------------------------------
    # Step 11: Update job status to "estimated"
    # ------------------------------------------------------------------
    try:
        update_job_status(job_id, "estimated")
        if proposal_id:
            update_proposal_status(proposal_id, "sent")
    except Exception as e:
        print(f"[{timestamp()}] WARN proposal_agent: Status update failed — {e}")

    print(f"[{timestamp()}] INFO proposal_agent: Complete. job_id={job_id}")
    try:
        log_activity(
            client_phone=client_phone,
            agent_name="proposal_agent",
            action_taken="proposal_generated",
            input_summary=raw_input[:120],
            output_summary=f"proposal_id={proposal_id} job_id={job_id} amount=${amount}",
            sms_sent=sms_result.get("success", False),
        )
    except Exception:
        pass
    return proposal_text
