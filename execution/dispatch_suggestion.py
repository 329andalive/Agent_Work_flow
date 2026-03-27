"""
dispatch_suggestion.py — Dispatch apprentice: learns from human patterns

Reads dispatch_decisions table to learn which workers get assigned which
jobs, zones, and types. After 30+ sessions, calls Claude Haiku with
the pattern summary to suggest assignments for today.

Phase 1: < 30 sessions → return [], log silently
Phase 2: >= 30 sessions → call Haiku, return suggestions with confidence
Phase 3: autonomous (future) → requires explicit owner opt-in flag

The learning loop:
  1. Human dispatches jobs on the drag-drop board
  2. POST /api/dispatch/send writes dispatch_decisions rows:
     job_id, worker_id, zone, type, was_suggested, was_overridden
  3. This module reads those rows and builds frequency patterns
  4. Claude Haiku reasons about patterns + today's jobs → suggestions
  5. Human sees suggestions as faded cards on the board
  6. Accepted suggestions → was_accepted=true (positive signal)
  7. Overridden suggestions → was_overridden=true (negative signal)
  8. Both feed back into step 3 for the next session

Usage:
    from execution.dispatch_suggestion import get_suggestions
    suggestions = get_suggestions(client_id, todays_jobs, workers)
"""

import os
import sys
import json
import re
from datetime import datetime, timezone, timedelta
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase
from execution.call_claude import call_claude


MINIMUM_SESSIONS = 30
LOOKBACK_DAYS = 90


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_suggestions(
    client_id: str,
    todays_jobs: list,
    workers: list,
) -> list:
    """
    Analyze past dispatch patterns and suggest worker assignments.

    Args:
        client_id:    UUID of the client (tenant identifier)
        todays_jobs:  List of job dicts for today (from get_todays_jobs)
        workers:      List of employee dicts (from get_workers)

    Returns:
        List of dicts: [{job_id, worker_id, confidence, reason}, ...]
        Empty list if Phase 2 not yet active or on any error.
    """
    if not todays_jobs or not workers:
        return []

    sb = get_supabase()

    # ── Check session count from dispatch_decisions ─────────────────
    try:
        # Count distinct sessions
        sessions = sb.table("dispatch_decisions").select(
            "session_id"
        ).eq("client_id", client_id).execute()
        unique_sessions = set(d.get("session_id") for d in (sessions.data or []))
        session_count = len(unique_sessions)
    except Exception as e:
        print(f"[{timestamp()}] WARN dispatch_suggestion: session count query failed — {e}")
        return []

    if session_count < MINIMUM_SESSIONS:
        print(
            f"[{timestamp()}] INFO dispatch_suggestion: Phase 2 not yet active — "
            f"{session_count} sessions logged, need {MINIMUM_SESSIONS}."
        )
        return []

    print(f"[{timestamp()}] INFO dispatch_suggestion: Phase 2 active — {session_count} sessions. Building patterns.")

    # ── Build pattern data from dispatch_decisions ──────────────────
    lookback = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()

    zone_worker_freq = Counter()    # (zone, worker_id) → count
    type_worker_freq = Counter()    # (job_type, worker_id) → count
    override_count = 0
    accepted_count = 0

    try:
        decisions = sb.table("dispatch_decisions").select(
            "worker_id, job_type, zone_cluster, was_suggested, was_accepted, was_overridden"
        ).eq("client_id", client_id).gte("created_at", lookback).execute()

        for d in (decisions.data or []):
            wid = d.get("worker_id", "")
            zone = d.get("zone_cluster", "Unknown")
            jtype = d.get("job_type", "unknown")

            zone_worker_freq[(zone, wid)] += 1
            type_worker_freq[(jtype, wid)] += 1

            if d.get("was_overridden"):
                override_count += 1
            if d.get("was_accepted"):
                accepted_count += 1

    except Exception as e:
        print(f"[{timestamp()}] WARN dispatch_suggestion: decisions query failed — {e}")
        return []

    # ── Build pattern summary for Claude ────────────────────────────
    worker_map = {w.get("id", ""): w.get("name", "Worker") for w in workers}

    zone_lines = []
    for (zone, wid), count in zone_worker_freq.most_common(20):
        wname = worker_map.get(wid, wid[:8])
        zone_lines.append(f"  {zone} → {wname}: {count} jobs")

    type_lines = []
    for (jtype, wid), count in type_worker_freq.most_common(20):
        wname = worker_map.get(wid, wid[:8])
        type_lines.append(f"  {jtype} → {wname}: {count} jobs")

    accuracy_note = ""
    total_suggested = accepted_count + override_count
    if total_suggested > 0:
        accuracy = round(accepted_count / total_suggested * 100)
        accuracy_note = f"\nSuggestion accuracy: {accuracy}% ({accepted_count} accepted, {override_count} overridden)"

    pattern_summary = (
        f"Zone preferences (last {LOOKBACK_DAYS} days, {session_count} sessions):\n"
        + "\n".join(zone_lines[:15]) + "\n\n"
        f"Job type preferences:\n"
        + "\n".join(type_lines[:15])
        + accuracy_note
    )

    # ── Build today's job list for Claude ───────────────────────────
    job_lines = []
    for j in todays_jobs:
        job_lines.append(json.dumps({
            "job_id": j.get("id", ""),
            "customer": j.get("customer_name", ""),
            "zone": j.get("zone_cluster", "Unknown"),
            "type": j.get("job_type", ""),
            "time": j.get("requested_time", ""),
        }))

    worker_lines = []
    for w in workers:
        worker_lines.append(json.dumps({
            "worker_id": w.get("id", ""),
            "name": w.get("name", ""),
        }))

    # ── Call Claude Haiku ───────────────────────────────────────────
    system_prompt = (
        "You are a dispatch apprentice for a trade services business. "
        "You analyze the human dispatcher's past decisions to suggest "
        "optimal worker assignments. Match the human's patterns — don't "
        "invent new ones. Return ONLY a JSON array — no explanation."
    )

    user_prompt = (
        f"Based on the dispatcher's historical patterns:\n{pattern_summary}\n\n"
        f"Today's jobs:\n" + "\n".join(job_lines) + "\n\n"
        f"Available workers:\n" + "\n".join(worker_lines) + "\n\n"
        f"Suggest the best worker for each job based on the patterns above.\n"
        f"Return a JSON array of objects:\n"
        f'  "job_id": "<uuid>", "worker_id": "<uuid>", '
        f'"confidence": 0.0-1.0, "reason": "short explanation"\n\n'
        f"Rules:\n"
        f"- Match zone preferences — if Dad always gets North, suggest Dad for North jobs\n"
        f"- Match job type expertise — if Jesse always gets repairs, suggest Jesse for repairs\n"
        f"- Balance workload — don't overload one worker\n"
        f"- Higher confidence when the pattern is strong and consistent\n"
        f"- Lower confidence when you're guessing\n"
        f"- Return ONLY the JSON array"
    )

    print(f"[{timestamp()}] INFO dispatch_suggestion: Calling Haiku for {len(todays_jobs)} jobs, {len(workers)} workers")

    try:
        raw = call_claude(system_prompt, user_prompt, model="haiku")

        if not raw:
            print(f"[{timestamp()}] WARN dispatch_suggestion: Haiku returned empty response")
            return []

        # Parse JSON — strip markdown fences if present
        cleaned = re.sub(r'^```(?:json)?\s*', '', raw.strip())
        cleaned = re.sub(r'\s*```$', '', cleaned)
        suggestions = json.loads(cleaned)

        if not isinstance(suggestions, list):
            print(f"[{timestamp()}] WARN dispatch_suggestion: Response is not a list")
            return []

        # Validate each suggestion
        valid = []
        for s in suggestions:
            if isinstance(s, dict) and s.get("job_id") and s.get("worker_id"):
                valid.append({
                    "job_id": s["job_id"],
                    "worker_id": s["worker_id"],
                    "confidence": float(s.get("confidence", 0.5)),
                    "reason": str(s.get("reason", ""))[:100],
                })

        print(f"[{timestamp()}] INFO dispatch_suggestion: {len(valid)} suggestions generated (accuracy context: {accuracy_note.strip() or 'no prior suggestions'})")
        return valid

    except (json.JSONDecodeError, ValueError) as e:
        print(f"[{timestamp()}] WARN dispatch_suggestion: JSON parse failed — {e}")
        return []
    except Exception as e:
        print(f"[{timestamp()}] ERROR dispatch_suggestion: Haiku call failed — {e}")
        return []
