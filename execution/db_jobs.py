"""
db_jobs.py — Jobs table queries

A "job" is the core work order record. Every agent flow creates or updates
a job. Status moves through: new → estimated → scheduled → complete → invoiced → paid.

Usage:
    from execution.db_jobs import create_job, update_job_status, get_job,
                                   update_job_completion, get_job_with_proposal,
                                   calculate_job_cost
"""

import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_job(
    client_id: str,
    customer_id: str,
    job_type: str,
    raw_input: str,
    job_description: str = None,
) -> str | None:
    """
    Create a new job record.
    Called by the proposal_agent when an owner texts in a new work request.

    Args:
        client_id:       UUID of the client (business owner)
        customer_id:     UUID of the end customer
        job_type:        Type of work: pump, inspect, repair, emergency, install
        raw_input:       Exact text the owner sent in (preserved verbatim)
        job_description: Human-readable summary (optional, can be added later)

    Returns:
        New job UUID as a string, or None on failure.
    """
    try:
        supabase = get_client()
        record = {
            "client_id":       client_id,
            "customer_id":     customer_id,
            "job_type":        job_type,
            "raw_input":       raw_input,
            "job_description": job_description or raw_input,
            "status":          "new",
        }
        result = supabase.table("jobs").insert(record).execute()
        job_id = result.data[0]["id"]
        print(f"[{timestamp()}] INFO db_jobs: Created job id={job_id} type={job_type}")
        return job_id

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_jobs: create_job failed — {e}")
        return None


def update_job_status(job_id: str, status: str) -> bool:
    """
    Update the status of a job.
    Valid statuses: new, estimated, scheduled, complete, invoiced, paid.

    Args:
        job_id: UUID of the job
        status: New status string

    Returns:
        True on success, False on failure.
    """
    try:
        supabase = get_client()
        supabase.table("jobs").update({"status": status}).eq("id", job_id).execute()
        print(f"[{timestamp()}] INFO db_jobs: Job {job_id} → status={status}")
        return True

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_jobs: update_job_status failed — {e}")
        return False


def get_job(job_id: str) -> dict | None:
    """
    Fetch the full job record by ID.

    Args:
        job_id: UUID of the job

    Returns:
        Job record as a dict, or None on failure.
    """
    try:
        supabase = get_client()
        result = (
            supabase.table("jobs")
            .select("*")
            .eq("id", job_id)
            .single()
            .execute()
        )
        return result.data

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_jobs: get_job failed for id={job_id} — {e}")
        return None


def get_jobs_by_client(client_id: str, status: str = None) -> list:
    """
    Return all jobs for a client, optionally filtered by status.
    Used by agents to check open work, pending estimates, etc.

    Args:
        client_id: UUID of the client
        status:    Optional status filter (e.g. "new", "invoiced")

    Returns:
        List of job dicts ordered newest first, or empty list on error.
    """
    try:
        supabase = get_client()
        query = (
            supabase.table("jobs")
            .select("*")
            .eq("client_id", client_id)
            .order("created_at", desc=True)
        )
        if status:
            query = query.eq("status", status)

        result = query.execute()
        print(f"[{timestamp()}] INFO db_jobs: get_jobs_by_client → {len(result.data)} jobs (status={status or 'all'})")
        return result.data or []

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_jobs: get_jobs_by_client failed — {e}")
        return []


def update_job_completion(
    job_id: str,
    actual_hours: float,
    actual_amount: float,
    notes: str = None,
) -> dict | None:
    """
    Record completion data on a job and set status to "complete".
    Called by invoice_agent after parsing the owner's done message.

    Args:
        job_id:        UUID of the job
        actual_hours:  How many hours the job actually took
        actual_amount: Dollar amount actually invoiced
        notes:         Any completion notes (optional)

    Returns:
        Updated job record as a dict, or None on failure.
    """
    try:
        supabase = get_client()
        update = {
            "actual_hours":  actual_hours,
            "actual_amount": actual_amount,
            "status":        "complete",
            "completed_date": datetime.now().date().isoformat(),
        }
        if notes:
            update["job_notes"] = notes

        supabase.table("jobs").update(update).eq("id", job_id).execute()
        print(f"[{timestamp()}] INFO db_jobs: Job {job_id} marked complete — {actual_hours}hrs, ${actual_amount}")

        # Return the updated record
        return get_job(job_id)

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_jobs: update_job_completion failed — {e}")
        return None


def get_job_with_proposal(job_id: str) -> dict | None:
    """
    Return a job record merged with its most recent proposal data.
    Used by invoice_agent and job_cost_agent to compare estimate vs actual.

    Returns a combined dict with all job fields plus:
        proposal_id, proposal_text, estimated_amount (from proposal),
        proposal_status, proposal_sent_at
    Returns None on failure.
    """
    try:
        supabase = get_client()

        # Get the job
        job_result = (
            supabase.table("jobs")
            .select("*")
            .eq("id", job_id)
            .single()
            .execute()
        )
        if not job_result.data:
            return None
        job = job_result.data

        # Get the most recent proposal for this job
        proposal_result = (
            supabase.table("proposals")
            .select("id, proposal_text, amount_estimate, status, sent_at")
            .eq("job_id", job_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if proposal_result.data:
            proposal = proposal_result.data[0]
            # Merge proposal fields into the job dict with clear prefixes
            job["proposal_id"]       = proposal["id"]
            job["proposal_text"]     = proposal["proposal_text"]
            job["proposal_estimate"] = proposal["amount_estimate"]
            job["proposal_status"]   = proposal["status"]
            job["proposal_sent_at"]  = proposal["sent_at"]
            # Use proposal amount as estimated_amount if job doesn't have one
            if not job.get("estimated_amount") and proposal.get("amount_estimate"):
                job["estimated_amount"] = proposal["amount_estimate"]
        else:
            job["proposal_id"]       = None
            job["proposal_text"]     = None
            job["proposal_estimate"] = None
            job["proposal_status"]   = None
            job["proposal_sent_at"]  = None

        print(f"[{timestamp()}] INFO db_jobs: get_job_with_proposal → job {job_id} (proposal={'found' if job['proposal_id'] else 'none'})")
        return job

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_jobs: get_job_with_proposal failed — {e}")
        return None


def _parse_hourly_rate(personality: str) -> float:
    """Extract the hourly rate from the personality text. Falls back to $125."""
    match = re.search(r'[Hh]ourly rate.*?\$(\d+(?:\.\d+)?)', personality)
    return float(match.group(1)) if match else 125.00


def calculate_job_cost(job_id: str) -> dict | None:
    """
    Compute the full job costing breakdown for a completed job.
    Compares estimated vs actual hours and dollars. Determines result.

    Break-even threshold: within 0.5 hours (30 minutes) of estimate.

    Args:
        job_id: UUID of the completed job

    Returns:
        Dict with full costing breakdown, or None on failure:
        {
          job_id, contract_type,
          estimated_hours, actual_hours, hour_variance,
          estimated_amount, actual_amount, amount_variance,
          hourly_rate, labor_cost, job_margin,
          result,       # "won", "lost", "break_even"
          summary       # one-line plain English
        }
    """
    try:
        supabase = get_client()

        # Get job with proposal data
        job = get_job_with_proposal(job_id)
        if not job:
            print(f"[{timestamp()}] ERROR db_jobs: calculate_job_cost — job not found: {job_id}")
            return None

        # Get client to pull hourly rate from personality
        client_result = (
            supabase.table("clients")
            .select("personality, owner_name")
            .eq("id", job["client_id"])
            .single()
            .execute()
        )
        personality  = client_result.data.get("personality", "") if client_result.data else ""
        hourly_rate  = _parse_hourly_rate(personality)

        # Pull numbers — default to 0 if not set
        estimated_hours  = float(job.get("estimated_hours") or 0)
        actual_hours     = float(job.get("actual_hours") or 0)
        estimated_amount = float(job.get("estimated_amount") or job.get("proposal_estimate") or 0)
        actual_amount    = float(job.get("actual_amount") or 0)
        contract_type    = job.get("contract_type") or "time_and_materials"

        hour_variance    = round(actual_hours - estimated_hours, 2)
        amount_variance  = round(actual_amount - estimated_amount, 2)
        labor_cost       = round(actual_hours * hourly_rate, 2)
        job_margin       = round(actual_amount - labor_cost, 2)

        # Determine result — within 30 min is break_even
        BREAK_EVEN_THRESHOLD = 0.5
        if abs(hour_variance) <= BREAK_EVEN_THRESHOLD:
            result = "break_even"
        elif hour_variance < 0:
            result = "won"       # finished faster than estimated
        else:
            result = "lost"      # ran over estimate

        # Build the one-line summary
        summary = _build_cost_summary(
            result, contract_type, actual_hours, estimated_hours,
            hour_variance, actual_amount, labor_cost, job_margin,
            job.get("job_type", "job"), hourly_rate
        )

        costing = {
            "job_id":            job_id,
            "contract_type":     contract_type,
            "estimated_hours":   estimated_hours,
            "actual_hours":      actual_hours,
            "hour_variance":     hour_variance,
            "estimated_amount":  estimated_amount,
            "actual_amount":     actual_amount,
            "amount_variance":   amount_variance,
            "hourly_rate":       hourly_rate,
            "labor_cost":        labor_cost,
            "job_margin":        job_margin,
            "result":            result,
            "summary":           summary,
        }

        print(f"[{timestamp()}] INFO db_jobs: calculate_job_cost → result={result} margin=${job_margin}")
        return costing

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_jobs: calculate_job_cost failed — {e}")
        return None


def _build_cost_summary(
    result, contract_type, actual_hours, estimated_hours,
    hour_variance, actual_amount, labor_cost, margin,
    job_type, hourly_rate
) -> str:
    """Generate a plain-English one or two line cost summary for the owner."""
    labor_delta = abs(round(hour_variance * hourly_rate, 2))

    if contract_type == "fixed_price":
        if result == "won":
            return (
                f"Fixed price: finished in {actual_hours}hrs on ${actual_amount:.0f} contract. "
                f"${margin:.0f} gross margin. Good job."
            )
        elif result == "lost":
            return (
                f"Fixed price: took {actual_hours}hrs on ${actual_amount:.0f} contract. "
                f"Labor alone was ${labor_cost:.0f}. Tight — ${margin:.0f} gross."
            )
        else:
            return f"Fixed price: came in right on estimate. ${margin:.0f} gross margin."

    # Time and materials
    if result == "won":
        return (
            f"Job cost: {actual_hours}hrs worked, quoted {estimated_hours}. "
            f"You made an extra ${labor_delta:.0f} on this one. Good margin."
        )
    elif result == "lost":
        return (
            f"Job cost: {actual_hours}hrs worked, quoted {estimated_hours}. "
            f"Ran ${labor_delta:.0f} long on labor. Adjust your {job_type} quotes."
        )
    else:
        return f"Job cost: came in right on estimate. Clean job."
