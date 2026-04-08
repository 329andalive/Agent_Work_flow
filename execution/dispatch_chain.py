"""
dispatch_chain.py — Manages the job-to-job chain in a dispatch route

The SMS clock flow:
  1. Tech texts CLOCK IN → system finds their dispatch route for today
  2. System sends the route summary via SMS
  3. System marks Job 1 as started (job_start timestamp)
  4. Tech texts DONE → system marks Job 1 as ended (job_end, duration)
  5. System auto-advances to Job 2 (marks it started)
  6. Repeat until all jobs done or tech clocks out

This module provides:
  - get_todays_route(client_id, worker_id) → ordered list of jobs
  - start_first_job(client_id, worker_id, time_entry_id) → starts Job 1
  - advance_to_next_job(client_id, worker_id, completed_job_id) → ends current, starts next
  - get_current_job(client_id, worker_id) → which job they're on now
  - build_route_sms(jobs, worker_name) → formatted route message
"""

import os
import sys
from datetime import datetime, timezone, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_todays_route(client_id: str, worker_id: str) -> list:
    """
    Get today's dispatched jobs for a worker, ordered by sort_order.

    Returns:
        List of dicts with job details + sort_order, or empty list.
        Each dict has: job_id, sort_order, job_type, job_description,
        customer_name, customer_address, estimated_amount, status
    """
    try:
        sb = get_supabase()
        today_str = date.today().isoformat()

        # Get assignments from route_assignments for today
        assignments = sb.table("route_assignments").select(
            "job_id, sort_order"
        ).eq("worker_id", worker_id).eq("client_id", client_id).order(
            "sort_order"
        ).execute()

        if not assignments.data:
            return []

        # Filter to today's jobs by checking jobs.scheduled_date
        job_ids = [a["job_id"] for a in assignments.data]
        sort_map = {a["job_id"]: a["sort_order"] for a in assignments.data}

        jobs = sb.table("jobs").select(
            "id, job_type, job_description, status, estimated_amount, "
            "customer_id, scheduled_date, job_start, job_end, dispatch_status"
        ).in_("id", job_ids).eq("scheduled_date", today_str).order(
            "sort_order"
        ).execute()

        if not jobs.data:
            return []

        # Enrich with customer info
        customer_ids = list(set(j.get("customer_id") for j in jobs.data if j.get("customer_id")))
        cust_map = {}
        if customer_ids:
            try:
                custs = sb.table("customers").select(
                    "id, customer_name, customer_address, customer_phone"
                ).in_("id", customer_ids).execute()
                cust_map = {c["id"]: c for c in (custs.data or [])}
            except Exception:
                pass

        route = []
        for job in jobs.data:
            cust = cust_map.get(job.get("customer_id"), {})
            route.append({
                "job_id": job["id"],
                "sort_order": sort_map.get(job["id"], 0),
                "job_type": job.get("job_type", ""),
                "job_description": job.get("job_description", ""),
                "status": job.get("status", ""),
                "dispatch_status": job.get("dispatch_status", ""),
                "estimated_amount": job.get("estimated_amount"),
                "job_start": job.get("job_start"),
                "job_end": job.get("job_end"),
                "customer_name": cust.get("customer_name", ""),
                "customer_address": cust.get("customer_address", ""),
                "customer_phone": cust.get("customer_phone", ""),
            })

        route.sort(key=lambda j: j["sort_order"])
        return route

    except Exception as e:
        print(f"[{timestamp()}] ERROR dispatch_chain: get_todays_route failed — {e}")
        return []


def get_current_job(client_id: str, worker_id: str) -> dict | None:
    """
    Get the job the worker is currently working on.
    Looks for a job in today's route that has job_start set but no job_end.

    Returns:
        Job dict or None.
    """
    route = get_todays_route(client_id, worker_id)
    for job in route:
        if job.get("job_start") and not job.get("job_end"):
            return job
    return None


def start_first_job(client_id: str, worker_id: str, time_entry_id: str = None) -> dict | None:
    """
    Start the first unstarted job in today's route.
    Sets job_start, updates status to in_progress, links time_entry.

    Returns:
        The started job dict, or None if no jobs to start.
    """
    route = get_todays_route(client_id, worker_id)
    if not route:
        return None

    # Find first job that hasn't started
    for job in route:
        if not job.get("job_start") and job.get("dispatch_status") not in ("completed", "carry_forward", "no_show"):
            return _start_job(job["job_id"], time_entry_id)

    return None


def advance_to_next_job(client_id: str, worker_id: str,
                        completed_job_id: str, time_entry_id: str = None) -> dict | None:
    """
    End the completed job and start the next one in the route.

    Args:
        completed_job_id: The job that was just finished (DONE)
        time_entry_id: Current open time entry to update current_job_id

    Returns:
        The next job dict that was started, or None if route is done.
    """
    # End the completed job
    _end_job(completed_job_id)

    # Find the next unstarted job in the route
    route = get_todays_route(client_id, worker_id)
    for job in route:
        if job["job_id"] == completed_job_id:
            continue
        if not job.get("job_start") and job.get("dispatch_status") not in ("completed", "carry_forward", "no_show"):
            return _start_job(job["job_id"], time_entry_id)

    return None


def _start_job(job_id: str, time_entry_id: str = None) -> dict | None:
    """Mark a job as started — sets job_start and status."""
    try:
        sb = get_supabase()
        now = datetime.now(timezone.utc).isoformat()

        sb.table("jobs").update({
            "job_start": now,
            "status": "in_progress",
            "dispatch_status": "in_progress",
        }).eq("id", job_id).execute()

        # Update time_entry to track current job
        if time_entry_id:
            try:
                sb.table("time_entries").update({
                    "current_job_id": job_id,
                }).eq("id", time_entry_id).execute()
            except Exception:
                pass  # Column may not exist yet

        print(f"[{timestamp()}] INFO dispatch_chain: Started job {job_id[:8]}")

        # Return the job details
        result = sb.table("jobs").select("*").eq("id", job_id).execute()
        return result.data[0] if result.data else {"id": job_id}

    except Exception as e:
        print(f"[{timestamp()}] ERROR dispatch_chain: _start_job failed — {e}")
        return None


def _end_job(job_id: str) -> None:
    """Mark a job as ended — sets job_end and computes duration."""
    try:
        sb = get_supabase()
        now = datetime.now(timezone.utc)

        # Get job_start to compute duration
        result = sb.table("jobs").select("job_start").eq("id", job_id).execute()
        duration = None
        if result.data and result.data[0].get("job_start"):
            try:
                start = datetime.fromisoformat(
                    result.data[0]["job_start"].replace("Z", "+00:00")
                )
                duration = int((now - start).total_seconds() / 60)
            except Exception:
                pass

        update = {
            "job_end": now.isoformat(),
            "status": "completed",
            "dispatch_status": "completed",
        }
        if duration is not None:
            update["job_duration_min"] = duration

        sb.table("jobs").update(update).eq("id", job_id).execute()
        print(f"[{timestamp()}] INFO dispatch_chain: Ended job {job_id[:8]} duration={duration}min")

    except Exception as e:
        print(f"[{timestamp()}] ERROR dispatch_chain: _end_job failed — {e}")


def resolve_job(route: list, identifier: str) -> dict | None:
    """
    Resolve a job from today's route by number, customer name, or address.

    Accepts:
      - "2" or "3"         → job by 1-indexed position
      - "alice smith"       → fuzzy match on customer_name
      - "123 main st"       → fuzzy match on customer_address
      - "alice"             → partial name match
      - "main st"           → partial address match

    Returns:
        Matching job dict from the route, or None if no match.
    """
    if not route or not identifier:
        return None

    identifier = identifier.strip()

    # Try numeric first
    if identifier.isdigit():
        idx = int(identifier) - 1
        if 0 <= idx < len(route):
            return route[idx]
        return None

    # Normalize for fuzzy matching
    query = identifier.lower().strip()

    # Try exact customer name match
    for job in route:
        name = (job.get("customer_name") or "").lower()
        if name and name == query:
            return job

    # Try partial customer name match (query is substring of name or vice versa)
    for job in route:
        name = (job.get("customer_name") or "").lower()
        if name and (query in name or name in query):
            return job

    # Try last name match (most distinctive part)
    query_parts = query.split()
    if query_parts:
        last_word = query_parts[-1]
        for job in route:
            name = (job.get("customer_name") or "").lower()
            if name and last_word in name.split():
                return job

    # Try address match
    for job in route:
        addr = (job.get("customer_address") or "").lower()
        if addr and (query in addr or addr in query):
            return job

    # Try partial address (street name or number)
    for job in route:
        addr = (job.get("customer_address") or "").lower()
        if addr:
            for part in query_parts:
                if len(part) >= 3 and part in addr:
                    return job

    return None


def build_route_sms(jobs: list, worker_name: str, business_name: str = "") -> str:
    """
    Build a formatted SMS message with today's dispatch route.

    Returns:
        Multi-line SMS string like:
            "Good morning Jesse! Today's jobs:
             1. Pump-out — Alice Smith, 123 Main St
             2. Inspection — Bob Jones, 45 Oak Ave
             3. Repair — Carol Duggan, 12 School St
             Reply DONE after each job."
    """
    if not jobs:
        return f"No jobs dispatched for you today, {worker_name}."

    first = worker_name.split()[0] if worker_name else "team"
    lines = [f"Good morning {first}! Today's route ({len(jobs)} jobs):"]

    for i, job in enumerate(jobs, 1):
        jtype = (job.get("job_type") or "Job").replace("_", " ").title()
        cust = job.get("customer_name", "")
        addr = job.get("customer_address", "")
        amt = job.get("estimated_amount")

        line = f"{i}. {jtype}"
        if cust:
            line += f" — {cust}"
        if addr:
            short_addr = addr.split(",")[0] if "," in addr else addr
            line += f", {short_addr}"
        if amt:
            line += f" (${amt:.0f})"
        lines.append(line)

    lines.append("")
    lines.append("JOB START [name or #] to begin")
    lines.append("DONE [name or #] when finished")
    return "\n".join(lines)
