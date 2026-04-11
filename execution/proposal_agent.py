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
    result = run(client_phone="+15555550200",
                 customer_phone="+12075550100",
                 raw_input="need tank pumped 3 bedroom route 9")
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_client import get_client_by_phone
from execution.db_agent_activity import log_activity
from execution.db_customer import get_customer_by_phone, create_customer
from execution.db_jobs import create_job, update_job_status
from execution.db_proposals import save_proposal
from execution.db_messages import log_message
from execution.call_claude import call_claude
from execution.sms_send import send_sms
from execution.vertical_loader import load_vertical, get_default_job_type


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Job type keyword detection
# ---------------------------------------------------------------------------

_FALLBACK_JOB_TYPE_KEYWORDS = {
    "emergency": ["emergency", "backup", "overflow", "flooding", "smell", "alarm", "urgent", "asap"],
    "repair":    ["repair", "fix", "broken", "cracked", "collapsed", "failing", "failed",
                  "replacement", "replace", "baffle", "install", "installation", "new system"],
    "locate":    ["locate", "find", "where is", "mark", "lost lid", "lost cover"],
    "inspect":   ["inspect", "inspection", "check", "evaluate", "assessment", "look at"],
    "pump":      ["pump", "pumped", "pumping", "empty", "emptied", "full", "due", "years"],
}


def _get_job_type_keywords(vertical_key: str) -> dict:
    config = load_vertical(vertical_key)
    kw_map = config.get("sms_keywords", {}).get("job_type_map", {})
    return kw_map if kw_map else _FALLBACK_JOB_TYPE_KEYWORDS


def _get_default_job_type(vertical_key: str) -> str:
    return get_default_job_type(vertical_key)


def detect_job_type(text: str, vertical_key: str = "sewer_drain") -> str:
    """Scan message for job type keywords. Emergency overrides all others."""
    text_lower = text.lower()
    keywords_map = _get_job_type_keywords(vertical_key)
    for job_type, keywords in keywords_map.items():
        for kw in keywords:
            if kw in text_lower:
                return job_type
    return _get_default_job_type(vertical_key)


def parse_job_fields(raw_input: str) -> dict:
    """
    Use Claude Haiku to extract structured fields from a natural language
    job description texted from the field.

    Returns dict with keys: name, address, job_type, price, notes
    All keys always present. Unknown values return empty string or None.
    """
    import json, re

    if not raw_input or not raw_input.strip():
        return {"name": "", "address": "", "job_type": "service", "price": None, "notes": ""}

    system_prompt = (
        "You extract structured job details from short text messages sent by trades business owners. "
        "Return only valid JSON. No explanation. No markdown fences."
    )

    user_prompt = f"""Extract job details from this text message sent by a trades business owner.

Message: "{raw_input}"

Return ONLY a JSON object with these exact keys:
{{
  "name": "customer full name or empty string if not found",
  "address": "address, street, road, or location mentioned or empty string",
  "job_type": "pump_out | repair | inspection | cleanout | install | service | other",
  "price": dollar amount as number or null,
  "notes": "any other relevant job details"
}}

Rules:
- name: extract any person's name mentioned. Capitalize it. If no name found, return empty string.
- address: extract any location — street address, road name, route number, town name. Empty string if none found.
- job_type: pick the closest match from the list above.
- price: extract the dollar amount IF mentioned anywhere in the message.
  Number only, no $ sign, no commas. null only if no amount is present.
  Examples:
    "pump out Brian $300"          → price: 300
    "needs riser replaced for 750" → price: 750
    "$1,250 for the install"       → price: 1250
    "estimate Bob a baffle job"    → price: null
- notes: everything else useful about the job."""

    try:
        response = call_claude(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model="haiku",
            max_tokens=256,
        )
        if response:
            clean = re.sub(r"```[a-z]*\n?", "", response).strip().rstrip("`")
            return json.loads(clean)
    except Exception as e:
        print(f"[{timestamp()}] WARN proposal_agent: parse_job_fields Haiku call failed — {e}")

    return {"name": "", "address": "", "job_type": "service", "price": None, "notes": raw_input}


def summarize_job(raw_input: str) -> str:
    """
    Use Claude Haiku to summarize raw owner input into a clean 1-line
    job description for the proposal document.

    HARD RULE: The summary must NEVER contain a dollar amount or price.
    Prices in the notes/summary field cause double-pricing on the document
    when the tech's explicit price also appears in the line items. This
    is the root cause of the "two prices on one estimate" bug.
    """
    import re
    try:
        summary = call_claude(
            "You summarize trade job descriptions in one clean sentence.",
            (
                "Summarize this job description in one clean sentence of 15 words or fewer.\n"
                "Use plain trade language. Include: job type, property type if mentioned, key scope items.\n"
                "CRITICAL: Do NOT include any dollar amounts, prices, or cost references whatsoever.\n"
                "Do not include customer names or conversational filler.\n"
                f"Input: {raw_input}"
            ),
            model="haiku",
        )
        if summary and len(summary.strip()) > 5:
            # Belt-and-suspenders: strip any dollar amounts that slipped through
            clean = re.sub(r'\$[\d,]+(\.\d{1,2})?', '', summary).strip()
            clean = re.sub(r'\b\d{3,5}\b', '', clean).strip()  # bare numbers like "325"
            clean = re.sub(r'\s+', ' ', clean).strip()
            if len(clean) > 5:
                return clean
    except Exception as e:
        print(f"[{timestamp()}] WARN proposal_agent: Job summarization failed — {e}")
    # Fallback: use job type detection — never includes a price
    return detect_job_type(raw_input).replace("_", " ").title() + " service"


def build_structured_prompt(client: dict, customer_name: str, customer_address: str,
                            job_type: str, raw_input: str) -> tuple:
    """
    Build system + user prompts that demand structured JSON output.
    Returns (system_prompt, user_prompt) tuple.

    PRICING RULE: This function is only called when no explicit price was
    provided by the tech. In that case, Claude may use the pricebook standard
    price. If the pricebook is empty, Claude must NOT invent a price — it
    must return an amount of 0 so the tech is prompted to enter one manually.
    """
    personality = client.get("personality", "")
    address_line = customer_address if customer_address else "address not provided"

    # Load standard prices from pricebook — single price only, no ranges
    pricing_context = ""
    try:
        from execution.db_pricebook import get_pricebook_for_prompt
        pricing_context = get_pricebook_for_prompt(client["id"])
    except Exception:
        pass

    if pricing_context:
        pricing_rules = (
            f"PRICE BOOK — Standard prices for this business:\n"
            f"{pricing_context}\n\n"
            f"PRICING RULES:\n"
            f"- Match the job to the closest service above and use that standard price.\n"
            f"- If the tech stated a specific price anywhere in their description, use that price exactly.\n"
            f"- For labor-only items, multiply hours × the hourly rate from the personality layer.\n"
            f"- If no matching service exists and no price was stated, use 0 as the amount —\n"
            f"  do NOT invent a price. The tech will fill it in on review.\n"
            f"- Never produce a $0.00 line item when a pricebook match exists.\n"
        )
    else:
        # No pricebook configured — do NOT fall back to hardcoded prices.
        # Claude inventing prices is the exact bug this system exists to prevent.
        # Return 0 so the review screen prompts the tech to enter the price.
        pricing_rules = (
            f"PRICING RULES:\n"
            f"- No price book is configured for this business yet.\n"
            f"- If the tech stated a specific price in their description, use that price exactly.\n"
            f"- If no price was stated, use 0 as the amount for each line item.\n"
            f"- Do NOT invent or estimate prices. The tech will enter them on the review screen.\n"
        )

    system_prompt = (
        f"You are generating a professional trade services proposal for {client['business_name']}.\n\n"
        f"PERSONALITY AND VOICE:\n{personality}\n\n"
        f"{pricing_rules}\n"
        f"OUTPUT FORMAT — NON-NEGOTIABLE:\n"
        f"You must return a JSON object with this exact structure and nothing else:\n"
        f'{{\n'
        f'  "job_summary": "One sentence describing the job scope — NO prices, NO customer names",\n'
        f'  "line_items": [\n'
        f'    {{"description": "Service description in plain trade language", "amount": 000.00}},\n'
        f'    ...\n'
        f'  ],\n'
        f'  "notes": "Any scope clarifications or assumptions (optional, can be empty string)"\n'
        f'}}\n\n'
        f"LINE ITEM RULES:\n"
        f"- Each line item describes ONE specific item of work or material\n"
        f"- Description: trade language only — \"Septic pump-out — 1,000 gal. tank\"\n"
        f"- job_summary must NEVER contain a dollar amount or price — descriptions only\n"
        f"- Never include customer names in any field\n"
        f"- Amount: number only, no $ sign\n"
        f"- If the tech stated a price, use it. If pricebook has a match, use it.\n"
        f"  Otherwise use 0 — never invent a number.\n"
        f"- Return ONLY the JSON object — no markdown, no explanation, no code fences"
    )

    user_prompt = (
        f"Customer: {customer_name}\n"
        f"Location: {address_line}\n"
        f"Job type: {job_type}\n"
        f"Tech's description: {raw_input}"
    )

    return (system_prompt, user_prompt)


FALLBACK_MESSAGE = (
    "Something went wrong generating your proposal. "
    "Please contact us directly."
)


def run(
    client_phone: str,
    customer_phone: str,
    raw_input: str,
    explicit_amount: float | None = None,
) -> str | None:
    """
    Main entry point for the proposal agent.

    Args:
        client_phone:    The business owner's Telnyx number
        customer_phone:  The end customer's phone number
        raw_input:       The raw SMS/PWA text describing the job
        explicit_amount: HARD RULE — when the tech provided an exact dollar
                         amount (chat action chip, PWA input, owner override),
                         this is the price. Claude is never called to reprice.
                         Pass None only when no price was given at all.

    Returns:
        The generated proposal text, or None on failure.
    """
    print(
        f"[{timestamp()}] INFO proposal_agent: Starting run | "
        f"client={client_phone} customer={customer_phone} "
        f"explicit_amount={explicit_amount}"
    )

    # Step 1: Load client record
    client = get_client_by_phone(client_phone)
    if not client:
        print(f"[{timestamp()}] ERROR proposal_agent: No client found for {client_phone}")
        send_sms(to_number=client_phone, message_body=FALLBACK_MESSAGE)
        return None

    client_id    = client["id"]
    owner_mobile = client.get("owner_mobile") or client_phone
    print(f"[{timestamp()}] INFO proposal_agent: Client → {client['business_name']} (id={client_id})")

    # Step 2: Log the inbound message
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

    # Step 3: Parse raw input for key details
    vertical_key    = client.get("trade_vertical", "sewer_drain")
    job_type        = detect_job_type(raw_input, vertical_key=vertical_key)
    parsed          = parse_job_fields(raw_input)
    customer_name   = parsed.get("name", "")
    customer_address = parsed.get("address", "")
    price_hint      = parsed.get("price")

    default_jt = _get_default_job_type(vertical_key)
    haiku_jt   = parsed.get("job_type", "")
    if job_type == default_jt and haiku_jt and haiku_jt not in ("service", "other"):
        job_type = haiku_jt

    print(f"[{timestamp()}] INFO proposal_agent: Parsed → job_type={job_type} name={customer_name!r} price_hint={price_hint}")

    # Step 4: Owner phone guard
    owner_phones = {client.get("phone", ""), client.get("owner_mobile", "")}
    owner_phones.discard("")
    if customer_phone and customer_phone in owner_phones:
        print(f"[{timestamp()}] ERROR proposal_agent: customer_phone matches owner — aborting")
        send_sms(
            to_number=owner_mobile,
            message_body="Couldn't find that customer. Try: EST +12075558806 [job description] or add them: ADD CUSTOMER [Name] [phone]",
            from_number=client_phone,
        )
        return None

    # Step 4b: Look up or create the customer record
    try:
        customer = get_customer_by_phone(client_id, customer_phone) if customer_phone else None
        if customer:
            customer_id      = customer["id"]
            customer_name    = customer.get("customer_name", customer_name)
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
            customer_id = None
            if customer_name and customer_name != "Customer":
                try:
                    from execution.db_connection import get_client as get_supabase
                    sb = get_supabase()
                    name_parts = customer_name.strip().split()
                    found = None
                    name_results = sb.table("customers").select(
                        "id, customer_name, customer_phone, customer_address"
                    ).eq("client_id", client_id).ilike(
                        "customer_name", f"%{customer_name}%"
                    ).limit(1).execute()
                    if name_results.data:
                        found = name_results.data[0]
                    elif len(name_parts) >= 2:
                        last_name = name_parts[-1]
                        name_results = sb.table("customers").select(
                            "id, customer_name, customer_phone, customer_address"
                        ).eq("client_id", client_id).ilike(
                            "customer_name", f"%{last_name}%"
                        ).limit(3).execute()
                        if name_results.data:
                            first_name = name_parts[0].lower()
                            for candidate in name_results.data:
                                if first_name in candidate.get("customer_name", "").lower():
                                    found = candidate
                                    break
                            if not found:
                                found = name_results.data[0]
                    if found:
                        customer_id      = found["id"]
                        customer_name    = found.get("customer_name", customer_name)
                        customer_address = found.get("customer_address", customer_address) or customer_address
                        customer_phone   = found.get("customer_phone")
                        print(f"[{timestamp()}] INFO proposal_agent: Found customer by name: {customer_name}")
                except Exception as e:
                    print(f"[{timestamp()}] WARN proposal_agent: Name search failed — {e}")

            if not customer_id:
                print(f"[{timestamp()}] ERROR proposal_agent: Could not resolve customer")
                send_sms(
                    to_number=owner_mobile,
                    message_body="Couldn't find that customer. Try: EST +12075558806 [job description] or add them: ADD CUSTOMER [Name] [phone]",
                    from_number=client_phone,
                )
                return None
    except Exception as e:
        print(f"[{timestamp()}] ERROR proposal_agent: Customer lookup/create failed — {e}")
        send_sms(to_number=client_phone, message_body=FALLBACK_MESSAGE)
        return None

    # Step 5: Summarize raw input — description only, NO prices
    job_summary = summarize_job(raw_input)
    print(f"[{timestamp()}] INFO proposal_agent: Job summary → {job_summary}")

    # Step 5b: Create a new job record
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
    # Step 6: Determine effective price.
    #
    # HARD RULE — tech price is never overridden by Claude:
    # (1) explicit_amount kwarg — from chat chip or PWA input
    # (2) price_hint — dollar amount parsed from the raw message text
    # (3) Neither present — Claude generates line items using pricebook
    #     standard prices only. If no pricebook, amounts default to 0
    #     and the tech enters prices on the review screen.
    # ------------------------------------------------------------------
    import json as _json
    import re

    def _coerce_amount(raw) -> float | None:
        if raw is None:
            return None
        try:
            v = float(raw)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    effective_amount = _coerce_amount(explicit_amount)
    if effective_amount is None:
        effective_amount = _coerce_amount(price_hint)

    line_items: list[dict] = []
    amount = 0.00
    proposal_text = ""

    if effective_amount is not None:
        # Tech provided a price — bypass Claude pricing entirely
        print(
            f"[{timestamp()}] INFO proposal_agent: BYPASSING Claude pricing — "
            f"using explicit amount=${effective_amount:.2f} "
            f"(source={'kwarg' if explicit_amount else 'parsed_hint'})"
        )
        line_items = [{
            "description": job_summary or "Service",
            "amount": effective_amount,
        }]
        amount = effective_amount
        proposal_text = f"{job_summary}\n\n{job_summary or 'Service'} — ${amount:.2f}\n\nTotal: ${amount:.2f}"

    else:
        # No price from tech — call Claude with pricebook standard prices
        system_prompt, user_prompt = build_structured_prompt(
            client, customer_name, customer_address, job_type, raw_input,
        )

        try:
            from execution.db_document import get_prompt_override
            override = get_prompt_override(client_id)
            if override and override.get("estimate_style_notes"):
                system_prompt = (
                    f"Style guidance from owner's past edits: {override['estimate_style_notes']}\n\n"
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
        proposal_text = raw_response

        try:
            cleaned = re.sub(r'^```(?:json)?\s*', '', raw_response.strip())
            cleaned = re.sub(r'\s*```$', '', cleaned)
            parsed_resp = _json.loads(cleaned)

            if isinstance(parsed_resp, dict) and "line_items" in parsed_resp:
                line_items = parsed_resp["line_items"]
                amount = sum(float(li.get("amount", 0)) for li in line_items)
                if parsed_resp.get("job_summary"):
                    # Strip prices from Claude's job_summary too — belt and suspenders
                    raw_js = parsed_resp["job_summary"]
                    clean_js = re.sub(r'\$[\d,]+(\.\d{1,2})?', '', raw_js).strip()
                    job_summary = clean_js if len(clean_js) > 5 else job_summary
                lines = [f"{li['description']} — ${float(li['amount']):.2f}" for li in line_items]
                proposal_text = f"{job_summary}\n\n" + "\n".join(lines) + f"\n\nTotal: ${amount:.2f}"
                if parsed_resp.get("notes"):
                    proposal_text += f"\n\n{parsed_resp['notes']}"
                print(f"[{timestamp()}] INFO proposal_agent: Parsed {len(line_items)} line items, total=${amount:.2f}")
            else:
                print(f"[{timestamp()}] WARN proposal_agent: Claude returned JSON but no line_items key")
        except (_json.JSONDecodeError, ValueError) as e:
            print(f"[{timestamp()}] WARN proposal_agent: JSON parse failed — {e}. Using raw text fallback.")
            price_match = re.search(r"\$(\d{2,5})", raw_response)
            if price_match:
                amount = float(price_match.group(1))

    # Step 6b: Track pricebook usage
    if line_items and client_id:
        try:
            from execution.db_pricebook import get_pricebook, increment_usage
            pricebook = get_pricebook(client_id)
            if pricebook:
                pb_lookup = {item["job_name"].lower(): item for item in pricebook}
                for li in line_items:
                    desc = (li.get("description") or "").lower()
                    for pb_name, pb_item in pb_lookup.items():
                        if pb_name in desc or desc in pb_name:
                            increment_usage(pb_item["id"])
                            break
        except Exception as e:
            print(f"[{timestamp()}] WARN proposal_agent: pricebook usage tracking failed — {e}")

    # Step 7: Save proposal to Supabase
    try:
        proposal_id = save_proposal(
            job_id=job_id,
            client_id=client_id,
            customer_id=customer_id,
            proposal_text=proposal_text,
            amount=amount,
        )
        if proposal_id and line_items:
            try:
                from execution.db_connection import get_client as get_supabase
                get_supabase().table("proposals").update({
                    "line_items": _json.dumps(line_items),
                }).eq("id", proposal_id).execute()
            except Exception:
                pass
        print(f"[{timestamp()}] INFO proposal_agent: Saved proposal (id={proposal_id})")
    except Exception as e:
        print(f"[{timestamp()}] WARN proposal_agent: save_proposal failed — {e}. Continuing anyway.")
        proposal_id = None

    # Step 8: Build review URL for tech/owner
    base_url = os.environ.get("BOLTS11_BASE_URL", "https://bolts11.com")
    edit_url = None
    if proposal_id:
        try:
            from execution.db_connection import get_client as get_supabase
            supabase = get_supabase()
            prop_row = supabase.table("proposals").select("edit_token").eq("id", proposal_id).execute()
            if prop_row.data and prop_row.data[0].get("edit_token"):
                edit_token = prop_row.data[0]["edit_token"]
                edit_url = f"{base_url}/doc/review/{edit_token}?type=proposal"
                print(f"[{timestamp()}] INFO proposal_agent: Review URL → {edit_url}")
        except Exception as e:
            print(f"[{timestamp()}] WARN proposal_agent: Could not fetch edit_token — {e}")

    if edit_url:
        notify_body = f"{customer_name} estimate ready — ${amount:.0f}\nReview & approve: {edit_url}"
    else:
        print(f"[{timestamp()}] WARN proposal_agent: No edit URL — sending raw text fallback")
        notify_body = f"New proposal for {customer_name}:\n\n{proposal_text}"

    print(f"[{timestamp()}] INFO proposal_agent: Sending proposal review link to {owner_mobile}")
    from execution.notify import notify
    notify_result = notify(
        client_id=client_id,
        to_phone=owner_mobile,
        message=notify_body,
        subject=f"Estimate ready for {customer_name} — ${amount:.0f}",
        message_type="proposal",
    )
    if not notify_result["success"]:
        print(f"[{timestamp()}] ERROR proposal_agent: Notify failed — {notify_result['error']}")
    else:
        print(f"[{timestamp()}] INFO proposal_agent: Notified owner via {notify_result['channel']}")

    # Step 9: Log outbound message
    try:
        log_message(
            client_id=client_id,
            direction="outbound",
            from_number=client_phone,
            to_number=owner_mobile,
            body=notify_body,
            agent_used="proposal_agent",
            job_id=job_id,
        )
    except Exception as e:
        print(f"[{timestamp()}] WARN proposal_agent: Failed to log outbound message — {e}")

    # Step 10: Update job status
    # Do NOT mark as 'sent' here — that happens only in /doc/send
    # after the tech/owner reviews and approves the draft.
    try:
        update_job_status(job_id, "estimated")
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
            sms_sent=notify_result.get("success", False),
        )
    except Exception:
        pass
    return proposal_text
