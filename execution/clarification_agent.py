"""
clarification_agent.py — Intercepts ambiguous SMS and gathers structured data

Two jobs:
  A) New ambiguous messages → classify with Claude, ask clarifying questions
  B) Replies to clarifying questions → collect answers, route to correct agent(s)

Also handles on-site customer approval flow: tech identifies extra work,
system texts customer an estimate, 10-min expiry, then hand off to follow-up.

Usage:
    from execution.clarification_agent import handle
    handle(client, employee, raw_input, from_number)
"""

import os
import sys
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_clarification import (
    get_pending, create_pending, update_pending, delete_pending,
    create_approval,
)
from execution.call_claude import call_claude
from execution.sms_send import send_sms
from execution.db_agent_activity import log_activity


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Intent label map
# ---------------------------------------------------------------------------
INTENT_LABELS = {
    "estimate": "an estimate",
    "schedule": "scheduling a job",
    "completion": "an invoice (job complete)",
    "both": "estimate + scheduling",
}

INTENT_PARSE_MAP = {
    "1": "estimate",
    "2": "schedule",
    "3": "completion",
    "4": "both",
    "estimate": "estimate",
    "quote": "estimate",
    "schedule": "schedule",
    "book": "schedule",
    "done": "completion",
    "complete": "completion",
    "finished": "completion",
    "invoice": "completion",
    "all": "both",
    "everything": "both",
}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def handle(client: dict, employee: dict, raw_input: str, from_number: str) -> None:
    """
    Main entry point for the clarification agent.

    Args:
        client:      Full client row from clients table
        employee:    Employee dict (or synthetic dict with name/role)
        raw_input:   The raw SMS body
        from_number: The sender's phone number
    """
    try:
        client_id = client["id"]
        client_phone = client.get("phone", "")

        # Check if there's already a pending clarification for this sender
        pending = get_pending(client_id, from_number)

        if pending:
            _handle_reply(client, employee, raw_input, from_number, pending)
        else:
            _handle_new(client, employee, raw_input, from_number)

    except Exception as e:
        print(f"[{timestamp()}] ERROR clarification_agent: handle failed — {e}")


# ---------------------------------------------------------------------------
# Handle new ambiguous message
# ---------------------------------------------------------------------------

def _handle_new(client: dict, employee: dict, raw_input: str, from_number: str) -> None:
    """Classify the message with Claude and either route directly or ask questions."""
    client_id = client["id"]
    client_phone = client.get("phone", "")
    trade = client.get("trade_vertical", "septic and drain")
    biz_name = client.get("business_name", "the company")

    # Call Claude to classify the intent
    system_prompt = (
        f"You are a dispatcher for {biz_name}, a {trade} company. "
        f"Classify this field tech message. Respond ONLY with valid JSON."
    )
    user_prompt = (
        f"Message: '{raw_input}'\n\n"
        f"Respond with JSON only:\n"
        f'{{"intent": "estimate|schedule|completion|both|unclear", '
        f'"has_address": true/false, '
        f'"has_customer_name": true/false, '
        f'"customer_name": "extracted name or null", '
        f'"address": "extracted address or null", '
        f'"scope": "brief description of work mentioned", '
        f'"confidence": "high|medium|low"}}'
    )

    response = call_claude(system_prompt, user_prompt, model="haiku")
    if not response:
        print(f"[{timestamp()}] ERROR clarification_agent: Claude returned no response")
        send_sms(
            to_number=from_number,
            message_body="I didn't catch that. Text ESTIMATE, SCHEDULE, or DONE followed by the job details.",
            from_number=client_phone,
        )
        return

    # Parse JSON — strip markdown fences if Claude adds them
    parsed = _parse_json(response)
    if not parsed:
        print(f"[{timestamp()}] WARN clarification_agent: Failed to parse Claude response: {response[:200]}")
        send_sms(
            to_number=from_number,
            message_body="I didn't catch that. Text ESTIMATE, SCHEDULE, or DONE followed by the job details.",
            from_number=client_phone,
        )
        return

    intent = parsed.get("intent", "unclear")
    confidence = parsed.get("confidence", "low")
    has_address = parsed.get("has_address", False)
    customer_name = parsed.get("customer_name")
    address = parsed.get("address")
    scope = parsed.get("scope", "")

    print(
        f"[{timestamp()}] INFO clarification_agent: Classified → "
        f"intent={intent} confidence={confidence} has_address={has_address} "
        f"customer={customer_name}"
    )

    # High confidence + has address + clear intent → route directly
    if confidence == "high" and has_address and intent not in ("unclear",):
        _route_to_agents(client, employee, parsed, raw_input, from_number)
        return

    # Unclear intent or low confidence → ask about intent (Stage 1)
    if intent == "unclear" or confidence == "low":
        create_pending(
            client_id=client_id,
            employee_phone=from_number,
            original_message=raw_input,
            stage=1,
            collected_scope=scope,
            collected_customer_name=customer_name,
        )
        send_sms(
            to_number=from_number,
            message_body=(
                "Got it. Quick question — is this for:\n"
                "1) An estimate\n"
                "2) Schedule a job\n"
                "3) Job's done, start invoice\n"
                "4) All of the above\n"
                "Reply 1, 2, 3, or 4"
            ),
            from_number=client_phone,
        )
        print(f"[{timestamp()}] INFO clarification_agent: Asked intent question (Stage 1)")
        return

    # Clear intent but no address → skip to address question (Stage 2)
    if not has_address:
        name_label = customer_name or "this job"
        intent_label = INTENT_LABELS.get(intent, intent)
        create_pending(
            client_id=client_id,
            employee_phone=from_number,
            original_message=raw_input,
            stage=2,
            collected_intent=intent,
            collected_customer_name=customer_name,
            collected_scope=scope,
        )
        send_sms(
            to_number=from_number,
            message_body=f"Got it — {intent_label} for {name_label}. What's the site address?",
            from_number=client_phone,
        )
        print(f"[{timestamp()}] INFO clarification_agent: Asked address question (Stage 2)")
        return

    # Clear intent + has address → route directly
    _route_to_agents(client, employee, parsed, raw_input, from_number)


# ---------------------------------------------------------------------------
# Handle reply to a pending clarification
# ---------------------------------------------------------------------------

def _handle_reply(client: dict, employee: dict, raw_input: str, from_number: str, pending: dict) -> None:
    """Process a reply to an active clarification question."""
    client_phone = client.get("phone", "")
    stage = pending.get("stage", 1)
    pending_id = pending["id"]

    if stage == 1:
        # Waiting for intent choice (1/2/3/4 or natural language)
        parsed_intent = _parse_intent_reply(raw_input)
        if not parsed_intent:
            send_sms(
                to_number=from_number,
                message_body="Just reply 1, 2, 3, or 4",
                from_number=client_phone,
            )
            return

        # Update and move to Stage 2: ask for address
        update_pending(pending_id, stage=2, collected_intent=parsed_intent)
        name_label = pending.get("collected_customer_name") or "this job"
        send_sms(
            to_number=from_number,
            message_body=f"Got it. What's the address for {name_label}?",
            from_number=client_phone,
        )
        print(f"[{timestamp()}] INFO clarification_agent: Intent collected → {parsed_intent}, asked for address (Stage 2)")

    elif stage == 2:
        # Waiting for address — treat entire reply as the address
        update_pending(pending_id, collected_address=raw_input)

        # Clarification complete — build context and route
        final_context = {
            "intent": pending.get("collected_intent", "estimate"),
            "customer_name": pending.get("collected_customer_name"),
            "address": raw_input,
            "scope": pending.get("collected_scope", ""),
            "has_address": True,
            "has_customer_name": bool(pending.get("collected_customer_name")),
        }
        full_input = (pending.get("original_message", "") + " " + raw_input).strip()

        # Delete the pending record — clarification is done
        delete_pending(pending_id)

        print(f"[{timestamp()}] INFO clarification_agent: Clarification complete → routing to {final_context['intent']}")
        _route_to_agents(client, employee, final_context, full_input, from_number)

    else:
        # Unknown stage — clean up
        print(f"[{timestamp()}] WARN clarification_agent: Unknown stage {stage} — deleting pending")
        delete_pending(pending_id)


# ---------------------------------------------------------------------------
# Route to the correct agent(s) based on resolved intent
# ---------------------------------------------------------------------------

def _route_to_agents(client: dict, employee: dict, context: dict, full_input: str, from_number: str) -> None:
    """
    Route to one or more agents based on the resolved intent.

    Args:
        client:     Full client row
        employee:   Employee dict
        context:    Parsed classification dict with intent, address, scope, etc.
        full_input: Combined raw text to pass to the agent
        from_number: Sender phone
    """
    intent = context.get("intent", "estimate")
    client_phone = client.get("phone", "")

    try:
        if intent == "estimate":
            from execution.proposal_agent import run as proposal_run
            proposal_run(client_phone=client_phone, customer_phone=from_number, raw_input=full_input)
            print(f"[{timestamp()}] INFO clarification_agent: Routed to → proposal_agent")

        elif intent == "schedule":
            from execution.scheduling_agent import handle_scheduling
            handle_scheduling(client=client, employee=employee, raw_input=full_input, from_number=from_number)
            print(f"[{timestamp()}] INFO clarification_agent: Routed to → scheduling_agent")

        elif intent == "completion":
            from execution.invoice_agent import run as invoice_run
            invoice_run(client_phone=client_phone, customer_phone=from_number, raw_input=full_input)
            print(f"[{timestamp()}] INFO clarification_agent: Routed to → invoice_agent")

        elif intent == "both":
            # Run estimate first, then schedule
            from execution.proposal_agent import run as proposal_run
            proposal_run(client_phone=client_phone, customer_phone=from_number, raw_input=full_input)
            print(f"[{timestamp()}] INFO clarification_agent: Routed to → proposal_agent (both)")

            from execution.scheduling_agent import handle_scheduling
            handle_scheduling(client=client, employee=employee, raw_input=full_input, from_number=from_number)
            print(f"[{timestamp()}] INFO clarification_agent: Routed to → scheduling_agent (both)")

        else:
            # Unclear — shouldn't reach here but handle gracefully
            send_sms(
                to_number=from_number,
                message_body="I didn't catch that. Text ESTIMATE, SCHEDULE, or DONE followed by the job details.",
                from_number=client_phone,
            )
            print(f"[{timestamp()}] WARN clarification_agent: Unclear intent after routing — sent help text")

        # Log activity
        log_activity(
            client_phone=client_phone,
            agent_name="clarification_agent",
            action_taken=f"routed_{intent}",
            input_summary=full_input[:120],
            output_summary=f"Routed {context.get('customer_name') or 'request'} to {intent}",
            sms_sent=True,
        )

    except Exception as e:
        print(f"[{timestamp()}] ERROR clarification_agent: _route_to_agents failed — {e}")


# ---------------------------------------------------------------------------
# On-site customer approval flow
# ---------------------------------------------------------------------------

def on_site_approval_flow(
    client: dict,
    employee: dict,
    job_id: str,
    proposal_id: str,
    customer: dict,
    estimate_amount: float,
    scope: str = "",
) -> None:
    """
    Send an on-site estimate to the customer for immediate approval.

    Called when a tech is on site and additional work is identified.
    Customer gets 10 minutes to reply YES/NO. If no reply, the approval
    expires and the follow-up agent chases later via cron.

    Args:
        client:          Full client row
        employee:        Employee dict (the tech on site)
        job_id:          UUID of the job
        proposal_id:     UUID of the proposal
        customer:        Full customer row
        estimate_amount: Dollar amount for the additional work
        scope:           Description of the additional work
    """
    client_phone = client.get("phone", "")
    biz_name = client.get("business_name", "Your service provider")
    tech_phone = employee.get("phone", "")
    customer_phone = customer.get("customer_phone", "")
    customer_name = customer.get("customer_name", "Customer")

    # No customer phone → tell the tech to get approval in person
    if not customer_phone:
        send_sms(
            to_number=tech_phone,
            message_body=f"No phone on file for {customer_name} — get approval in person",
            from_number=client_phone,
        )
        print(f"[{timestamp()}] WARN clarification_agent: No customer phone for {customer_name} — skipping approval SMS")
        return

    # HARD RULE #2 — check SMS opt-in before texting customer
    if not customer.get("sms_consent"):
        send_sms(
            to_number=tech_phone,
            message_body=(
                f"{customer_name} is not opted in to SMS. "
                f"Get approval in person or call them directly."
            ),
            from_number=client_phone,
        )
        print(
            f"[{timestamp()}] WARNING clarification_agent: Customer not opted in — "
            f"on-site approval blocked | customer={customer.get('id')}"
        )
        try:
            log_activity(
                client_phone=client_phone,
                agent_name="clarification_agent",
                action_taken="sms_blocked_no_optin",
                input_summary=f"on_site_approval job={job_id}",
                output_summary=f"{customer_name} not opted in — approval blocked",
                sms_sent=False,
            )
        except Exception:
            pass
        return

    # SMS the customer with the estimate
    customer_msg = (
        f"{biz_name} — your tech is on site now.\n\n"
        f"Additional work identified: {scope or 'additional service'}\n"
        f"Estimate: ${estimate_amount:.2f}\n\n"
        f"Reply YES to approve and we'll get it scheduled.\n"
        f"Reply NO to decline.\n\n"
        f"This offer expires in 10 minutes."
    )
    send_sms(to_number=customer_phone, message_body=customer_msg, from_number=client_phone)

    # Create the approval record
    create_approval(
        client_id=client["id"],
        customer_id=customer.get("id", ""),
        job_id=job_id,
        proposal_id=proposal_id,
        tech_phone=tech_phone,
        customer_phone=customer_phone,
        estimate_amount=estimate_amount,
    )

    # SMS the tech
    send_sms(
        to_number=tech_phone,
        message_body=(
            f"Estimate sent to {customer_name}. You'll hear back in 10 min. "
            f"Safe to wrap up — we'll handle scheduling if they approve."
        ),
        from_number=client_phone,
    )

    print(
        f"[{timestamp()}] INFO clarification_agent: On-site approval sent | "
        f"job={job_id} | customer={customer.get('id')} | amount=${estimate_amount}"
    )

    log_activity(
        client_phone=client_phone,
        agent_name="clarification_agent",
        action_taken="on_site_approval_sent",
        input_summary=f"job={job_id} scope={scope[:80]}",
        output_summary=f"On-site approval sent to {customer_name} — ${estimate_amount:.2f}",
        sms_sent=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json(text: str) -> dict | None:
    """Parse JSON from Claude response, stripping markdown fences if present."""
    text = text.strip()
    # Strip ```json ... ``` fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines if they are fences
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _parse_intent_reply(text: str) -> str | None:
    """
    Parse an intent reply from the employee.
    Accepts: '1', '2', '3', '4', or natural language like 'estimate', 'done'.

    Returns:
        Intent string or None if unrecognized.
    """
    cleaned = text.strip().lower().rstrip(".")
    return INTENT_PARSE_MAP.get(cleaned)
