"""
pwa_clock.py — PWA-facing clock in/out + status

This module wraps the underlying clock_agent primitives so the PWA can
clock techs in/out without going through SMS. The DB writes are identical
to the SMS path — only the response shape differs (JSON instead of SMS).

Public API:
    clock_in(client_id, employee_id)  → dict
    clock_out(client_id, employee_id) → dict
    get_status(client_id, employee_id) → dict
"""

import os
import sys
from datetime import datetime, timezone, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.clock_agent import (
    _find_open_entry,
    _find_open_entry_latest,
    _match_schedule,
    _insert_time_entry,
    _update_schedule_status,
    _close_time_entry,
    _job_label,
)


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fmt_local_time(dt: datetime) -> str:
    """Format a UTC datetime for display. PWA shows in browser local time."""
    return dt.isoformat()


def _fmt_duration(minutes: int) -> str:
    """Format minutes as 'Xh Ym' or 'Ym'."""
    if minutes is None:
        return "—"
    h, m = divmod(int(minutes), 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def get_status(client_id: str, employee_id: str) -> dict:
    """
    Return current clock status for an employee.

    Returns:
        {
            "clocked_in": bool,
            "entry_id": str or None,
            "clock_in_at": ISO timestamp or None,
            "elapsed_minutes": int or None,
            "current_job": dict or None,    # from dispatch_chain.get_current_job
            "todays_route": list,           # ordered jobs for today
            "completed_today": int,          # count of jobs with job_end set
        }
    """
    open_entry = _find_open_entry(client_id, employee_id)

    elapsed = None
    clock_in_at = None
    entry_id = None
    if open_entry:
        entry_id = open_entry.get("id")
        clock_in_at = open_entry.get("clock_in")
        if clock_in_at:
            try:
                start = datetime.fromisoformat(clock_in_at.replace("Z", "+00:00"))
                elapsed = int((datetime.now(timezone.utc) - start).total_seconds() / 60)
            except Exception:
                pass

    # Load today's route + current job
    todays_route = []
    current_job = None
    completed_today = 0
    try:
        from execution.dispatch_chain import get_todays_route, get_current_job
        todays_route = get_todays_route(client_id, employee_id)
        current_job = get_current_job(client_id, employee_id)
        completed_today = sum(1 for j in todays_route if j.get("job_end"))
    except Exception as e:
        print(f"[{_ts()}] WARN pwa_clock: route load failed — {e}")

    return {
        "clocked_in": bool(open_entry),
        "entry_id": entry_id,
        "clock_in_at": clock_in_at,
        "elapsed_minutes": elapsed,
        "elapsed_label": _fmt_duration(elapsed) if elapsed is not None else None,
        "current_job": current_job,
        "todays_route": todays_route,
        "completed_today": completed_today,
        "total_jobs": len(todays_route),
    }


def clock_in(client_id: str, employee_id: str) -> dict:
    """
    Clock in an employee. Mirrors the SMS clock-in flow:
      1. Refuse if already clocked in
      2. Match today's schedule (if any)
      3. Insert open time_entry
      4. Mark schedule as in_progress
      5. Auto-start the first job in the dispatch route

    Returns:
        {
            "success": bool,
            "entry_id": str or None,
            "clock_in_at": ISO timestamp or None,
            "job_label": str or None,
            "started_job": dict or None,  # auto-started job, if any
            "error": str or None,
        }
    """
    if _find_open_entry(client_id, employee_id):
        return {
            "success": False,
            "error": "Already clocked in. Clock out before starting a new shift.",
        }

    today_iso = date.today().isoformat()
    schedule_row = _match_schedule(client_id, employee_id, today_iso)
    schedule_id = (schedule_row or {}).get("id")
    job_id = (schedule_row or {}).get("job_id")

    now_utc = datetime.now(timezone.utc)
    entry_id = _insert_time_entry(client_id, employee_id, schedule_id, job_id, now_utc)
    if not entry_id:
        return {
            "success": False,
            "error": "Could not record clock-in. Please try again.",
        }

    _update_schedule_status(schedule_id, "in_progress")

    # Auto-start the first dispatched job if any exist
    started_job = None
    try:
        from execution.dispatch_chain import start_first_job
        started_job = start_first_job(client_id, employee_id, entry_id)
        if started_job:
            print(f"[{_ts()}] INFO pwa_clock: Auto-started first job for employee {employee_id[:8] if employee_id else ''}")
    except Exception as e:
        print(f"[{_ts()}] WARN pwa_clock: auto-start first job failed — {e}")

    print(f"[{_ts()}] INFO pwa_clock: Clock-in success for employee={employee_id} entry={entry_id}")
    return {
        "success": True,
        "entry_id": entry_id,
        "clock_in_at": _fmt_local_time(now_utc),
        "job_label": _job_label(schedule_row),
        "started_job": started_job,
        "error": None,
    }


def clock_out(client_id: str, employee_id: str) -> dict:
    """
    Clock out an employee. Mirrors the SMS clock-out flow:
      1. Refuse if not clocked in
      2. Compute duration
      3. Close time_entry
      4. Mark schedule as completed (if any)

    Returns:
        {
            "success": bool,
            "entry_id": str or None,
            "duration_minutes": int or None,
            "duration_label": str or None,
            "error": str or None,
        }
    """
    open_entry = _find_open_entry_latest(client_id, employee_id)
    if not open_entry:
        return {
            "success": False,
            "error": "You're not clocked in.",
        }

    entry_id = open_entry["id"]
    schedule_id = open_entry.get("schedule_id")

    now_utc = datetime.now(timezone.utc)
    try:
        clock_in_dt = datetime.fromisoformat(open_entry["clock_in"].replace("Z", "+00:00"))
        duration_minutes = int((now_utc - clock_in_dt).total_seconds() / 60)
    except Exception:
        duration_minutes = 0

    if not _close_time_entry(entry_id, now_utc, duration_minutes):
        return {
            "success": False,
            "error": "Could not save clock-out. Please try again.",
        }

    _update_schedule_status(schedule_id, "completed")

    print(f"[{_ts()}] INFO pwa_clock: Clock-out success for employee={employee_id} duration={duration_minutes}min")
    return {
        "success": True,
        "entry_id": entry_id,
        "duration_minutes": duration_minutes,
        "duration_label": _fmt_duration(duration_minutes),
        "error": None,
    }
