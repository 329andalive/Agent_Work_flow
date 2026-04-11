"""
pwa_chat_actions.py — Server-side validator + decorator for chat actions

The chat agent (pwa_chat.py) classifies a tech's intent and may return
an "action" dict like:

    {"type": "mark_job_done", "params": {"customer_name": "alice"}}

This module takes that raw action and:

    1. Validates the type is one we know about.
    2. Resolves customer_name → real job_id by hitting today's route
       (multi-tenant safe — only the employee's own route is used).
    3. Builds a human-readable label for the chip ("Mark Alice done").
    4. Attaches the existing PWA endpoint + HTTP method, so the
       front-end just POSTs to whatever the chip carries.

Security model:
    - The chat agent NEVER calls a write path. It only suggests.
    - The front-end NEVER trusts the chip blindly — it tap-fires the
      existing /pwa/api/* endpoint, which re-verifies the session and
      multi-tenancy on its own.
    - This module's job is purely UX polish + cheap server-side
      resolution so the model doesn't have to know UUIDs.

Returns None if the action can't be validated. Callers must treat
None as "drop the chip, just show the reply text".
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


_ALLOWED = {
    "create_proposal",
    "mark_job_done",
    "start_job",
    "clock_in",
    "clock_out",
}


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _truncate(text: str, n: int) -> str:
    """Single-line truncate for chip labels."""
    s = (text or "").strip()
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def _resolve_job_for_employee(client_id: str, employee_id: str, customer_name: str) -> dict | None:
    """
    Look up today's route and fuzzy-match customer_name to a job.
    Returns the matching job row (with job_id, customer_name, etc.)
    or None if no match. Multi-tenant safe — uses the route helper
    that already filters on (client_id, worker_id).
    """
    if not customer_name or not customer_name.strip():
        return None
    try:
        from execution.dispatch_chain import get_todays_route, resolve_job
        route = get_todays_route(client_id, employee_id)
        return resolve_job(route, customer_name)
    except Exception as e:
        print(f"[{_ts()}] WARN pwa_chat_actions: route lookup failed — {e}")
        return None


# ---------------------------------------------------------------------------
# Per-action decorators
# ---------------------------------------------------------------------------

def _decorate_create_proposal(client_id: str, employee_id: str, params: dict) -> dict | None:
    """
    create_proposal needs at least a description. Customer name/phone/etc
    are optional — the existing /pwa/api/job/new endpoint will create
    or look up the customer based on what we pass.

    HARD RULE: amount must pass through to the endpoint so Claude never
    re-prices what the tech already stated. Previously this field was
    stripped here, causing Claude to reprice every job sent via the chat.
    """
    description = (params.get("description") or "").strip()
    if not description:
        return None

    customer_name    = (params.get("customer_name") or "").strip()
    customer_phone   = (params.get("customer_phone") or "").strip()
    customer_address = (params.get("customer_address") or "").strip()
    customer_email   = (params.get("customer_email") or "").strip()
    amount           = params.get("amount")

    # Coerce amount to a clean float or None
    clean_amount = None
    if amount is not None:
        try:
            v = float(amount)
            if v > 0:
                clean_amount = v
        except (TypeError, ValueError):
            pass

    # Build chip label
    if clean_amount:
        label = f"Create estimate · ${int(clean_amount)}"
    elif customer_name:
        label = f"Create estimate for {_truncate(customer_name, 24)}"
    else:
        label = "Create estimate"

    return {
        "type": "create_proposal",
        "label": label,
        "params": {
            "description":    description,
            "customer_name":  customer_name,
            "customer_phone": customer_phone,
            "customer_address": customer_address,
            "customer_email": customer_email,
            # amount flows through to pwa_new_job → proposal_agent as
            # explicit_amount, which bypasses Claude pricing entirely.
            # None means "tech didn't state a price — use pricebook standard."
            "amount": clean_amount,
        },
        "endpoint": "/pwa/api/job/new",
        "method": "POST",
    }


def _decorate_mark_job_done(client_id: str, employee_id: str, params: dict) -> dict | None:
    """
    mark_job_done needs to resolve customer_name → job_id from today's
    route. If we can't match, return None and the front-end shows just
    the reply text.
    """
    customer_name = (params.get("customer_name") or "").strip()
    job = _resolve_job_for_employee(client_id, employee_id, customer_name)
    if not job:
        return None
    job_id = job.get("job_id")
    if not job_id:
        return None

    matched_name = job.get("customer_name") or customer_name
    return {
        "type": "mark_job_done",
        "label": f"Mark {_truncate(matched_name, 20)} done",
        "params": {
            "job_id": job_id,
            "customer_name": matched_name,
        },
        "endpoint": f"/pwa/api/job/{job_id}/done",
        "method": "POST",
    }


def _decorate_start_job(client_id: str, employee_id: str, params: dict) -> dict | None:
    """Same resolution as mark_job_done, different endpoint."""
    customer_name = (params.get("customer_name") or "").strip()
    job = _resolve_job_for_employee(client_id, employee_id, customer_name)
    if not job:
        return None
    job_id = job.get("job_id")
    if not job_id:
        return None

    matched_name = job.get("customer_name") or customer_name
    return {
        "type": "start_job",
        "label": f"Start {_truncate(matched_name, 20)}",
        "params": {
            "job_id": job_id,
            "customer_name": matched_name,
        },
        "endpoint": f"/pwa/api/job/{job_id}/start",
        "method": "POST",
    }


def _decorate_clock_in(client_id: str, employee_id: str, params: dict) -> dict:
    return {
        "type": "clock_in",
        "label": "Clock in",
        "params": {},
        "endpoint": "/pwa/api/clock/in",
        "method": "POST",
    }


def _decorate_clock_out(client_id: str, employee_id: str, params: dict) -> dict:
    return {
        "type": "clock_out",
        "label": "Clock out",
        "params": {},
        "endpoint": "/pwa/api/clock/out",
        "method": "POST",
    }


_DISPATCH = {
    "create_proposal": _decorate_create_proposal,
    "mark_job_done":   _decorate_mark_job_done,
    "start_job":       _decorate_start_job,
    "clock_in":        _decorate_clock_in,
    "clock_out":       _decorate_clock_out,
}


def decorate_action(client_id: str, employee_id: str,
                    action_type: str, params: dict) -> dict | None:
    """
    Validate + decorate a raw action proposal from the chat agent.

    Returns a decorated chip dict ready for the PWA, or None if the action
    can't be validated. None means "no chip — just show the reply text".
    """
    if action_type not in _ALLOWED:
        return None
    if not isinstance(params, dict):
        params = {}

    decorator = _DISPATCH.get(action_type)
    if not decorator:
        return None

    try:
        return decorator(client_id, employee_id, params)
    except Exception as e:
        print(f"[{_ts()}] WARN pwa_chat_actions: decorator {action_type} failed — {e}")
        return None
