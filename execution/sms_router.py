"""
sms_router.py — Routes inbound SMS to the correct AI agent

Flow:
    1. Receive parsed SMS data from sms_receive.py
    2. Look up the client by phone number via db_client.py (real Supabase)
    3. Scan message body for routing keywords
    4. Dispatch to the correct agent and run it
    5. Return the agent name that handled the message

Usage:
    from execution.sms_router import route_message
    agent = route_message(sms_data)
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_client import get_client_by_phone


def timestamp():
    """Return a formatted timestamp string for log lines."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Keyword routing table
# Each entry maps an agent name to a list of trigger keywords.
# Keywords are matched case-insensitively against the message body.
# The first matching agent wins. Default fallback is proposal_agent.
# ---------------------------------------------------------------------------
ROUTING_TABLE = {
    # Invoice agent — owner texting in that a job is done
    # These keywords take priority over proposal keywords
    "invoice_agent":  [
        "done", "finished", "complete", "completed", "all done",
        "wrapped up", "job done", "just finished", "took me",
        "hours", "billed", "bill them", "send invoice",
        "worked", "spent", "3 hours", "2 hours", "4 hours", "1 hour",
    ],
    # Proposal agent — new job request keywords
    "proposal_agent":  ["quote", "estimate", "proposal", "pricing", "price", "cost", "bid"],
    # Payment / account questions
    "followup_agent":  ["follow up", "followup", "follow-up", "check in", "checking in", "update", "status"],
    # Review request
    "review_agent":    ["review", "google", "feedback", "rating", "stars", "yelp"],
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
    Scan the message body for routing keywords and return the matching agent name.
    Matching is case-insensitive. First match wins.
    Falls back to DEFAULT_AGENT if no keywords match.
    """
    body_lower = message_body.lower()

    for agent_name, keywords in ROUTING_TABLE.items():
        for keyword in keywords:
            if keyword in body_lower:
                return agent_name

    return DEFAULT_AGENT


def dispatch(agent_name: str, sms_data: dict) -> None:
    """
    Call the correct agent with the parsed SMS data.
    Each agent is imported here to avoid circular imports and to keep
    agents decoupled from the router until they're actually needed.

    Args:
        agent_name: Name of agent to run (e.g. "proposal_agent")
        sms_data:   Parsed SMS dict with from_number, to_number, body, message_id
    """
    from_number = sms_data.get("from_number")
    to_number   = sms_data.get("to_number")
    body        = sms_data.get("body", "")

    try:
        if agent_name == "proposal_agent":
            from execution.proposal_agent import run as proposal_run
            # client_phone = the Telnyx number that received the text (to_number)
            # customer_phone = the person who sent the text (from_number)
            proposal_run(
                client_phone=to_number,
                customer_phone=from_number,
                raw_input=body,
            )

        elif agent_name == "invoice_agent":
            from execution.invoice_agent import run as invoice_run
            # client_phone = the Telnyx number that received the text (to_number)
            # customer_phone = the sender (from_number) — may be owner's personal phone
            invoice_run(
                client_phone=to_number,
                customer_phone=from_number,
                raw_input=body,
            )

        elif agent_name == "followup_agent":
            # Stub — build followup_agent next
            print(f"[{timestamp()}] INFO sms_router: followup_agent not yet implemented")

        elif agent_name == "review_agent":
            # Stub — build review_agent next
            print(f"[{timestamp()}] INFO sms_router: review_agent not yet implemented")

        else:
            print(f"[{timestamp()}] WARN sms_router: Unknown agent '{agent_name}' — no dispatch")

    except Exception as e:
        print(f"[{timestamp()}] ERROR sms_router: dispatch to {agent_name} failed — {e}")


def route_message(sms_data: dict) -> str:
    """
    Main entry point. Identifies the right agent, dispatches the message to it,
    and returns the agent name.

    Args:
        sms_data: dict with keys: from_number, to_number, body, message_id

    Returns:
        Agent name as a string (e.g. "proposal_agent")
    """
    try:
        from_number = sms_data.get("from_number", "unknown")
        body        = sms_data.get("body", "")
        message_id  = sms_data.get("message_id", "unknown")

        print(f"[{timestamp()}] INFO sms_router: Routing message_id={message_id} from={from_number}")

        # Step 1: Look up the client in Supabase by their Telnyx number (to_number)
        to_number = sms_data.get("to_number", "unknown")
        client = lookup_client(to_number)
        if not client:
            print(f"[{timestamp()}] WARN sms_router: No client found for Telnyx number {to_number}")
            return DEFAULT_AGENT
        print(f"[{timestamp()}] INFO sms_router: Client resolved → {client['business_name']}")

        # Step 2: Determine the right agent based on message content
        agent = detect_agent(body)
        print(f"[{timestamp()}] INFO sms_router: Routed to → {agent} (body: '{body[:60]}')")

        # Step 3: Dispatch to the agent
        dispatch(agent, sms_data)

        return agent

    except Exception as e:
        print(f"[{timestamp()}] ERROR sms_router: Unexpected error during routing — {e}")
        return DEFAULT_AGENT
