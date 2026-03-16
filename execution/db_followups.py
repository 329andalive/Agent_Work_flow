"""
db_followups.py — Follow-ups table queries

The follow-up queue. The followup_agent reads this table on a schedule
to know what to send and when. Covers estimate chasers, payment reminders,
and seasonal reminders (e.g. "spring pump season is coming").

Usage:
    from execution.db_followups import schedule_followup, get_due_followups
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def schedule_followup(
    client_id: str,
    customer_id: str,
    job_id: str,
    proposal_id: str,
    followup_type: str,
    scheduled_for: str,
) -> str | None:
    """
    Create a follow-up record in the queue.
    Called after sending a proposal or invoice to schedule the next touch.

    Args:
        client_id:     UUID of the client (business owner)
        customer_id:   UUID of the end customer
        job_id:        UUID of the related job
        proposal_id:   UUID of the related proposal (can be None for payment/seasonal)
        followup_type: 'estimate_followup', 'payment_chase', or 'seasonal_reminder'
        scheduled_for: ISO 8601 datetime string of when to send

    Returns:
        New follow-up UUID as a string, or None on failure.
    """
    try:
        supabase = get_client()
        record = {
            "client_id":     client_id,
            "customer_id":   customer_id,
            "job_id":        job_id,
            "proposal_id":   proposal_id,
            "follow_up_type": followup_type,
            "scheduled_for": scheduled_for,
            "status":        "pending",
        }
        result = supabase.table("follow_ups").insert(record).execute()
        followup_id = result.data[0]["id"]
        print(f"[{timestamp()}] INFO db_followups: Scheduled {followup_type} id={followup_id} for {scheduled_for}")
        return followup_id

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_followups: schedule_followup failed — {e}")
        return None


def get_due_followups() -> list:
    """
    Return all pending follow-ups that are due right now (scheduled_for <= now).
    The followup_agent calls this on a schedule to find work to do.

    Returns:
        List of follow-up dicts ready to be sent, or empty list on error.
    """
    try:
        supabase = get_client()
        now = datetime.now(timezone.utc).isoformat()

        result = (
            supabase.table("follow_ups")
            .select("*")
            .eq("status", "pending")
            .lte("scheduled_for", now)
            .order("scheduled_for")
            .execute()
        )
        print(f"[{timestamp()}] INFO db_followups: get_due_followups → {len(result.data)} due")
        return result.data or []

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_followups: get_due_followups failed — {e}")
        return []


def mark_followup_sent(followup_id: str, message_sent: str) -> bool:
    """
    Mark a follow-up as sent and record the message that was sent.
    Called by the followup_agent immediately after sending the SMS.

    Args:
        followup_id:  UUID of the follow-up record
        message_sent: The exact message text that was sent

    Returns:
        True on success, False on failure.
    """
    try:
        supabase = get_client()
        now = datetime.now(timezone.utc).isoformat()
        supabase.table("follow_ups").update({
            "status":       "sent",
            "sent_at":      now,
            "message_sent": message_sent,
        }).eq("id", followup_id).execute()
        print(f"[{timestamp()}] INFO db_followups: Marked followup {followup_id} as sent")
        return True

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_followups: mark_followup_sent failed — {e}")
        return False
