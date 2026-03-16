"""
sms_router.py — Routes inbound SMS to the correct AI agent

Flow:
    1. Receive parsed SMS data from sms_receive.py
    2. Look up the client by phone number (Supabase — stubbed for now)
    3. Scan message body for routing keywords
    4. Return the name of the agent that should handle this message

Usage:
    from execution.sms_router import route_message
    agent = route_message(sms_data)
"""

from datetime import datetime


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


def lookup_client(phone_number: str) -> dict:
    """
    Look up a client record by phone number.

    STUB — replace with real Supabase query when ready:
        from supabase import create_client
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        result = supabase.table("clients").select("*").eq("phone", phone_number).single().execute()
        return result.data

    Returns a mock client object for now.
    """
    print(f"[{timestamp()}] INFO sms_router: Looking up client for {phone_number} (stub — returning mock)")
    return {
        "id": "mock-client-001",
        "name": "Mock Client",
        "phone": phone_number,
        "status": "active",
    }


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

        # Step 1: Look up the client (stubbed — will hit Supabase later)
        client = lookup_client(from_number)
        print(f"[{timestamp()}] INFO sms_router: Client resolved → id={client['id']} name={client['name']}")

        # Step 2: Determine the right agent based on message content
        agent = detect_agent(body)
        print(f"[{timestamp()}] INFO sms_router: Routed to → {agent} (body snippet: '{body[:60]}')")

        return agent

    except Exception as e:
        print(f"[{timestamp()}] ERROR sms_router: Unexpected error during routing — {e}")
        # Default to proposal_agent so the message still gets handled
        return DEFAULT_AGENT
