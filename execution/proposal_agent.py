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


def summarize_job(raw_input: str) -> str:
    """
    Use Claude Haiku to summarize raw owner input into a clean 1-line
    job description. This is what gets stored in jobs.job_description
    and displayed on the proposal — NOT the raw SMS text.
    """
    try:
        summary = call_claude(
            "You summarize trade job descriptions in one clean sentence.",
            (
                "Summarize this job description in one clean sentence of 15 words or fewer.\n"
                "Use plain trade language. Include: job type, property type if mentioned, key scope items.\n"
                "Do not include customer names, pricing, or conversational filler.\n"
                f"Input: {raw_input}"
            ),
            model="haiku",
        )
        if summary and len(summary.strip()) > 5:
            return summary.strip()
    except Exception as e:
        print(f"[{timestamp()}] WARN proposal_agent: Job summarization failed — {e}")
    # Fallback: use job type detection
    return detect_job_type(raw_input).replace("_", " ").title() + " service"


def build_structured_prompt(client: dict, customer_name: str, customer_address: str,
                            job_type: str, raw_input: str) -> tuple:
    """
    Build system + user prompts that demand structured JSON output.
    Returns (system_prompt, user_prompt) tuple.
    """
    personality = client.get("personality", "")
    address_line = customer_address if customer_address else "address not provided"

    system_prompt = (
        f"You are generating a professional trade services proposal for {client['business_name']}.\n\n"
        f"PERSONALITY AND VOICE:\n{personality}\n\n"
        f"PRICING RULES — NON-NEGOTIABLE:\n"
        f"- Pump-out (1,000 gal): $275. Pump-out (1,500 gal): $325. Never $0.\n"
        f"- Baffle replacement: $175 minimum. Never $0.\n"
        f"- Riser and cover (12\"): use the price stated by the owner if given.\n"
        f"- Labor: multiply hours stated by owner × $125/hr. Show as \"Labor: X hrs @ $125/hr\"\n"
        f"- Travel: if owner mentions travel, add as a separate line item using minimum charge $150\n"
        f"  unless owner stated a different amount.\n"
        f"- If the owner stated a specific price for any item, use that price exactly.\n"
        f"- If no price is stated, use the standard rate from the personality layer.\n"
        f"- NEVER produce a $0.00 line item. If you don't know the price, use the minimum charge.\n"
        f"- NEVER duplicate line items across multiple locations unless the owner explicitly\n"
        f"  states there are multiple job sites.\n\n"
        f"OUTPUT FORMAT — NON-NEGOTIABLE:\n"
        f"You must return a JSON object with this exact structure and nothing else:\n"
        f'{{\n'
        f'  "job_summary": "One sentence describing the job in plain trade language",\n'
        f'  "line_items": [\n'
        f'    {{"description": "Service description — no customer names, no filler", "amount": 000.00}},\n'
        f'    ...\n'
        f'  ],\n'
        f'  "notes": "Any scope clarifications or assumptions (optional, can be empty string)"\n'
        f'}}\n\n'
        f"LINE ITEM RULES:\n"
        f"- Each line item describes ONE specific item of work or material\n"
        f"- Description: trade language only — \"Septic pump-out — 1,000 gal. tank\", \"Labor: 5 hrs @ $125/hr\"\n"
        f"- Never include customer names, greetings, or partial sentences in any line item\n"
        f"- Never truncate — every description must be complete\n"
        f"- Amount: number only, no $ sign\n"
        f"- Minimum charge is $150 — never go below this\n"
        f"- If the owner specified a price, use it exactly\n"
        f"- If the owner specified hours, calculate labor at $125/hr\n"
        f"- Return ONLY the JSON object — no markdown, no explanation, no code fences"
    )

    user_prompt = (
        f"Customer: {customer_name}\n"
        f"Location: {address_line}\n"
        f"Job type: {job_type}\n"
        f"Owner's description: {raw_input}"
    )

    return (system_prompt, user_prompt)


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
    # Step 4: HARD RULE — owner phones must never become customers
    # ------------------------------------------------------------------
    owner_phones = {client.get("phone", ""), client.get("owner_mobile", "")}
    owner_phones.discard("")
    if customer_phone and customer_phone in owner_phones:
        print(f"[{timestamp()}] ERROR proposal_agent: customer_phone {customer_phone} matches owner — aborting")
        send_sms(
            to_number=owner_mobile,
            message_body="Couldn't find that customer. Try: EST +12075558806 [job description] or add them: ADD CUSTOMER [Name] [phone]",
            from_number=client_phone,
        )
        return None

    # ------------------------------------------------------------------
    # Step 4b: Look up or create the customer record
    # ------------------------------------------------------------------
    try:
        customer = get_customer_by_phone(client_id, customer_phone) if customer_phone else None
        if customer:
            customer_id = customer["id"]
            customer_name = customer.get("customer_name", customer_name)
            customer_address = customer.get("customer_address", customer_address) or customer_address
            print(f"[{timestamp()}] INFO proposal_agent: Existing customer → {customer_name} (id={customer_id})")
        elif customer_phone:
            customer_id = create_customer(
                client_id=client_id,
                name=customer_name,
                phone=customer_phone,
                address=customer_address if customer_address else None,
            )
            print(f"[{timestamp()}] INFO proposal_agent: Created new customer (id={customer_id})")
        else:
            # No customer phone provided — try name search in DB
            customer_id = None
            if customer_name and customer_name != "Customer":
                try:
                    from execution.db_connection import get_client as get_supabase
                    sb = get_supabase()
                    name_results = sb.table("customers").select(
                        "id, customer_name, customer_phone, customer_address"
                    ).eq("client_id", client_id).ilike(
                        "customer_name", f"%{customer_name}%"
                    ).limit(1).execute()
                    if name_results.data:
                        found = name_results.data[0]
                        customer_id = found["id"]
                        customer_name = found.get("customer_name", customer_name)
                        customer_address = found.get("customer_address", customer_address) or customer_address
                        customer_phone = found.get("customer_phone")
                        print(f"[{timestamp()}] INFO proposal_agent: Found customer by name: {customer_name}")
                except Exception as e:
                    print(f"[{timestamp()}] WARN proposal_agent: Name search failed — {e}")

            if not customer_id:
                print(f"[{timestamp()}] ERROR proposal_agent: Could not resolve customer — no phone, name search failed")
                send_sms(
                    to_number=owner_mobile,
                    message_body=(
                        f"Couldn't find that customer. Try: EST +12075558806 [job description] "
                        f"or add them first with: ADD CUSTOMER [Name] [phone]"
                    ),
                    from_number=client_phone,
                )
                try:
                    log_activity(
                        client_phone=client_phone,
                        agent_name="proposal_agent",
                        action_taken="proposal_failed",
                        input_summary=raw_input[:120],
                        output_summary=f"Customer not found — proposal aborted",
                        sms_sent=True,
                    )
                except Exception:
                    pass
                return None
    except Exception as e:
        print(f"[{timestamp()}] ERROR proposal_agent: Customer lookup/create failed — {e}")
        send_sms(to_number=client_phone, message_body=FALLBACK_MESSAGE)
        return None

    # ------------------------------------------------------------------
    # Step 5: Summarize raw input into clean job description
    # ------------------------------------------------------------------
    job_summary = summarize_job(raw_input)
    print(f"[{timestamp()}] INFO proposal_agent: Job summary → {job_summary}")

    # ------------------------------------------------------------------
    # Step 5b: Create a new job record with clean description
    # ------------------------------------------------------------------
    try:
        job_id = create_job(
            client_id=client_id,
            customer_id=customer_id,
            job_type=job_type,
            raw_input=raw_input,
            job_description=job_summary,
        )
        print(f"[{timestamp()}] INFO proposal_agent: Created job (id={job_id})")
    except Exception as e:
        print(f"[{timestamp()}] ERROR proposal_agent: create_job failed — {e}")
        send_sms(to_number=client_phone, message_body=FALLBACK_MESSAGE)
        return None

    # ------------------------------------------------------------------
    # Step 6: Build structured prompt and call Claude for JSON line items
    # ------------------------------------------------------------------
    system_prompt, user_prompt = build_structured_prompt(
        client, customer_name, customer_address, job_type, raw_input,
    )

    # Inject style overrides from owner's past edits if available
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

    print(f"[{timestamp()}] INFO proposal_agent: Calling Claude (sonnet) for structured proposal...")
    raw_response = call_claude(system_prompt, user_prompt, model="sonnet")

    if not raw_response:
        print(f"[{timestamp()}] ERROR proposal_agent: Claude returned no text — sending fallback")
        send_sms(to_number=client_phone, message_body=FALLBACK_MESSAGE)
        update_job_status(job_id, "new")
        return None

    print(f"[{timestamp()}] INFO proposal_agent: Raw response ({len(raw_response)} chars)")

    # ------------------------------------------------------------------
    # Step 6b: Parse structured JSON response
    # ------------------------------------------------------------------
    import json as _json
    import re

    line_items = []
    amount = 0.00
    proposal_text = raw_response  # fallback

    try:
        # Strip markdown code fences if Claude wrapped the JSON
        cleaned = re.sub(r'^```(?:json)?\s*', '', raw_response.strip())
        cleaned = re.sub(r'\s*```$', '', cleaned)
        parsed = _json.loads(cleaned)

        if isinstance(parsed, dict) and "line_items" in parsed:
            line_items = parsed["line_items"]
            amount = sum(float(li.get("amount", 0)) for li in line_items)
            # Use parsed job_summary if available
            if parsed.get("job_summary"):
                job_summary = parsed["job_summary"]
            # Build clean proposal text from line items for SMS/display
            lines = [f"{li['description']} — ${float(li['amount']):.2f}" for li in line_items]
            proposal_text = f"{job_summary}\n\n" + "\n".join(lines) + f"\n\nTotal: ${amount:.2f}"
            if parsed.get("notes"):
                proposal_text += f"\n\n{parsed['notes']}"
            print(f"[{timestamp()}] INFO proposal_agent: Parsed {len(line_items)} line items, total=${amount:.2f}")
        else:
            print(f"[{timestamp()}] WARN proposal_agent: Claude returned JSON but no line_items key — using as text")
    except (_json.JSONDecodeError, ValueError) as e:
        print(f"[{timestamp()}] WARN proposal_agent: JSON parse failed — {e}. Using raw text fallback.")
        # Fallback: extract dollar amount from raw text
        price_match = re.search(r"\$(\d{2,5})", raw_response)
        if price_match:
            amount = float(price_match.group(1))

    # ------------------------------------------------------------------
    # Step 7: Save proposal to Supabase with structured line items
    # ------------------------------------------------------------------
    try:
        proposal_id = save_proposal(
            job_id=job_id,
            client_id=client_id,
            customer_id=customer_id,
            proposal_text=proposal_text,
            amount=amount,
        )
        # Write line_items JSON to the proposal row if we have structured data
        if proposal_id and line_items:
            try:
                from execution.db_connection import get_client as get_supabase
                get_supabase().table("proposals").update({
                    "line_items": _json.dumps(line_items),
                }).eq("id", proposal_id).execute()
            except Exception:
                pass  # Non-fatal — line_items column may not exist
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
            output_summary=f"Proposal for {customer_name} — ${amount:.0f}",
            sms_sent=sms_result.get("success", False),
        )
    except Exception:
        pass
    return proposal_text
