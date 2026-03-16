"""
db_messages.py — Messages table queries

Every SMS in and out gets logged here. This is the full conversation
history. Agents use it to understand context before responding.

Usage:
    from execution.db_messages import log_message, get_conversation_history
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_message(
    client_id: str,
    direction: str,
    from_number: str,
    to_number: str,
    body: str,
    agent_used: str = None,
    job_id: str = None,
    telnyx_message_id: str = None,
) -> str | None:
    """
    Log an SMS to the messages table.
    Called on every inbound message (sms_receive.py) and every outbound
    message (sms_send.py) so we have a complete conversation history.

    Args:
        client_id:         UUID of the client this message belongs to
        direction:         'inbound' or 'outbound'
        from_number:       Sender E.164 phone number
        to_number:         Recipient E.164 phone number
        body:              Message text
        agent_used:        Name of the agent that generated this (outbound only)
        job_id:            UUID of a related job (optional)
        telnyx_message_id: Telnyx message ID for deduplication (optional)

    Returns:
        New message UUID as a string, or None on failure.
    """
    try:
        supabase = get_client()
        record = {
            "client_id":          client_id,
            "direction":          direction,
            "from_number":        from_number,
            "to_number":          to_number,
            "body":               body,
            "agent_used":         agent_used,
            "job_id":             job_id,
            "telnyx_message_id":  telnyx_message_id,
        }
        result = supabase.table("messages").insert(record).execute()
        message_id = result.data[0]["id"]
        print(f"[{timestamp()}] INFO db_messages: Logged {direction} message id={message_id}")
        return message_id

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_messages: log_message failed — {e}")
        return None


def get_conversation_history(
    client_id: str,
    customer_phone: str,
    limit: int = 10,
) -> list:
    """
    Return the last N messages between a client and a customer phone number.
    Agents use this as context before generating a reply — prevents
    repeating information or missing context from earlier in the thread.

    Args:
        client_id:      UUID of the client
        customer_phone: Customer's E.164 phone number
        limit:          Max number of messages to return (default 10)

    Returns:
        List of message dicts ordered oldest-first (ready to feed into
        an AI context window), or empty list on error.
    """
    try:
        supabase = get_client()

        # Get messages where the customer was either sender or recipient
        result = (
            supabase.table("messages")
            .select("*")
            .eq("client_id", client_id)
            .or_(f"from_number.eq.{customer_phone},to_number.eq.{customer_phone}")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

        # Reverse so oldest message is first (chronological for AI context)
        messages = list(reversed(result.data or []))
        print(f"[{timestamp()}] INFO db_messages: get_conversation_history → {len(messages)} messages")
        return messages

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_messages: get_conversation_history failed — {e}")
        return []
