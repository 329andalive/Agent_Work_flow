"""
db_jobs.py — Jobs table queries

A "job" is the core work order record. Every agent flow creates or updates
a job. Status moves through: new → estimated → scheduled → complete → invoiced → paid.

Usage:
    from execution.db_jobs import create_job, update_job_status, get_job
"""

import os
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
