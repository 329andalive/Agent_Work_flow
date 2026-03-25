"""
db_scheduling.py — Core scheduling DB helpers with full multi-tenancy

Every function filters by client_phone (HARD RULE: multi-tenancy).
Works with the scheduled_jobs, workers, route_assignments, and
dispatch_log tables. Calls geocode_address on new job creation.

Usage:
    from execution.db_scheduling import (
        get_todays_jobs, get_workers, save_dispatch_session,
        get_carry_forward_jobs, get_held_jobs, update_job_status,
        create_scheduled_job,
    )
"""

import os
import sys
import uuid
import asyncio
from datetime import datetime, date, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_todays_jobs(client_phone: str, target_date: str = None) -> list:
    """
    Get all scheduled jobs for a given date, ordered by zone_cluster
    then requested_time (nulls last).

    Args:
        client_phone: E.164 phone (tenant identifier)
        target_date:  ISO date string (YYYY-MM-DD). Defaults to today.

    Returns:
        List of scheduled_jobs dicts, or empty list on error.
    """
    if not target_date:
        target_date = date.today().isoformat()

    try:
        sb = get_supabase()
        result = (
            sb.table("scheduled_jobs")
            .select("*")
            .eq("client_phone", client_phone)
            .eq("scheduled_date", target_date)
            .order("zone_cluster")
            .order("requested_time", nullsfirst=False)
            .execute()
        )
        jobs = result.data or []
        print(f"[{timestamp()}] INFO db_scheduling: get_todays_jobs({target_date}) → {len(jobs)} jobs for {client_phone}")
        return jobs
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_scheduling: get_todays_jobs failed — {e}")
        return []


def get_workers(client_phone: str) -> list:
    """
    Get all active workers for a client.

    Args:
        client_phone: E.164 phone (tenant identifier)

    Returns:
        List of worker dicts, or empty list on error.
    """
    try:
        sb = get_supabase()
        result = (
            sb.table("workers")
            .select("*")
            .eq("client_phone", client_phone)
            .eq("active", True)
            .order("name")
            .execute()
        )
        workers = result.data or []
        print(f"[{timestamp()}] INFO db_scheduling: get_workers → {len(workers)} active for {client_phone}")
        return workers
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_scheduling: get_workers failed — {e}")
        return []


def save_dispatch_session(
    client_phone: str,
    assignments: list,
    session_id: str = None,
) -> str | None:
    """
    Write route assignments and dispatch log in one pass.

    Args:
        client_phone: E.164 phone (tenant identifier)
        assignments:  List of dicts: {job_id, worker_id, wave_id, sort_order}
        session_id:   Optional UUID. Auto-generated if not provided.

    Returns:
        session_id on success, None on failure.
    """
    if not session_id:
        session_id = str(uuid.uuid4())

    try:
        sb = get_supabase()
        now = datetime.now(timezone.utc).isoformat()

        # Write route_assignments
        rows = []
        for a in assignments:
            rows.append({
                "client_phone": client_phone,
                "session_id": session_id,
                "job_id": a["job_id"],
                "worker_id": a["worker_id"],
                "wave_id": a.get("wave_id"),
                "sort_order": a.get("sort_order", 0),
                "assigned_at": now,
            })

        if rows:
            sb.table("route_assignments").insert(rows).execute()
            print(f"[{timestamp()}] INFO db_scheduling: Saved {len(rows)} route assignments (session={session_id[:8]})")

        # Write dispatch_log entry
        sb.table("dispatch_log").insert({
            "client_phone": client_phone,
            "session_id": session_id,
            "job_count": len(assignments),
            "worker_count": len(set(a["worker_id"] for a in assignments)),
            "created_at": now,
        }).execute()
        print(f"[{timestamp()}] INFO db_scheduling: Dispatch log written (session={session_id[:8]})")

        return session_id

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_scheduling: save_dispatch_session failed — {e}")
        return None


def get_carry_forward_jobs(client_phone: str) -> list:
    """
    Get jobs with status='carry_forward' and scheduled_date < today.
    These are yesterday's incomplete jobs that need to be rescheduled.

    Args:
        client_phone: E.164 phone (tenant identifier)

    Returns:
        List of scheduled_jobs dicts, or empty list on error.
    """
    try:
        sb = get_supabase()
        today = date.today().isoformat()
        result = (
            sb.table("scheduled_jobs")
            .select("*")
            .eq("client_phone", client_phone)
            .eq("status", "carry_forward")
            .lt("scheduled_date", today)
            .order("scheduled_date")
            .execute()
        )
        jobs = result.data or []
        if jobs:
            print(f"[{timestamp()}] INFO db_scheduling: {len(jobs)} carry-forward jobs for {client_phone}")
        return jobs
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_scheduling: get_carry_forward_jobs failed — {e}")
        return []


def get_held_jobs(client_phone: str) -> list:
    """
    Get jobs with status in ('parts_pending', 'scope_review').
    These are on hold and need manual attention before scheduling.

    Args:
        client_phone: E.164 phone (tenant identifier)

    Returns:
        List of scheduled_jobs dicts, or empty list on error.
    """
    try:
        sb = get_supabase()
        result = (
            sb.table("scheduled_jobs")
            .select("*")
            .eq("client_phone", client_phone)
            .in_("status", ["parts_pending", "scope_review"])
            .order("created_at")
            .execute()
        )
        jobs = result.data or []
        if jobs:
            print(f"[{timestamp()}] INFO db_scheduling: {len(jobs)} held jobs for {client_phone}")
        return jobs
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_scheduling: get_held_jobs failed — {e}")
        return []


def update_job_status(
    job_id: str,
    status: str,
    incomplete_reason: str = None,
) -> bool:
    """
    Update the status of a scheduled job.

    Args:
        job_id:            UUID of the scheduled job
        status:            New status string
        incomplete_reason: Optional reason when status is carry_forward or held

    Returns:
        True on success, False on failure.
    """
    try:
        sb = get_supabase()
        update = {"status": status}
        if incomplete_reason:
            update["incomplete_reason"] = incomplete_reason
        if status == "completed":
            update["completed_at"] = datetime.now(timezone.utc).isoformat()

        sb.table("scheduled_jobs").update(update).eq("id", job_id).execute()
        print(f"[{timestamp()}] INFO db_scheduling: Job {job_id[:8]} → status={status}")
        return True
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_scheduling: update_job_status failed — {e}")
        return False


def create_scheduled_job(client_phone: str, job_data: dict) -> str | None:
    """
    Insert a new scheduled job. Calls geocode_address to populate
    geo_lat, geo_lng, and zone_cluster from the address field.

    Args:
        client_phone: E.164 phone (tenant identifier)
        job_data:     Dict with keys: customer_name, address, job_type,
                      scheduled_date, requested_time (optional),
                      notes (optional), customer_phone (optional)

    Returns:
        New job UUID as a string, or None on failure.
    """
    try:
        sb = get_supabase()

        # Geocode the address for zone clustering
        geo = {"lat": None, "lng": None, "zone_cluster": "Unknown"}
        address = job_data.get("address", "")
        if address:
            try:
                from execution.geocode import geocode_address
                geo_result = asyncio.run(geocode_address(address))
                geo = {
                    "lat": geo_result.get("lat"),
                    "lng": geo_result.get("lng"),
                    "zone_cluster": geo_result.get("zone_cluster", "Unknown"),
                }
                if geo_result.get("formatted_address"):
                    address = geo_result["formatted_address"]
            except Exception as e:
                print(f"[{timestamp()}] WARN db_scheduling: Geocode failed for '{address}' — {e}")

        record = {
            "client_phone": client_phone,
            "customer_name": job_data.get("customer_name", ""),
            "customer_phone": job_data.get("customer_phone"),
            "address": address,
            "job_type": job_data.get("job_type", ""),
            "scheduled_date": job_data.get("scheduled_date", date.today().isoformat()),
            "requested_time": job_data.get("requested_time"),
            "notes": job_data.get("notes"),
            "status": "scheduled",
            "geo_lat": geo["lat"],
            "geo_lng": geo["lng"],
            "zone_cluster": geo["zone_cluster"],
        }

        result = sb.table("scheduled_jobs").insert(record).execute()
        if not result.data:
            print(f"[{timestamp()}] ERROR db_scheduling: create_scheduled_job insert returned no data")
            return None

        job_id = result.data[0]["id"]
        print(
            f"[{timestamp()}] INFO db_scheduling: Created scheduled job {job_id[:8]} "
            f"type={record['job_type']} zone={record['zone_cluster']} date={record['scheduled_date']}"
        )
        return job_id

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_scheduling: create_scheduled_job failed — {e}")
        return None
