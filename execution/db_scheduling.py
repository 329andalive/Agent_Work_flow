"""
db_scheduling.py — Scheduling DB helpers using the unified jobs table (Option A)

Option A: queries the existing `jobs` table instead of a separate `scheduled_jobs`
table. The jobs table gets dispatch columns added via SQL migration:
  geo_lat, geo_lng, zone_cluster, requested_time, dispatch_status,
  assigned_worker_id, wave_id, sort_order, incomplete_reason

Every function filters by client_id (HARD RULE: multi-tenancy — never return
data from one client to another).

Also queries: employees (as workers), route_assignments, dispatch_log.

Usage:
    from execution.db_scheduling import (
        get_todays_jobs, get_workers, save_dispatch_session,
        get_carry_forward_jobs, get_held_jobs, update_dispatch_status,
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


# ---------------------------------------------------------------------------
# get_todays_jobs — jobs for a date, ordered by zone then time
# ---------------------------------------------------------------------------

def get_todays_jobs(client_id: str, target_date: str = None) -> list:
    """
    Get all jobs for a given date that are not dispatch-cancelled.
    Ordered by zone_cluster ASC then requested_time ASC (nulls last).

    Args:
        client_id:    UUID of the client (tenant identifier)
        target_date:  ISO date string (YYYY-MM-DD). Defaults to today.

    Returns:
        List of job dicts, or empty list on error.
    """
    if not target_date:
        target_date = date.today().isoformat()

    try:
        sb = get_supabase()

        # Try full query with dispatch columns first
        try:
            result = (
                sb.table("jobs")
                .select(
                    "id, job_type, job_description, status, scheduled_date, "
                    "estimated_amount, customer_id, raw_input, job_notes, "
                    "geo_lat, geo_lng, zone_cluster, requested_time, "
                    "dispatch_status, assigned_worker_id, wave_id, sort_order"
                )
                .eq("client_id", client_id)
                .eq("scheduled_date", target_date)
                .order("sort_order")
                .order("created_at")
                .execute()
            )
        except Exception:
            # Dispatch columns don't exist yet — use basic query
            print(f"[{timestamp()}] WARN db_scheduling: Dispatch columns not on jobs table yet — basic query")
            result = (
                sb.table("jobs")
                .select("id, job_type, job_description, status, scheduled_date, "
                        "estimated_amount, customer_id, raw_input, job_notes")
                .eq("client_id", client_id)
                .eq("scheduled_date", target_date)
                .order("created_at")
                .execute()
            )

        # Filter out dispatch-cancelled — keep NULL, unassigned, assigned, etc.
        all_jobs = result.data or []
        jobs = [j for j in all_jobs if j.get("dispatch_status") != "cancelled"]
        print(f"[{timestamp()}] INFO db_scheduling: get_todays_jobs({target_date}) → {len(jobs)} jobs")
        return jobs

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_scheduling: get_todays_jobs failed — {e}")
        return []


# ---------------------------------------------------------------------------
# get_workers — active employees for client (employees table = workers)
# ---------------------------------------------------------------------------

def get_workers(client_id: str) -> list:
    """
    Get all active employees for a client. Uses the existing employees
    table — no separate workers table needed.

    Args:
        client_id: UUID of the client (tenant identifier)

    Returns:
        List of employee dicts, or empty list on error.
    """
    try:
        sb = get_supabase()
        result = (
            sb.table("employees")
            .select("id, name, phone, role, active")
            .eq("client_id", client_id)
            .eq("active", True)
            .order("name")
            .execute()
        )
        workers = result.data or []
        print(f"[{timestamp()}] INFO db_scheduling: get_workers → {len(workers)} active for client {client_id[:8]}")
        return workers
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_scheduling: get_workers failed — {e}")
        return []


# ---------------------------------------------------------------------------
# save_dispatch_session — write assignments + dispatch log
# ---------------------------------------------------------------------------

def save_dispatch_session(
    client_id: str,
    assignments: list,
    session_id: str = None,
) -> str | None:
    """
    Write route assignments and dispatch log in one pass.

    Args:
        client_id:    UUID of the client (tenant identifier)
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
                "client_id": client_id,
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

        # Also update each job's dispatch columns
        for a in assignments:
            try:
                update = {
                    "assigned_worker_id": a["worker_id"],
                    "dispatch_status": "assigned",
                }
                if a.get("wave_id"):
                    update["wave_id"] = a["wave_id"]
                if a.get("sort_order") is not None:
                    update["sort_order"] = a["sort_order"]
                sb.table("jobs").update(update).eq("id", a["job_id"]).execute()
            except Exception:
                pass  # Non-fatal — column may not exist yet

        # Write dispatch_log entry
        sb.table("dispatch_log").insert({
            "client_id": client_id,
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


# ---------------------------------------------------------------------------
# get_carry_forward_jobs — yesterday's incomplete jobs
# ---------------------------------------------------------------------------

def get_carry_forward_jobs(client_id: str) -> list:
    """
    Get jobs with dispatch_status='carry_forward' and scheduled_date < today.

    Falls back to status-based query if dispatch columns don't exist yet.

    Args:
        client_id: UUID of the client (tenant identifier)

    Returns:
        List of job dicts, or empty list on error.
    """
    try:
        sb = get_supabase()
        today = date.today().isoformat()
        result = (
            sb.table("jobs")
            .select("id, job_type, job_description, status, scheduled_date, "
                    "estimated_amount, customer_id, dispatch_status, zone_cluster")
            .eq("client_id", client_id)
            .eq("dispatch_status", "carry_forward")
            .lt("scheduled_date", today)
            .order("scheduled_date")
            .execute()
        )
        jobs = result.data or []
        if jobs:
            print(f"[{timestamp()}] INFO db_scheduling: {len(jobs)} carry-forward jobs")
        return jobs
    except Exception as e:
        if "column" in str(e).lower():
            print(f"[{timestamp()}] WARN db_scheduling: dispatch_status column not yet added — no carry-forward jobs")
            return []
        print(f"[{timestamp()}] ERROR db_scheduling: get_carry_forward_jobs failed — {e}")
        return []


# ---------------------------------------------------------------------------
# get_held_jobs — parts_pending + scope_review
# ---------------------------------------------------------------------------

def get_held_jobs(client_id: str) -> list:
    """
    Get jobs with dispatch_status in ('parts_pending', 'scope_review').

    Args:
        client_id: UUID of the client (tenant identifier)

    Returns:
        List of job dicts, or empty list on error.
    """
    try:
        sb = get_supabase()
        result = (
            sb.table("jobs")
            .select("id, job_type, job_description, status, scheduled_date, "
                    "customer_id, dispatch_status")
            .eq("client_id", client_id)
            .in_("dispatch_status", ["parts_pending", "scope_review"])
            .order("created_at")
            .execute()
        )
        jobs = result.data or []
        if jobs:
            print(f"[{timestamp()}] INFO db_scheduling: {len(jobs)} held jobs")
        return jobs
    except Exception as e:
        if "column" in str(e).lower():
            return []
        print(f"[{timestamp()}] ERROR db_scheduling: get_held_jobs failed — {e}")
        return []


# ---------------------------------------------------------------------------
# update_dispatch_status — update a job's dispatch status
# ---------------------------------------------------------------------------

def update_dispatch_status(
    job_id: str,
    dispatch_status: str,
    incomplete_reason: str = None,
) -> bool:
    """
    Update the dispatch_status of a job.

    Args:
        job_id:            UUID of the job
        dispatch_status:   New status: assigned, completed, carry_forward,
                           parts_pending, scope_review, no_show, cancelled
        incomplete_reason: Optional reason for carry_forward/held

    Returns:
        True on success, False on failure.
    """
    try:
        sb = get_supabase()
        update = {"dispatch_status": dispatch_status}
        if incomplete_reason:
            update["incomplete_reason"] = incomplete_reason
        if dispatch_status == "completed":
            update["status"] = "completed"  # Also update the main status

        sb.table("jobs").update(update).eq("id", job_id).execute()
        print(f"[{timestamp()}] INFO db_scheduling: Job {job_id[:8]} → dispatch_status={dispatch_status}")
        return True
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_scheduling: update_dispatch_status failed — {e}")
        return False


# ---------------------------------------------------------------------------
# create_scheduled_job — insert job with geocoding
# ---------------------------------------------------------------------------

def create_scheduled_job(client_id: str, job_data: dict) -> str | None:
    """
    Insert a new job with geocoded coordinates and zone cluster.
    Uses the existing jobs table with dispatch columns.

    Args:
        client_id:  UUID of the client (tenant identifier)
        job_data:   Dict with keys: customer_id, job_type, job_description,
                    scheduled_date, requested_time (optional),
                    job_notes (optional), address (for geocoding)

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
            "client_id": client_id,
            "customer_id": job_data.get("customer_id"),
            "job_type": job_data.get("job_type", ""),
            "job_description": job_data.get("job_description") or address or "",
            "scheduled_date": job_data.get("scheduled_date", date.today().isoformat()),
            "raw_input": job_data.get("raw_input", "Created via dispatch"),
            "status": "scheduled",
            "dispatch_status": "unassigned",
        }

        # Add dispatch columns if provided — these may not exist yet
        optional_cols = {
            "requested_time": job_data.get("requested_time"),
            "job_notes": job_data.get("job_notes"),
            "geo_lat": geo["lat"],
            "geo_lng": geo["lng"],
            "zone_cluster": geo["zone_cluster"],
        }
        for k, v in optional_cols.items():
            if v is not None:
                record[k] = v

        result = sb.table("jobs").insert(record).execute()
        if not result.data:
            print(f"[{timestamp()}] ERROR db_scheduling: create_scheduled_job insert returned no data")
            return None

        job_id = result.data[0]["id"]
        print(
            f"[{timestamp()}] INFO db_scheduling: Created job {job_id[:8]} "
            f"type={record['job_type']} zone={geo['zone_cluster']} date={record['scheduled_date']}"
        )
        return job_id

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_scheduling: create_scheduled_job failed — {e}")
        return None


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    # Replace with your test client_id from Supabase
    test_client_id = os.environ.get("TEST_CLIENT_ID", "your-client-id-here")

    print("--- get_workers ---")
    workers = get_workers(test_client_id)
    for w in workers:
        print(f"  {w.get('name')} ({w.get('role')})")
    if not workers:
        print("  (no workers found)")

    print("\n--- get_todays_jobs ---")
    jobs = get_todays_jobs(test_client_id)
    for j in jobs:
        print(f"  {j.get('job_type')} — {j.get('job_description', '')[:40]} [{j.get('status')}]")
    if not jobs:
        print("  (no jobs for today)")
