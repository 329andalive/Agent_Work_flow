"""
context_loader.py — Stateful context assembly for single-pass architecture

One function: load_context(from_phone, client_phone) returns a structured
dict with everything needed to classify intent and dispatch an agent.
No Claude calls, no API calls — pure database lookups.

This module is not yet wired into any route. It will replace the current
routing-first approach by giving the Intent Resolver complete context
before any classification happens.

Usage:
    from execution.context_loader import load_context
    ctx = load_context(from_phone="+15555550100", client_phone="+15555550200")
"""

import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_personality_snapshot(personality_text: str) -> dict:
    """
    Extract structured pricing/policy data from the raw personality text.
    Returns a dict with labor_rate, travel_policy, common_jobs.
    All values are None/[] if parsing fails — never crashes.
    """
    snapshot = {
        "labor_rate": None,
        "travel_policy": None,
        "common_jobs": [],
    }

    if not personality_text:
        return snapshot

    try:
        # Extract hourly rate: "Hourly rate: $125/hr" or "$125/hr"
        rate_match = re.search(r'(?:hourly\s+rate|labor)[:\s]*\$(\d+(?:\.\d+)?)/hr', personality_text, re.IGNORECASE)
        if rate_match:
            snapshot["labor_rate"] = float(rate_match.group(1))

        # Extract travel policy
        travel_match = re.search(r'(?:travel|mileage)[:\s]*(.{10,80}?)(?:\n|$)', personality_text, re.IGNORECASE)
        if travel_match:
            snapshot["travel_policy"] = travel_match.group(0).strip()

        # Extract common job types from pricing sections
        job_patterns = re.findall(
            r'(?:pump[- ]?out|inspection|baffle|riser|jetting|camera|locate|repair|emergency|install)',
            personality_text,
            re.IGNORECASE,
        )
        snapshot["common_jobs"] = list(set(j.lower() for j in job_patterns))[:10]

    except Exception:
        pass  # Parsing is best-effort — never crash

    return snapshot


def load_context(from_phone: str, client_phone: str) -> dict:
    """
    Assemble the complete context object for a single inbound message.

    Args:
        from_phone:   E.164 phone of the sender (tech, owner, or customer)
        client_phone: E.164 phone of the business (Telnyx number)

    Returns:
        Structured context dict — always returned, even if partially populated.
    """
    sb = get_supabase()
    now_iso = datetime.now(timezone.utc).isoformat()

    # ── 1. Resolve client by phone ─────────────────────────────────────
    client_record = None
    try:
        result = sb.table("clients").select(
            "id, business_name, owner_name, phone, owner_mobile, personality"
        ).eq("phone", client_phone).execute()
        if result.data:
            client_record = result.data[0]
    except Exception as e:
        print(f"[{timestamp()}] WARN context_loader: client lookup failed — {e}")

    client_id = client_record["id"] if client_record else None

    client_ctx = {
        "id": client_id,
        "business_name": (client_record or {}).get("business_name", ""),
        "personality_snapshot": _parse_personality_snapshot(
            (client_record or {}).get("personality", "")
        ),
        "raw_personality": (client_record or {}).get("personality", ""),
    }

    # ── 2. Resolve tech/sender from employees table ────────────────────
    tech_ctx = {
        "id": None,
        "name": "Unknown",
        "role": "owner",
        "phone": from_phone,
        "client_id": client_id,
        "client_phone": client_phone,
        "found": False,
    }

    if client_id:
        try:
            result = sb.table("employees").select(
                "id, name, phone, role"
            ).eq("client_id", client_id).eq("phone", from_phone).eq("active", True).limit(1).execute()
            if result.data:
                emp = result.data[0]
                tech_ctx.update({
                    "id": emp["id"],
                    "name": emp.get("name", "Unknown"),
                    "role": emp.get("role", "field_tech"),
                    "found": True,
                })
        except Exception as e:
            print(f"[{timestamp()}] WARN context_loader: employee lookup failed — {e}")

        # Check if sender is the owner (phone or owner_mobile)
        if not tech_ctx["found"] and client_record:
            owner_phones = {
                client_record.get("phone", ""),
                client_record.get("owner_mobile", ""),
            }
            owner_phones.discard("")
            if from_phone in owner_phones:
                tech_ctx.update({
                    "name": client_record.get("owner_name", "Owner"),
                    "role": "owner",
                    "found": True,
                })

    # ── 3. Load active jobs ────────────────────────────────────────────
    active_jobs = []
    if client_id:
        try:
            result = sb.table("jobs").select(
                "id, job_type, job_description, status, customer_id, scheduled_date, created_at"
            ).eq("client_id", client_id).not_.in_(
                "status", ["completed", "cancelled"]
            ).order("created_at", desc=True).limit(5).execute()
            active_jobs = result.data or []
        except Exception as e:
            print(f"[{timestamp()}] WARN context_loader: active_jobs query failed — {e}")

    # ── 4. Load recent message thread ──────────────────────────────────
    # NOTE: messages table has no client_id column yet — query by phone
    # numbers only. This is a known schema gap. When client_id is added
    # to messages, add .eq("client_id", client_id) for multi-tenancy.
    recent_thread = []
    try:
        # Get messages where this phone is sender or recipient
        inbound = sb.table("messages").select(
            "id, from_number, to_number, body, created_at"
        ).eq("from_number", from_phone).order("created_at", desc=True).limit(5).execute()

        outbound = sb.table("messages").select(
            "id, from_number, to_number, body, created_at"
        ).eq("to_number", from_phone).order("created_at", desc=True).limit(5).execute()

        # Merge and sort
        all_msgs = (inbound.data or []) + (outbound.data or [])
        for msg in all_msgs:
            msg["direction"] = "inbound" if msg.get("from_number") == from_phone else "outbound"
        all_msgs.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        recent_thread = all_msgs[:5]
    except Exception as e:
        print(f"[{timestamp()}] WARN context_loader: message thread query failed — {e}")

    # ── 5. Load pending state ──────────────────────────────────────────
    pending_clarification = None
    open_proposals = 0
    unpaid_invoices = 0

    if client_id:
        # Pending clarification
        try:
            result = sb.table("pending_clarifications").select("*").eq(
                "employee_phone", from_phone
            ).gt("expires_at", now_iso).order("created_at", desc=True).limit(1).execute()
            if result.data:
                pending_clarification = result.data[0]
        except Exception as e:
            print(f"[{timestamp()}] WARN context_loader: clarification query failed — {e}")

        # Open proposals
        try:
            result = sb.table("proposals").select("id").eq(
                "client_id", client_id
            ).not_.in_("status", ["accepted", "declined"]).execute()
            open_proposals = len(result.data or [])
        except Exception as e:
            print(f"[{timestamp()}] WARN context_loader: open proposals count failed — {e}")

        # Unpaid invoices
        try:
            result = sb.table("invoices").select("id").eq(
                "client_id", client_id
            ).neq("status", "paid").execute()
            unpaid_invoices = len(result.data or [])
        except Exception as e:
            print(f"[{timestamp()}] WARN context_loader: unpaid invoices count failed — {e}")

    # ── 6. Assemble and return ─────────────────────────────────────────
    cold_start = len(active_jobs) == 0 and len(recent_thread) == 0

    ctx = {
        "tech": tech_ctx,
        "client": client_ctx,
        "active_jobs": active_jobs,
        "recent_thread": recent_thread,
        "pending": {
            "clarification": pending_clarification,
            "open_proposals": open_proposals,
            "unpaid_invoices": unpaid_invoices,
        },
        "cold_start": cold_start,
        "loaded_at": now_iso,
    }

    print(
        f"[{timestamp()}] INFO context_loader: loaded context for {from_phone} | "
        f"cold_start={cold_start} | jobs={len(active_jobs)} | thread={len(recent_thread)}"
    )

    return ctx


# ---------------------------------------------------------------------------
# Standalone test — run directly to verify with real data
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    # Replace with your test phone numbers from .env or Supabase
    ctx = load_context(
        from_phone=os.environ.get("TEST_OWNER_MOBILE", "+15555550100"),
        client_phone=os.environ.get("TELNYX_PHONE_NUMBER", "+15555550200"),
    )
    import json
    print(json.dumps(ctx, indent=2, default=str))
