"""
dispatch_suggestion.py — Phase 2 AI learning for dispatch suggestions

Analyzes past dispatch patterns and suggests worker assignments for today's
jobs using Claude Haiku. Only activates after 30+ dispatch sessions exist
in dispatch_log — before that, returns [] silently.

Phase 1: session count < 30 → return [], log silently
Phase 2: session count >= 30 → call Haiku, return suggestions
Phase 3: autonomous (future) → requires explicit owner opt-in flag

Usage:
    from execution.dispatch_suggestion import get_suggestions
    suggestions = get_suggestions(client_phone, todays_jobs, workers)
    # → [{"job_id": "...", "worker_id": "...", "confidence": 0.85, "reason": "..."}, ...]
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
    client_phone: str,
    todays_jobs: list,
    workers: list,
) -> list:
    """
    Analyze past dispatch patterns and suggest worker assignments.

    Args:
        client_phone: E.164 phone (tenant identifier)
        todays_jobs:  List of scheduled_jobs dicts for today
        workers:      List of active worker dicts

    Returns:
        List of dicts: [{job_id, worker_id, confidence, reason}, ...]
        Empty list if Phase 2 not yet active or on any error.
    """
    if not todays_jobs or not workers:
        return []

    sb = get_supabase()

    # ── Check session count ─────────────────────────────────────────
    try:
        count_result = sb.table("dispatch_log").select(
            "id", count="exact"
        ).eq("client_phone", client_phone).execute()
        session_count = count_result.count if hasattr(count_result, 'count') else len(count_result.data or [])
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

    # ── Build pattern data from past assignments ────────────────────
    lookback = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()

    zone_worker_freq = Counter()   # (zone, worker_id) → count
    type_worker_freq = Counter()   # (job_type, worker_id) → count

    try:
        assignments = sb.table("route_assignments").select(
            "job_id, worker_id"
        ).eq("client_phone", client_phone).gte("assigned_at", lookback).execute()

        if assignments.data:
            job_ids = list(set(a["job_id"] for a in assignments.data))

            # Batch-fetch job details
            job_details = {}
            # Supabase .in_() has limits, batch in chunks of 50
            for i in range(0, len(job_ids), 50):
                chunk = job_ids[i:i+50]
                try:
                    rows = sb.table("scheduled_jobs").select(
                        "id, zone_cluster, job_type"
                    ).in_("id", chunk).execute()
                    for j in (rows.data or []):
                        job_details[j["id"]] = j
                except Exception:
                    pass

            for a in assignments.data:
                jid = a["job_id"]
                wid = a["worker_id"]
                if jid in job_details:
                    zone = job_details[jid].get("zone_cluster", "Unknown")
                    jtype = job_details[jid].get("job_type", "unknown")
                    zone_worker_freq[(zone, wid)] += 1
                    type_worker_freq[(jtype, wid)] += 1

    except Exception as e:
        print(f"[{timestamp()}] WARN dispatch_suggestion: pattern query failed — {e}")
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

    pattern_summary = (
        f"Zone preferences (last {LOOKBACK_DAYS} days, {session_count} sessions):\n"
        + "\n".join(zone_lines[:15]) + "\n\n"
        f"Job type preferences:\n"
        + "\n".join(type_lines[:15])
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
        "You are a dispatch assistant for a trade services business. "
        "You analyze past dispatch patterns to suggest optimal worker assignments. "
        "You must return ONLY a JSON array — no explanation, no markdown."
    )

    user_prompt = (
        f"Based on these historical patterns:\n{pattern_summary}\n\n"
        f"Today's jobs:\n" + "\n".join(job_lines) + "\n\n"
        f"Available workers:\n" + "\n".join(worker_lines) + "\n\n"
        f"Suggest the best worker assignment for each job. "
        f"Return a JSON array of objects, each with:\n"
        f'  "job_id": "<uuid>", "worker_id": "<uuid>", '
        f'"confidence": 0.0-1.0, "reason": "short string"\n\n'
        f"Rules:\n"
        f"- Match zone preferences when possible\n"
        f"- Match job type expertise when possible\n"
        f"- Balance workload across workers\n"
        f"- If a job has a requested time, prioritize workers near that zone\n"
        f"- confidence should reflect how strong the pattern match is\n"
        f"- Return ONLY the JSON array, nothing else"
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

        # Validate each suggestion has required keys
        valid = []
        for s in suggestions:
            if isinstance(s, dict) and s.get("job_id") and s.get("worker_id"):
                valid.append({
                    "job_id": s["job_id"],
                    "worker_id": s["worker_id"],
                    "confidence": float(s.get("confidence", 0.5)),
                    "reason": str(s.get("reason", ""))[:100],
                })

        print(f"[{timestamp()}] INFO dispatch_suggestion: {len(valid)} suggestions generated")
        return valid

    except (json.JSONDecodeError, ValueError) as e:
        print(f"[{timestamp()}] WARN dispatch_suggestion: JSON parse failed — {e}")
        return []
    except Exception as e:
        print(f"[{timestamp()}] ERROR dispatch_suggestion: Haiku call failed — {e}")
        return []
