"""
sms_router.py — Routes inbound SMS to the correct AI agent

Flow:
    1. Receive parsed SMS data from sms_receive.py
    2. Look up the client by phone number via db_client.py (real Supabase)
    3. Priority-check for follow-up response intent (loss_reason, accepted, declined, lost_report)
    4. Scan message body for routing keywords
    5. Dispatch to the correct agent and run it
    6. Return the agent name that handled the message

Priority order for inbound messages:
    1. loss_reason  — owner answering "why did you lose it" (check pending lost_job_why first)
    2. accepted     — customer confirming a proposal
    3. declined     — customer declining a proposal
    4. lost_report  — owner proactively reporting a loss
    5. invoice_agent — owner reporting a job completion
    6. proposal_agent — new job request (default)

Usage:
    from execution.sms_router import route_message
    agent = route_message(sms_data)
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_client import get_client_by_phone
from execution.response_detector import detect_response_type
from execution.db_followups import get_pending_followups_by_type


def timestamp():
    """Return a formatted timestamp string for log lines."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Keyword routing table — only used when priority detection doesn't match
# ---------------------------------------------------------------------------
ROUTING_TABLE = {
    # Invoice agent — owner texting in that a job is done
    "invoice_agent": [
        "all done", "job done", "just finished", "we're done", "just wrapped",
        "wrapped up", "took me", "billed", "bill them", "send invoice",
        "send the invoice", "finished up", "worked", "spent",
        "3 hours", "2 hours", "4 hours", "5 hours", "1 hour", "half a day",
    ],
    # Proposal agent — new job request keywords
    "proposal_agent": ["quote", "estimate", "proposal", "pricing", "price", "cost", "bid"],
    # Review request
    "review_agent": ["review", "google", "feedback", "rating", "stars", "yelp"],
}

DEFAULT_AGENT = "proposal_agent"


def lookup_client(phone_number: str) -> dict | None:
    """
    Look up a client record by phone number via Supabase.
    Returns the full client record, or None if the number isn't registered.
    """
    return get_client_by_phone(phone_number)


def detect_agent(message_body: str) -> str:
    """
    Keyword-only routing fallback. Used when priority detection doesn't match.
    Falls back to DEFAULT_AGENT if no keywords match.
    """
    body_lower = message_body.lower()

    for agent_name, keywords in ROUTING_TABLE.items():
        for keyword in keywords:
            if keyword in body_lower:
                return agent_name

    return DEFAULT_AGENT


def dispatch(agent_name: str, sms_data: dict, **kwargs) -> None:
    """
    Call the correct agent with the parsed SMS data.

    Args:
        agent_name: Name of agent to run
        sms_data:   Parsed SMS dict with from_number, to_number, body, message_id
        **kwargs:   Extra args passed to specific agents (e.g. response_type)
    """
    from_number = sms_data.get("from_number")
    to_number   = sms_data.get("to_number")
    body        = sms_data.get("body", "")

    try:
        if agent_name == "proposal_agent":
            from execution.proposal_agent import run as proposal_run
            proposal_run(
                client_phone=to_number,
                customer_phone=from_number,
                raw_input=body,
            )

        elif agent_name == "invoice_agent":
            from execution.invoice_agent import run as invoice_run
            invoice_run(
                client_phone=to_number,
                customer_phone=from_number,
                raw_input=body,
            )

        elif agent_name == "proposal_response":
            from execution.followup_agent import handle_proposal_response
            handle_proposal_response(
                client_phone=to_number,
                customer_phone=from_number,
                response_type=kwargs.get("response_type", "declined"),
            )

        elif agent_name == "loss_reason":
            from execution.followup_agent import handle_loss_reason
            handle_loss_reason(
                client_phone=to_number,
                customer_phone=from_number,
                raw_input=body,
            )

        elif agent_name == "lost_report":
            from execution.followup_agent import handle_lost_report
            handle_lost_report(
                client_phone=to_number,
                owner_phone=from_number,
            )

        elif agent_name == "review_agent":
            # Stub
            print(f"[{timestamp()}] INFO sms_router: review_agent not yet implemented")

        else:
            print(f"[{timestamp()}] WARN sms_router: Unknown agent '{agent_name}' — no dispatch")

    except Exception as e:
        print(f"[{timestamp()}] ERROR sms_router: dispatch to {agent_name} failed — {e}")


def route_message(sms_data: dict) -> str:
    """
    Main entry point. Priority-detects intent, then falls back to keyword routing.
    Dispatches to the correct agent and returns the agent name.

    Args:
        sms_data: dict with keys: from_number, to_number, body, message_id

    Returns:
        Agent name as a string (e.g. "proposal_agent")
    """
    try:
        from_number = sms_data.get("from_number", "unknown")
        to_number   = sms_data.get("to_number", "unknown")
        body        = sms_data.get("body", "")
        message_id  = sms_data.get("message_id", "unknown")

        print(f"[{timestamp()}] INFO sms_router: Routing message_id={message_id} from={from_number}")

        # Step 1: Look up the client by their Telnyx number (to_number)
        client = lookup_client(to_number)
        if not client:
            print(f"[{timestamp()}] WARN sms_router: No client found for Telnyx number {to_number}")
            return DEFAULT_AGENT
        print(f"[{timestamp()}] INFO sms_router: Client resolved → {client['business_name']}")

        client_id = client["id"]

        # Step 2: Priority detection — check intent before keyword routing
        intent = detect_response_type(body)

        # Priority 1: Owner answering loss reason — check if a lost_job_why is pending
        if intent == "loss_reason":
            pending = get_pending_followups_by_type(client_id, "lost_job_why")
            if pending:
                print(f"[{timestamp()}] INFO sms_router: Routed to → loss_reason (pending lost_job_why found)")
                dispatch("loss_reason", sms_data)
                return "loss_reason"
            # If no pending why question, fall through to keyword routing
            print(f"[{timestamp()}] INFO sms_router: loss_reason detected but no pending why question — falling through")

        # Priority 2: Customer accepting a proposal
        elif intent == "accepted":
            print(f"[{timestamp()}] INFO sms_router: Routed to → proposal_response (accepted)")
            dispatch("proposal_response", sms_data, response_type="accepted")
            return "proposal_response"

        # Priority 3: Customer declining a proposal
        elif intent == "declined":
            print(f"[{timestamp()}] INFO sms_router: Routed to → proposal_response (declined)")
            dispatch("proposal_response", sms_data, response_type="declined")
            return "proposal_response"

        # Priority 4: Owner proactively reporting a loss
        elif intent == "lost_report":
            print(f"[{timestamp()}] INFO sms_router: Routed to → lost_report")
            dispatch("lost_report", sms_data)
            return "lost_report"

        # Step 3: Keyword routing fallback
        agent = detect_agent(body)
        print(f"[{timestamp()}] INFO sms_router: Routed to → {agent} (body: '{body[:60]}')")
        dispatch(agent, sms_data)
        return agent

    except Exception as e:
        print(f"[{timestamp()}] ERROR sms_router: Unexpected error during routing — {e}")
        return DEFAULT_AGENT
