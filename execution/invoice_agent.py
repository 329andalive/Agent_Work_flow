"""
invoice_agent.py — Generates invoices from job completion texts

Flow:
  1. Parse raw_input: extract hours, materials, work description
  2. Load client + personality from Supabase
  3. Find the most recent estimated/new job for this customer
  4. Update job with actual_hours and mark complete
  5. Build Claude prompt with job details + pricing from personality
  6. Call Claude → get invoice text
  7. Parse invoice for total amount
  8. Save invoice to Supabase
  9. Run job_cost_agent.calculate() → get private margin summary
  10. Combine invoice + job cost summary into one SMS
  11. Send combined SMS to client_phone (owner-only, NOT customer)
  12. Log all messages
  13. Schedule 7-day payment follow-up

NOTE: The combined SMS goes to the OWNER, not the customer.
      The owner reads the invoice, then manually forwards the
      invoice portion to the customer. The job cost summary
      at the bottom is private margin data.

Usage:
    from execution.invoice_agent import run
    run(client_phone="+12074190986",
        customer_phone="+12075550100",
        raw_input="done at the Anderson place, replaced baffle, 3.5 hours, $95 parts")
"""

import os
import re
import sys
from datetime import datetime, timezone, timedelta, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_client import get_client_by_phone
from execution.db_agent_activity import log_activity
from execution.db_customer import get_customer_by_phone
from execution.db_jobs import get_jobs_by_client, update_job_completion, update_job_status
from execution.db_invoices import save_invoice, update_invoice_status
from execution.db_messages import log_message
from execution.db_followups import schedule_followup
from execution.call_claude import call_claude
from execution.sms_send import send_sms
from execution.job_cost_agent import calculate as calculate_job_cost


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


FALLBACK_MESSAGE = (
    "Something went wrong on the invoice. "
    "Call me at 207-419-0986"
)

# Minimum hours before we flag for clarification
HOURS_MISSING_REPLY = "Got it. How many hours did that take?"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_hours(text: str) -> float | None:
    """
    Extract hours worked from the owner's completion message.
    Returns a float or None if hours cannot be determined.

    Patterns matched (in priority order):
      "3.5 hours", "3.5hrs", "3 hrs", "took me 3.5", "spent 3 hours"
    """
    patterns = [
        r'(\d+(?:\.\d+)?)\s*hrs?\b',
        r'took\s+me\s+(\d+(?:\.\d+)?)',
        r'spent\s+(\d+(?:\.\d+)?)\s*hrs?\b',
        r'(\d+(?:\.\d+)?)\s*hours?\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def parse_flat_rate(text: str) -> float | None:
    """
    Detect when the owner specifies a flat total instead of hours.
    Patterns: "bill for $275", "charge $275", "invoice for $275", "flat $275"
    Returns the dollar amount as a float, or None if not found.
    """
    patterns = [
        r'bill\s+(?:her|him|them|for)\s+\$(\d+(?:\.\d+)?)',
        r'(?:a\s+)?bill\s+for\s+\$(\d+(?:\.\d+)?)',
        r'charge\s+(?:her|him|them)?\s*\$(\d+(?:\.\d+)?)',
        r'invoice\s+(?:her|him|them|for)\s+\$(\d+(?:\.\d+)?)',
        r'flat\s+(?:rate\s+)?\$(\d+(?:\.\d+)?)',
        r'total\s+(?:is\s+)?\$(\d+(?:\.\d+)?)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def parse_materials(text: str) -> tuple[str, float]:
    """
    Extract materials description and cost from the message.

    Returns:
        (description_string, dollar_amount)
        description_string is empty if no materials mentioned.
        dollar_amount is 0.0 if no cost found.
    """
    # Look for dollar amounts: "$95", "95 in parts", "$95 in materials"
    amount_match = re.search(
        r'\$(\d+(?:\.\d+)?)\s*(?:in\s+)?(?:parts?|materials?)?'
        r'|(\d+(?:\.\d+)?)\s+in\s+(?:parts?|materials?)',
        text, re.IGNORECASE
    )
    amount = 0.0
    if amount_match:
        val = amount_match.group(1) or amount_match.group(2)
        amount = float(val) if val else 0.0

    # Look for materials description: "used X", "parts: X", "materials: X"
    desc_match = re.search(
        r'(?:used|parts?|materials?)[:\s]+(.{5,60}?)(?:\.|,|$|\n)',
        text, re.IGNORECASE
    )
    desc = desc_match.group(1).strip() if desc_match else ""

    return desc, amount


def parse_invoice_total(invoice_text: str) -> float:
    """
    Pull the total dollar amount from the generated invoice text.
    Looks for "Total due: $X" or "Total: $X" patterns.
    Returns 0.0 if not found.
    """
    match = re.search(
        r'[Tt]otal\s+(?:due|amount)?:?\s*\$(\d+(?:\.\d+)?)',
        invoice_text
    )
    return float(match.group(1)) if match else 0.0


def extract_hourly_rate(personality: str) -> float:
    """Parse hourly rate from personality text. Falls back to $125."""
    match = re.search(r'[Hh]ourly rate.*?\$(\d+(?:\.\d+)?)', personality)
    return float(match.group(1)) if match else 125.00


def extract_payment_terms(personality: str) -> str:
    """Pull payment terms line from personality. Falls back to 'due on receipt'."""
    match = re.search(r'[Ss]tandard payment terms?:?\s*(.+?)(?:\n|$)', personality)
    return match.group(1).strip() if match else "due on receipt"


def extract_payment_methods(personality: str) -> str:
    """Pull payment methods from personality."""
    match = re.search(r'[Pp]ayment methods? accepted?:?\s*(.+?)(?:\n|$)', personality)
    return match.group(1).strip() if match else "check, cash, or Venmo"


# ---------------------------------------------------------------------------
# Claude prompt builders
# ---------------------------------------------------------------------------

def build_system_prompt(client: dict) -> str:
    return (
        f"You are the AI back office assistant for {client['business_name']}, "
        f"owned by {client['owner_name']}.\n"
        f"Read this Personality Layer completely before doing anything. "
        f"This is who you are writing for:\n\n"
        f"{client['personality']}\n\n"
        f"CRITICAL RULES:\n"
        f"Every word must sound like {client['owner_name']} wrote it personally\n"
        f"Plain text only — no markdown, no bullet symbols\n"
        f"Format for SMS — clean line breaks only\n"
        f"Under 1500 characters\n"
        f"Be specific about what was done — customers remember details\n"
        f"Include itemized breakdown of labor and parts\n"
        f"Payment terms must match the personality layer\n"
        f"End with business name and contact number\n"
        f"Never use corporate filler language\n"
        f"A rural Maine septic customer should feel like they got "
        f"a fair honest bill from someone they can trust"
    )


def build_user_prompt(
    client: dict,
    customer_name: str,
    customer_address: str,
    raw_input: str,
    actual_hours: float,
    materials_desc: str,
    materials_cost: float,
    estimated_amount: float,
    contract_type: str,
    job_id: str,
) -> str:
    hourly_rate    = extract_hourly_rate(client["personality"])
    payment_terms  = extract_payment_terms(client["personality"])
    payment_methods = extract_payment_methods(client["personality"])

    # Invoice number: INV-YYYYMMDD-last4ofJobId
    inv_date   = date.today().strftime("%Y%m%d")
    inv_suffix = job_id[-4:].upper() if job_id else "0000"
    inv_number = f"INV-{inv_date}-{inv_suffix}"

    labor_total = round(actual_hours * hourly_rate, 2)

    materials_line = (
        f"Parts/materials: {materials_desc} = ${materials_cost:.2f}"
        if materials_desc or materials_cost > 0
        else "No additional parts or materials"
    )

    estimate_line = (
        f"Original estimate was: ${estimated_amount:.2f}"
        if estimated_amount > 0
        else "No prior estimate on file"
    )

    return (
        f"Generate a complete invoice for this completed septic job.\n\n"
        f"Business: {client['business_name']}\n"
        f"Owner: {client['owner_name']}\n"
        f"Invoice number: {inv_number}\n"
        f"Date: {date.today().strftime('%B %d, %Y')}\n"
        f"Customer: {customer_name}\n"
        f"Address: {customer_address or 'on file'}\n"
        f"Work completed: {raw_input}\n\n"
        f"Job details:\n"
        f"Hours worked: {actual_hours}\n"
        f"Hourly rate: ${hourly_rate:.2f}/hr\n"
        f"Labor total: ${labor_total:.2f}\n"
        f"{materials_line}\n"
        f"{estimate_line}\n"
        f"Contract type: {contract_type}\n\n"
        f"Payment terms: {payment_terms}\n"
        f"Payment methods: {payment_methods}\n\n"
        f"Generate a professional invoice that includes:\n"
        f"Invoice number {inv_number} and today's date\n"
        f"Customer name and address\n"
        f"Itemized list of work performed in plain language\n"
        f"Labor line: {actual_hours} hrs x ${hourly_rate:.2f}/hr\n"
        f"Parts line if applicable\n"
        f"Total due\n"
        f"Payment terms and accepted methods\n"
        f"A brief thank-you in the owner's voice\n"
        f"Business name and phone at the bottom\n\n"
        f"Output the invoice text only. No commentary."
    )


# ---------------------------------------------------------------------------
# Main agent function
# ---------------------------------------------------------------------------

def run(client_phone: str, customer_phone: str, raw_input: str) -> str | None:
    """
    Main entry point for the invoice agent.

    Args:
        client_phone:   The Telnyx number (identifies the business owner)
        customer_phone: The customer's phone number (used to find their job)
        raw_input:      The owner's completion text

    Returns:
        The combined SMS text sent to the owner, or None on failure.
    """
    print(f"[{timestamp()}] INFO invoice_agent: Starting run | client={client_phone} customer={customer_phone}")

    # ------------------------------------------------------------------
    # Step 1: Load client record
    # ------------------------------------------------------------------
    client = get_client_by_phone(client_phone)
    if not client:
        print(f"[{timestamp()}] ERROR invoice_agent: No client found for {client_phone}")
        return None

    client_id    = client["id"]
    owner_mobile = client.get("owner_mobile") or customer_phone
    print(f"[{timestamp()}] INFO invoice_agent: Client → {client['business_name']}")

    # ------------------------------------------------------------------
    # Step 2: Log the inbound message
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
        print(f"[{timestamp()}] WARN invoice_agent: inbound log failed — {e}")

    # ------------------------------------------------------------------
    # Step 3: Parse the raw input
    # ------------------------------------------------------------------
    actual_hours    = parse_hours(raw_input)
    flat_rate       = parse_flat_rate(raw_input)
    materials_desc, materials_cost = parse_materials(raw_input)

    # If no hours but a flat rate was specified, use it directly — no clarification needed
    if actual_hours is None and flat_rate is not None:
        print(f"[{timestamp()}] INFO invoice_agent: Flat rate detected — ${flat_rate} (no hours required)")
        actual_hours   = 0.0
        materials_cost = flat_rate   # pass flat rate as the billable amount
        materials_desc = materials_desc or "services rendered"
    elif actual_hours is None:
        print(f"[{timestamp()}] INFO invoice_agent: Hours not found — sending clarification request")
        send_sms(to_number=owner_mobile, message_body=HOURS_MISSING_REPLY, from_number=client_phone)
        return None

    print(f"[{timestamp()}] INFO invoice_agent: Parsed → {actual_hours}hrs flat_rate=${flat_rate} | materials='{materials_desc}' ${materials_cost}")

    # ------------------------------------------------------------------
    # Step 4: Find the customer and their most recent open job
    # ------------------------------------------------------------------
    customer    = get_customer_by_phone(client_id, customer_phone)
    customer_id = customer["id"] if customer else None
    customer_name    = customer["customer_name"] if customer else "Customer"
    customer_address = customer.get("customer_address", "") if customer else ""

    # Find the most recent job for this customer that needs invoicing
    job = None
    if customer_id:
        all_jobs = get_jobs_by_client(client_id)
        for j in all_jobs:
            if j.get("customer_id") == customer_id and j.get("status") in ("estimated", "new", "scheduled"):
                job = j
                break  # newest first — take the first match

    if not job:
        # No matching job found — create a new one on the fly
        print(f"[{timestamp()}] WARN invoice_agent: No open job found — creating one from completion text")
        from execution.db_customer import create_customer
        from execution.db_jobs import create_job
        if not customer_id:
            customer_id = create_customer(client_id, "Customer", customer_phone)
        job_id = create_job(
            client_id=client_id,
            customer_id=customer_id,
            job_type="repair",
            raw_input=raw_input,
        )
        estimated_amount = 0.0
        contract_type    = "time_and_materials"
    else:
        job_id           = job["id"]
        estimated_amount = float(job.get("estimated_amount") or 0)
        contract_type    = job.get("contract_type") or "time_and_materials"

    print(f"[{timestamp()}] INFO invoice_agent: Job → id={job_id} est=${estimated_amount}")

    # ------------------------------------------------------------------
    # Step 5: Calculate actual amount and update job to complete
    # ------------------------------------------------------------------
    hourly_rate   = extract_hourly_rate(client["personality"])
    labor_total   = round(actual_hours * hourly_rate, 2)
    actual_amount = round(labor_total + materials_cost, 2)

    update_job_completion(
        job_id=job_id,
        actual_hours=actual_hours,
        actual_amount=actual_amount,
        notes=raw_input,
    )

    # ------------------------------------------------------------------
    # Step 6: Call Claude to generate the invoice
    # ------------------------------------------------------------------
    system_prompt = build_system_prompt(client)
    user_prompt   = build_user_prompt(
        client=client,
        customer_name=customer_name,
        customer_address=customer_address,
        raw_input=raw_input,
        actual_hours=actual_hours,
        materials_desc=materials_desc,
        materials_cost=materials_cost,
        estimated_amount=estimated_amount,
        contract_type=contract_type,
        job_id=job_id,
    )

    print(f"[{timestamp()}] INFO invoice_agent: Calling Claude to generate invoice...")
    invoice_text = call_claude(system_prompt, user_prompt, model="sonnet")

    if not invoice_text:
        print(f"[{timestamp()}] ERROR invoice_agent: Claude returned no text")
        send_sms(to_number=owner_mobile, message_body=FALLBACK_MESSAGE)
        return None

    print(f"[{timestamp()}] INFO invoice_agent: Invoice generated ({len(invoice_text)} chars)")

    # ------------------------------------------------------------------
    # Step 7: Save invoice to Supabase
    # ------------------------------------------------------------------
    parsed_total = parse_invoice_total(invoice_text)
    final_amount = parsed_total if parsed_total > 0 else actual_amount

    invoice_id = save_invoice(
        job_id=job_id,
        client_id=client_id,
        customer_id=customer_id,
        invoice_text=invoice_text,
        amount_due=final_amount,
    )

    # Update job with the final invoiced amount and status
    try:
        from execution.db_connection import get_client as get_supabase
        supabase = get_supabase()
        supabase.table("jobs").update({
            "actual_amount": final_amount,
            "status": "invoiced",
        }).eq("id", job_id).execute()
    except Exception as e:
        print(f"[{timestamp()}] WARN invoice_agent: job amount/status update failed — {e}")

    if invoice_id:
        update_invoice_status(invoice_id, "sent")

    # ------------------------------------------------------------------
    # Step 8: Generate a signed token URL for the invoice.
    # The HTML is rendered server-side via Flask /i/<token> route.
    # ------------------------------------------------------------------
    from execution.token_generator import generate_token

    base_url = os.environ.get("BOLTS11_BASE_URL", "https://bolts11.com")
    token = generate_token(job_id=job_id, client_phone=client_phone, link_type="invoice")

    if token:
        invoice_url = f"{base_url}/i/{token}"
        print(f"[{timestamp()}] INFO invoice_agent: Invoice link → {invoice_url}")
    else:
        invoice_url = None
        print(f"[{timestamp()}] WARN invoice_agent: Token generation failed — sending raw text only")

    # ------------------------------------------------------------------
    # Step 9: Run job cost agent → get private margin summary
    # ------------------------------------------------------------------
    cost_summary = calculate_job_cost(job_id=job_id, client_id=client_id)
    print(f"[{timestamp()}] INFO invoice_agent: Job cost summary → {cost_summary}")

    # ------------------------------------------------------------------
    # Step 10: Build the combined owner SMS
    #
    # FORMAT:
    #   Invoice for [Customer] — forward this link:
    #   [INVOICE URL]
    #
    #   JOB COST SUMMARY
    #   [COST SUMMARY LINE]
    #
    # The INVOICE URL is what the owner forwards to the customer.
    # The JOB COST SUMMARY is private — never goes to the customer.
    # ------------------------------------------------------------------
    if invoice_url:
        combined_sms = (
            f"Invoice for {customer_name} — forward this link:\n"
            f"{invoice_url}\n\n"
            f"---\n"
            f"JOB COST (owner only)\n"
            f"{cost_summary}"
        )
    else:
        combined_sms = (
            f"{invoice_text}\n\n"
            f"---\n"
            f"JOB COST (owner only)\n"
            f"{cost_summary}"
        )

    # ------------------------------------------------------------------
    # Step 10: Send combined SMS to the OWNER'S Telnyx number
    # ------------------------------------------------------------------
    print(f"[{timestamp()}] INFO invoice_agent: Sending combined invoice+cost SMS to {owner_mobile}")
    sms_result = send_sms(to_number=owner_mobile, message_body=combined_sms, from_number=client_phone)

    if not sms_result["success"]:
        print(f"[{timestamp()}] ERROR invoice_agent: SMS send failed — {sms_result['error']}")
    else:
        print(f"[{timestamp()}] INFO invoice_agent: SMS sent (telnyx_id={sms_result['message_id']})")

    # ------------------------------------------------------------------
    # Step 11: Log the outbound message
    # ------------------------------------------------------------------
    try:
        log_message(
            client_id=client_id,
            direction="outbound",
            from_number=client_phone,
            to_number=client_phone,
            body=combined_sms,
            agent_used="invoice_agent",
            job_id=job_id,
            telnyx_message_id=sms_result.get("message_id"),
        )
    except Exception as e:
        print(f"[{timestamp()}] WARN invoice_agent: outbound log failed — {e}")

    # ------------------------------------------------------------------
    # Step 12: Schedule 7-day payment follow-up
    # ------------------------------------------------------------------
    try:
        followup_time = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        schedule_followup(
            client_id=client_id,
            customer_id=customer_id,
            job_id=job_id,
            proposal_id=None,
            followup_type="payment_chase",
            scheduled_for=followup_time,
        )
        print(f"[{timestamp()}] INFO invoice_agent: Payment follow-up scheduled for {followup_time}")
    except Exception as e:
        print(f"[{timestamp()}] WARN invoice_agent: schedule_followup failed — {e}")

    print(f"[{timestamp()}] INFO invoice_agent: Complete. job_id={job_id} amount=${final_amount}")
    try:
        log_activity(
            client_phone=client_phone,
            agent_name="invoice_agent",
            action_taken="invoice_generated",
            input_summary=raw_input[:120],
            output_summary=f"invoice_id={invoice_id} job_id={job_id} amount=${final_amount}",
            sms_sent=sms_result.get("success", False),
        )
    except Exception:
        pass
    return combined_sms
