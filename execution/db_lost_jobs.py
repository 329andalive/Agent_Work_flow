"""
db_lost_jobs.py — Lost jobs and proposal outcomes table queries

Tracks every job that wasn't won and powers the monthly closing rate report.

Usage:
    from execution.db_lost_jobs import save_lost_job, update_monthly_outcomes
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def save_lost_job(
    client_id: str,
    customer_id: str,
    job_id: str,
    proposal_id: str,
    proposal_amount: float,
    lost_reason: str = "unknown",
    lost_reason_detail: str = None,
    competitor_mentioned: str = None,
) -> str | None:
    """
    Record a lost job. Called when a proposal is declined or goes cold.

    Args:
        client_id:            UUID of the client
        customer_id:          UUID of the end customer
        job_id:               UUID of the job
        proposal_id:          UUID of the proposal
        proposal_amount:      Dollar amount that was quoted
        lost_reason:          Reason code: price/timing/competition/relationship/unknown
        lost_reason_detail:   Owner's free-text explanation
        competitor_mentioned: Any competitor name if the owner mentioned one

    Returns:
        New lost_job UUID, or None on failure.
    """
    try:
        supabase = get_client()
        record = {
            "client_id":            client_id,
            "customer_id":          customer_id,
            "job_id":               job_id,
            "proposal_id":          proposal_id,
            "proposal_amount":      proposal_amount,
            "lost_reason":          lost_reason,
            "lost_reason_detail":   lost_reason_detail,
            "competitor_mentioned": competitor_mentioned,
        }
        result = supabase.table("lost_jobs").insert(record).execute()
        lost_id = result.data[0]["id"]
        print(f"[{timestamp()}] INFO db_lost_jobs: Saved lost_job id={lost_id} reason={lost_reason} amount=${proposal_amount}")
        return lost_id

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_lost_jobs: save_lost_job failed — {e}")
        return None


def update_lost_job_reason(
    proposal_id: str,
    lost_reason: str,
    lost_reason_detail: str = None,
) -> bool:
    """
    Update the reason on an existing lost_job record.
    Called when the owner replies to the "why did you lose it" question.

    Args:
        proposal_id:        UUID of the proposal (FK to find the lost_job)
        lost_reason:        Reason code
        lost_reason_detail: Owner's own words

    Returns:
        True on success, False on failure.
    """
    try:
        supabase = get_client()
        update = {"lost_reason": lost_reason}
        if lost_reason_detail:
            update["lost_reason_detail"] = lost_reason_detail

        supabase.table("lost_jobs").update(update).eq("proposal_id", proposal_id).execute()
        print(f"[{timestamp()}] INFO db_lost_jobs: Updated reason for proposal {proposal_id} → {lost_reason}")
        return True

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_lost_jobs: update_lost_job_reason failed — {e}")
        return False


def update_monthly_outcomes(client_id: str) -> dict | None:
    """
    Recalculate and upsert the proposal_outcomes row for the current month.
    Called after every proposal status change so the report is always current.

    Reads all proposals for this client in the current month and tallies:
    - proposals_sent, proposals_accepted, proposals_declined, proposals_cold
    - revenue_won (accepted proposals), revenue_lost (declined + cold)
    - top_lost_reason (most common reason in lost_jobs this month)

    Returns:
        The updated proposal_outcomes record dict, or None on failure.
    """
    try:
        supabase = get_client()
        month = datetime.now().strftime("%Y-%m")

        # Get all proposals for this client created this month
        month_start = f"{month}-01T00:00:00+00:00"
        proposals_result = (
            supabase.table("proposals")
            .select("status, response_type, amount_estimate")
            .eq("client_id", client_id)
            .gte("created_at", month_start)
            .execute()
        )
        proposals = proposals_result.data or []

        sent       = len([p for p in proposals if p["status"] in ("sent", "accepted", "rejected", "expired")])
        accepted   = len([p for p in proposals if p["response_type"] == "accepted"])
        declined   = len([p for p in proposals if p["response_type"] == "declined"])
        cold       = len([p for p in proposals if p["response_type"] == "cold"])
        revenue_won  = sum(float(p.get("amount_estimate") or 0) for p in proposals if p["response_type"] == "accepted")
        revenue_lost = sum(float(p.get("amount_estimate") or 0) for p in proposals if p["response_type"] in ("declined", "cold"))

        # Find the most common lost reason this month
        lost_jobs_result = (
            supabase.table("lost_jobs")
            .select("lost_reason")
            .eq("client_id", client_id)
            .gte("created_at", month_start)
            .execute()
        )
        reason_counts = {}
        for lj in (lost_jobs_result.data or []):
            r = lj.get("lost_reason") or "unknown"
            reason_counts[r] = reason_counts.get(r, 0) + 1
        top_lost_reason = max(reason_counts, key=reason_counts.get) if reason_counts else None

        # Upsert — create or update the row for this month
        record = {
            "client_id":           client_id,
            "month":               month,
            "proposals_sent":      sent,
            "proposals_accepted":  accepted,
            "proposals_declined":  declined,
            "proposals_cold":      cold,
            "revenue_won":         round(revenue_won, 2),
            "revenue_lost":        round(revenue_lost, 2),
            "top_lost_reason":     top_lost_reason,
            "updated_at":          datetime.now(timezone.utc).isoformat(),
        }
        result = supabase.table("proposal_outcomes").upsert(
            record, on_conflict="client_id,month"
        ).execute()

        print(f"[{timestamp()}] INFO db_lost_jobs: Monthly outcomes updated → {month} sent={sent} won={accepted} lost={declined+cold}")
        return result.data[0] if result.data else None

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_lost_jobs: update_monthly_outcomes failed — {e}")
        return None


def get_monthly_outcomes(client_id: str, month: str = None) -> dict | None:
    """
    Fetch the proposal_outcomes record for a given month.

    Args:
        client_id: UUID of the client
        month:     Format "2026-03". Defaults to current month.

    Returns:
        Outcomes dict or None.
    """
    try:
        supabase = get_client()
        if not month:
            month = datetime.now().strftime("%Y-%m")

        result = (
            supabase.table("proposal_outcomes")
            .select("*")
            .eq("client_id", client_id)
            .eq("month", month)
            .single()
            .execute()
        )
        return result.data

    except Exception as e:
        print(f"[{timestamp()}] INFO db_lost_jobs: No outcomes found for {month} — {e}")
        return None
