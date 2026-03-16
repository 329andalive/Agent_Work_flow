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


def cancel_followups_for_job(job_id: str) -> int:
    """
    Cancel all pending follow-ups for a job.
    Called immediately when a proposal is accepted or declined so we
    stop chasing a resolved opportunity.

    Args:
        job_id: UUID of the job whose follow-ups should be cancelled

    Returns:
        Number of follow-ups cancelled, or 0 on failure.
    """
    try:
        supabase = get_client()
        result = (
            supabase.table("follow_ups")
            .update({"status": "cancelled"})
            .eq("job_id", job_id)
            .eq("status", "pending")
            .execute()
        )
        count = len(result.data) if result.data else 0
        print(f"[{timestamp()}] INFO db_followups: Cancelled {count} follow-ups for job_id={job_id}")
        return count

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_followups: cancel_followups_for_job failed — {e}")
        return 0


def get_pending_followups_by_type(client_id: str, followup_type: str) -> list:
    """
    Return pending follow-ups of a specific type for a client.
    Used by sms_router to check if there's an outstanding 'lost_job_why'
    question before routing an inbound message.

    Args:
        client_id:     UUID of the client
        followup_type: e.g. "lost_job_why"

    Returns:
        List of follow-up dicts, or empty list on error.
    """
    try:
        supabase = get_client()
        result = (
            supabase.table("follow_ups")
            .select("*")
            .eq("client_id", client_id)
            .eq("follow_up_type", followup_type)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_followups: get_pending_followups_by_type failed — {e}")
        return []


def count_followups_sent_for_proposal(proposal_id: str) -> int:
    """
    Count how many follow-up messages have already been sent for a proposal.
    Prevents sending more than 3 touches total.

    Args:
        proposal_id: UUID of the proposal

    Returns:
        Count of sent follow-ups, or 0 on failure.
    """
    try:
        supabase = get_client()
        result = (
            supabase.table("follow_ups")
            .select("id")
            .eq("proposal_id", proposal_id)
            .eq("status", "sent")
            .execute()
        )
        return len(result.data) if result.data else 0

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_followups: count_followups_sent_for_proposal failed — {e}")
        return 0
