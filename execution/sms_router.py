"""
sms_router.py — Routes inbound SMS to the correct AI agent

Flow:
    1. Receive parsed SMS data from sms_receive.py
    2. Look up the client by phone number via db_client.py (real Supabase)
    3. Scan message body for routing keywords
    4. Return the name of the agent that should handle this message

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
    "proposal_agent":  ["quote", "estimate", "proposal", "pricing", "price", "cost", "bid"],
    "invoice_agent":   ["invoice", "bill", "payment", "pay", "receipt", "balance", "owe"],
    "followup_agent":  ["follow up", "followup", "follow-up", "check in", "checking in", "update", "status"],
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


def route_message(sms_data: dict) -> str:
    """
    Main entry point. Takes parsed SMS data and returns the agent name to handle it.

    Args:
        sms_data: dict with keys: from_number, to_number, body, message_id

    Returns:
        Agent name as a string (e.g. "proposal_agent")
    """
    try:
        from_number = sms_data.get("from_number", "unknown")
        body = sms_data.get("body", "")
        message_id = sms_data.get("message_id", "unknown")

        print(f"[{timestamp()}] INFO sms_router: Routing message_id={message_id} from={from_number}")

        # Step 1: Look up the client in Supabase by their phone number
        client = lookup_client(from_number)
        if not client:
            print(f"[{timestamp()}] WARN sms_router: Unknown number {from_number} — no client found")
            return DEFAULT_AGENT
        print(f"[{timestamp()}] INFO sms_router: Client resolved → id={client['id']} name={client['business_name']}")

        # Step 2: Determine the right agent based on message content
        agent = detect_agent(body)
        print(f"[{timestamp()}] INFO sms_router: Routed to → {agent} (body snippet: '{body[:60]}')")

        return agent

    except Exception as e:
        print(f"[{timestamp()}] ERROR sms_router: Unexpected error during routing — {e}")
        # Default to proposal_agent so the message still gets handled
        return DEFAULT_AGENT
