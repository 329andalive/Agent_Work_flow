"""
guided_estimate.py — State machine for the guided estimate flow.

This module handles the multi-turn conversation that walks a tech through
creating an estimate without ever letting the AI invent a price.

State flow:
    IDLE
      → start()              creates estimate_session, asks "Who's the customer?"
    gathering
      → handle_input()       routes to the right handler based on current_step
    confirming_customer      customer found, waiting for yes/no
    awaiting_job_type        customer confirmed, asking job type
    awaiting_price           job type set, showing history, asking price
    awaiting_line_items      price set, asking for more line items or done
    review                   all data collected, showing summary chip
    done / cancelled         terminal states

Design rules (non-negotiable):
    - No Claude calls for pricing. Ever.
    - One Haiku call: job type classification from keywords.
    - All state lives in estimate_sessions table — not in memory.
    - Tech provides every price. History is reference only, never pre-filled.
    - Returns same {reply, action} shape as pwa_chat.py so the chat UI
      renders it identically.

Usage (from pwa_chat.py):
    from execution.guided_estimate import handle_input, start, get_active_session

    session = get_active_session(client_id, employee_id, chat_session_id)
    if session:
        return handle_input(session, user_message, client_id, employee_id)
    elif _is_estimate_intent(user_message):
        return start(client_id, employee_id, chat_session_id)
"""

import os
import re
import sys
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase
from execution.schema import (
    EstimateSessions as ES,
    JobPricingHistory as JPH,
    Customers as C,
    Clients as CL,
)


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Intent detection — is this message trying to start an estimate?
# ---------------------------------------------------------------------------

_ESTIMATE_TRIGGERS = re.compile(
    r'\b(create|new|start|make|write|draft)\s+(estimate|quote|bid|proposal)\b'
    r'|^estimate\b'
    r'|\bestimate\s+for\b',
    re.IGNORECASE,
)


def is_estimate_intent(message: str) -> bool:
    """Return True if the tech's message is trying to start a guided estimate."""
    return bool(_ESTIMATE_TRIGGERS.search(message.strip()))


# ---------------------------------------------------------------------------
# Global command detection — intercepts before state-specific parsing
# ---------------------------------------------------------------------------

_CANCEL_RE = re.compile(r'\b(cancel|stop|nevermind|never mind|abort|quit)\b', re.IGNORECASE)
_DONE_RE   = re.compile(r'^\s*(done|no|no more|that\'?s?\s*(it|all)|finish|finished)\s*$', re.IGNORECASE)


def _is_cancel(text: str) -> bool:
    return bool(_CANCEL_RE.search(text))


def _is_done(text: str) -> bool:
    return bool(_DONE_RE.match(text))


# ---------------------------------------------------------------------------
# DB helpers — all queries use schema constants
# ---------------------------------------------------------------------------

def get_active_session(client_id: str, employee_id: str, session_id: str) -> dict | None:
    """
    Return an in-progress estimate session for this employee + chat session,
    or None if no active session exists.

    Multi-tenant safe: filters by client_id first.
    """
    try:
        sb = get_supabase()
        result = sb.table(ES.TABLE).select("*").eq(
            ES.CLIENT_ID, client_id
        ).eq(
            ES.EMPLOYEE_ID, employee_id
        ).eq(
            ES.SESSION_ID, session_id
        ).not_.in_(ES.STATUS, ["done", "cancelled"]).order(
            ES.CREATED_AT, desc=True
        ).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[{_ts()}] WARN guided_estimate: get_active_session failed — {e}")
        return None


def _create_session(client_id: str, employee_id: str, session_id: str) -> dict | None:
    """Create a new estimate session and return the row."""
    try:
        sb = get_supabase()
        result = sb.table(ES.TABLE).insert({
            ES.CLIENT_ID:   client_id,
            ES.EMPLOYEE_ID: employee_id,
            ES.SESSION_ID:  session_id,
            ES.STATUS:      "gathering",
            ES.CURRENT_STEP: "ask_customer",
            ES.LINE_ITEMS:  [],
        }).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[{_ts()}] ERROR guided_estimate: _create_session failed — {e}")
        return None


def _update_session(session_id_pk: str, updates: dict) -> bool:
    """Update estimate session by primary key id."""
    try:
        sb = get_supabase()
        updates[ES.UPDATED_AT] = datetime.utcnow().isoformat()
        sb.table(ES.TABLE).update(updates).eq(ES.ID, session_id_pk).execute()
        return True
    except Exception as e:
        print(f"[{_ts()}] ERROR guided_estimate: _update_session failed — {e}")
        return False


def _cancel_session(session_id_pk: str) -> bool:
    return _update_session(session_id_pk, {ES.STATUS: "cancelled"})


# ---------------------------------------------------------------------------
# Pricing history lookup — the "last 3 averaged $X" reference
# ---------------------------------------------------------------------------

def get_pricing_reference(client_id: str, job_type: str,
                          customer_id: str | None = None) -> str | None:
    """
    Query job_pricing_history and return a human-readable reference string.

    Priority:
      1. Last 3 jobs for this specific customer + job type
      2. Last 5 shop-wide jobs for this job type
      3. None (no history yet)

    Returns a string like:
      "Last 3 pump outs for this customer averaged $285 (range $275–$300)."
      "Your shop's last 5 pump outs averaged $310."
    Or None if no history exists.

    IMPORTANT: This is reference text only. It is never pre-filled as a
    price. The tech reads it and types their own number.
    """
    try:
        sb = get_supabase()

        # Try customer-specific history first
        if customer_id:
            result = sb.table(JPH.TABLE).select(
                JPH.AMOUNT
            ).eq(JPH.CLIENT_ID, client_id).eq(
                JPH.CUSTOMER_ID, customer_id
            ).eq(JPH.JOB_TYPE, job_type).order(
                JPH.COMPLETED_AT, desc=True
            ).limit(3).execute()

            rows = result.data or []
            if len(rows) >= 2:
                amounts = [float(r[JPH.AMOUNT]) for r in rows]
                avg = round(sum(amounts) / len(amounts))
                lo, hi = int(min(amounts)), int(max(amounts))
                job_label = job_type.replace("_", " ")
                if lo == hi:
                    return f"Last {len(amounts)} {job_label}s for this customer: ${avg}."
                return (
                    f"Last {len(amounts)} {job_label}s for this customer "
                    f"averaged ${avg} (range ${lo}–${hi})."
                )

        # Fall back to shop-wide history
        result = sb.table(JPH.TABLE).select(
            JPH.AMOUNT
        ).eq(JPH.CLIENT_ID, client_id).eq(
            JPH.JOB_TYPE, job_type
        ).order(JPH.COMPLETED_AT, desc=True).limit(5).execute()

        rows = result.data or []
        if len(rows) >= 2:
            amounts = [float(r[JPH.AMOUNT]) for r in rows]
            avg = round(sum(amounts) / len(amounts))
            job_label = job_type.replace("_", " ")
            return f"Your shop's last {len(amounts)} {job_label}s averaged ${avg}."

        return None

    except Exception as e:
        print(f"[{_ts()}] WARN guided_estimate: get_pricing_reference failed — {e}")
        return None


# ---------------------------------------------------------------------------
# Customer lookup helpers
# ---------------------------------------------------------------------------

def _find_customers(client_id: str, query: str) -> list[dict]:
    """
    Fuzzy search customers by name. Returns up to 5 matches.
    Multi-tenant safe.
    """
    if not query or not query.strip():
        return []
    try:
        sb = get_supabase()
        result = sb.table(C.TABLE).select(
            f"{C.ID}, {C.CUSTOMER_NAME}, {C.CUSTOMER_PHONE}, {C.CUSTOMER_ADDRESS}"
        ).eq(C.CLIENT_ID, client_id).ilike(
            C.CUSTOMER_NAME, f"%{query.strip()}%"
        ).order(C.CREATED_AT, desc=True).limit(5).execute()
        return result.data or []
    except Exception as e:
        print(f"[{_ts()}] WARN guided_estimate: _find_customers failed — {e}")
        return []


# ---------------------------------------------------------------------------
# Job type classification — one Haiku call, keyword-first
# ---------------------------------------------------------------------------

# Keyword map for sewer_drain vertical — deterministic, no LLM needed
_JOB_TYPE_KEYWORDS = {
    "pump_out":           ["pump", "pumping", "pump out", "empty", "emptied"],
    "baffle_replacement": ["baffle", "baffler"],
    "riser_installation": ["riser", "risers"],
    "line_repair":        ["line repair", "belly", "outlet line", "inlet line"],
    "inspection":         ["inspect", "inspection", "check", "camera"],
    "locate":             ["locate", "find tank", "mark", "lost lid"],
    "hydro_jetting":      ["hydro", "jetting", "jet"],
    "drain_cleaning":     ["drain clean", "cleanout", "clean out"],
    "emergency":          ["emergency", "backup", "overflow", "flooding", "alarm", "urgent"],
}


def _classify_job_type(text: str, vertical_key: str = "sewer_drain") -> str | None:
    """
    Classify job type from tech input. Keyword-first, Haiku fallback.
    Returns a job_type key like "pump_out" or None if unrecognised.
    """
    text_lower = text.lower()

    # Keyword match — deterministic, free
    for job_type, keywords in _JOB_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return job_type

    # Haiku fallback — only if keywords didn't match
    try:
        from execution.call_claude import call_claude
        valid = list(_JOB_TYPE_KEYWORDS.keys()) + ["other"]
        response = call_claude(
            system_prompt=(
                "Classify the trade job type from the tech's message. "
                f"Reply with ONLY one of these exact keys: {', '.join(valid)}. "
                "No explanation. No punctuation."
            ),
            user_prompt=f"Job description: {text}",
            model="haiku",
            max_tokens=20,
        )
        if response:
            candidate = response.strip().lower().replace(" ", "_")
            if candidate in _JOB_TYPE_KEYWORDS:
                return candidate
    except Exception as e:
        print(f"[{_ts()}] WARN guided_estimate: Haiku job type classification failed — {e}")

    return None


# ---------------------------------------------------------------------------
# Response builder — keeps the return shape consistent with pwa_chat.py
# ---------------------------------------------------------------------------

def _reply(text: str, action: dict | None = None) -> dict:
    """Build a guided_estimate response in the same shape pwa_chat.chat() returns."""
    return {
        "success": True,
        "reply": text,
        "action": action,
        "model": "guided_flow",   # signals to the UI this came from the state machine
        "system_prompt_chars": 0,
        "error": None,
    }


def _error(text: str) -> dict:
    return {"success": False, "reply": text, "action": None,
            "model": "guided_flow", "system_prompt_chars": 0, "error": text}


# ---------------------------------------------------------------------------
# Entry point — start a new guided estimate session
# ---------------------------------------------------------------------------

def start(client_id: str, employee_id: str, session_id: str) -> dict:
    """
    Create a new estimate session and return the first prompt.
    Called by pwa_chat.chat() when it detects estimate intent.
    """
    session = _create_session(client_id, employee_id, session_id)
    if not session:
        return _error("Couldn't start the estimate flow. Try again.")

    return _reply("Who's the customer?")


# ---------------------------------------------------------------------------
# Main dispatcher — routes each turn to the right handler
# ---------------------------------------------------------------------------

def handle_input(session: dict, user_message: str,
                 client_id: str, employee_id: str) -> dict:
    """
    Route the tech's message to the correct state handler based on
    the session's current_step. Global commands (cancel, done) are
    intercepted before reaching state-specific logic.
    """
    session_pk   = session[ES.ID]
    current_step = session.get(ES.CURRENT_STEP, "ask_customer")
    status       = session.get(ES.STATUS, "gathering")

    # Global: cancel from any state
    if _is_cancel(user_message):
        _cancel_session(session_pk)
        return _reply("Estimate cancelled. Start a new one anytime.")

    # Route by current step
    if current_step == "ask_customer":
        return _handle_customer_input(session, user_message, client_id)

    if current_step == "confirm_customer":
        return _handle_customer_confirm(session, user_message, client_id)

    if current_step == "disambiguate_customer":
        return _handle_customer_disambiguate(session, user_message, client_id)

    if current_step == "ask_job_type":
        return _handle_job_type_input(session, user_message, client_id)

    if current_step == "ask_price":
        return _handle_price_input(session, user_message, client_id)

    if current_step == "ask_line_items":
        return _handle_line_item_input(session, user_message, client_id, employee_id)

    if current_step == "ask_notes":
        return _handle_notes_input(session, user_message, client_id, employee_id)

    # Shouldn't reach here — reset to customer ask
    _update_session(session_pk, {ES.CURRENT_STEP: "ask_customer"})
    return _reply("Let's start over. Who's the customer?")


# ---------------------------------------------------------------------------
# State handlers
# ---------------------------------------------------------------------------

def _handle_customer_input(session: dict, text: str, client_id: str) -> dict:
    """S1: Tech typed a customer name. Search the DB."""
    session_pk = session[ES.ID]
    matches = _find_customers(client_id, text)

    if not matches:
        _update_session(session_pk, {
            ES.CURRENT_STEP: "ask_customer",
        })
        return _reply(
            f"I don't have a customer matching '{text}'. "
            "Try a different name, or type 'new' to add them."
        )

    if len(matches) == 1:
        cust = matches[0]
        name = cust.get(C.CUSTOMER_NAME, "")
        addr = cust.get(C.CUSTOMER_ADDRESS, "")
        addr_part = f" — {addr}" if addr else ""
        _update_session(session_pk, {
            ES.CURRENT_STEP: "confirm_customer",
            ES.STATUS: "confirming_customer",
            # Store candidate in notes field temporarily
            "notes": json.dumps({"candidate": cust}),
        })
        return _reply(f"Found {name}{addr_part}. Correct?")

    # Multiple matches — show numbered list
    lines = ["I found a few matches:"]
    for i, c in enumerate(matches[:5], 1):
        addr = c.get(C.CUSTOMER_ADDRESS, "")
        lines.append(f"  {i}) {c.get(C.CUSTOMER_NAME, '')} — {addr}")
    lines.append("Which one? (or 'new' to add a new customer)")

    _update_session(session_pk, {
        ES.CURRENT_STEP: "disambiguate_customer",
        ES.STATUS: "gathering",
        "notes": json.dumps({"candidates": matches[:5]}),
    })
    return _reply("\n".join(lines))


def _handle_customer_confirm(session: dict, text: str, client_id: str) -> dict:
    """S2: Tech said yes/no to a customer match."""
    session_pk = session[ES.ID]
    text_lower = text.strip().lower()

    yes = text_lower in ("yes", "y", "yep", "yeah", "correct", "right", "yup", "sure")
    no  = text_lower in ("no", "n", "nope", "wrong", "incorrect")

    if no:
        _update_session(session_pk, {
            ES.CURRENT_STEP: "ask_customer",
            ES.STATUS: "gathering",
            "notes": None,
        })
        return _reply("Who's the customer?")

    if yes:
        # Pull candidate from notes
        try:
            notes_raw = session.get(ES.NOTES) or "{}"
            stored = json.loads(notes_raw) if isinstance(notes_raw, str) else notes_raw
            cust = stored.get("candidate") or {}
        except Exception:
            cust = {}

        _update_session(session_pk, {
            ES.CUSTOMER_ID: cust.get(C.ID),
            ES.CUSTOMER_CONFIRMED: True,
            ES.CURRENT_STEP: "ask_job_type",
            ES.STATUS: "gathering",
            "notes": None,
        })
        return _reply("What type of job?")

    # Ambiguous — re-ask
    return _reply("Is that the right customer? (yes or no)")


def _handle_customer_disambiguate(session: dict, text: str, client_id: str) -> dict:
    """S3: Tech picked from a numbered list of customer matches."""
    session_pk = session[ES.ID]

    try:
        stored = json.loads(session.get(ES.NOTES) or "{}")
        candidates = stored.get("candidates", [])
    except Exception:
        candidates = []

    # Try number pick
    pick = None
    stripped = text.strip()
    if stripped.isdigit():
        idx = int(stripped) - 1
        if 0 <= idx < len(candidates):
            pick = candidates[idx]

    # Try "new"
    if stripped.lower() == "new":
        _update_session(session_pk, {
            ES.CURRENT_STEP: "ask_customer",
            ES.STATUS: "gathering",
            "notes": json.dumps({"create_new": True}),
        })
        return _reply(
            "New customer — what's their name?"
        )

    if not pick:
        return _reply(
            f"Pick a number (1–{len(candidates)}) or type 'new'."
        )

    name = pick.get(C.CUSTOMER_NAME, "")
    addr = pick.get(C.CUSTOMER_ADDRESS, "")
    addr_part = f" — {addr}" if addr else ""

    _update_session(session_pk, {
        ES.CURRENT_STEP: "confirm_customer",
        ES.STATUS: "confirming_customer",
        "notes": json.dumps({"candidate": pick}),
    })
    return _reply(f"Found {name}{addr_part}. Correct?")


def _handle_job_type_input(session: dict, text: str, client_id: str) -> dict:
    """S6: Tech typed a job type. Classify, load history, ask for price."""
    session_pk  = session[ES.ID]
    customer_id = session.get(ES.CUSTOMER_ID)

    # Load vertical key from client record
    vertical_key = "sewer_drain"
    try:
        sb = get_supabase()
        cr = sb.table(CL.TABLE).select(CL.TRADE_VERTICAL).eq(
            CL.ID, client_id
        ).limit(1).execute()
        if cr.data and cr.data[0].get(CL.TRADE_VERTICAL):
            vertical_key = cr.data[0][CL.TRADE_VERTICAL]
    except Exception:
        pass

    job_type = _classify_job_type(text, vertical_key)

    if not job_type or job_type == "other":
        return _reply(
            f"I don't recognise '{text}' as a job type. "
            "Try something like: pump out, baffle replacement, inspection, riser installation."
        )

    # Get pricing reference — reference text only, never pre-filled
    price_ref = get_pricing_reference(client_id, job_type, customer_id)

    _update_session(session_pk, {
        ES.JOB_TYPE: job_type,
        ES.JOB_TYPE_CONFIRMED: True,
        ES.CURRENT_STEP: "ask_price",
        ES.STATUS: "awaiting_price",
    })

    job_label = job_type.replace("_", " ").title()
    if price_ref:
        return _reply(f"{price_ref}\nWhat's your price for the {job_label}?")
    return _reply(f"What's your price for the {job_label}?")


def _handle_price_input(session: dict, text: str, client_id: str) -> dict:
    """S7a: Tech entered a price. Parse and confirm."""
    session_pk = session[ES.ID]

    # Parse dollar amount — accepts "$325", "325", "three twenty five" (voice)
    amount = _parse_dollar_amount(text)

    if amount is None:
        return _reply("I didn't catch a price. Enter a dollar amount, like 325.")

    if amount <= 0:
        return _reply("Price needs to be greater than $0. What's your price?")

    if amount > 100_000:
        return _reply(f"${amount:,.0f} looks high — double-check and re-enter.")

    _update_session(session_pk, {
        ES.PRIMARY_PRICE: amount,
        ES.CURRENT_STEP: "ask_line_items",
        ES.STATUS: "awaiting_line_items",
    })

    return _reply(
        f"Got it — ${amount:,.0f}. "
        "Any additional line items? (e.g. 'disposal fee $45') "
        "Or say 'done'."
    )


def _handle_line_item_input(session: dict, text: str,
                             client_id: str, employee_id: str) -> dict:
    """S8: Tech adding line items or saying done."""
    session_pk = session[ES.ID]

    if _is_done(text):
        # Skip to notes
        _update_session(session_pk, {ES.CURRENT_STEP: "ask_notes"})
        return _reply("Any notes to add? Or say 'done'.")

    # Try to parse "description $amount"
    parsed = _parse_line_item(text)
    if not parsed:
        return _reply(
            "I didn't catch a price for that. "
            "Try: 'disposal fee $45' or say 'done' if you're finished."
        )

    desc, amount = parsed

    # Append to line_items
    try:
        existing = session.get(ES.LINE_ITEMS) or []
        if isinstance(existing, str):
            existing = json.loads(existing)
        existing.append({"description": desc, "amount": amount})
        _update_session(session_pk, {ES.LINE_ITEMS: existing})
    except Exception as e:
        print(f"[{_ts()}] WARN guided_estimate: line item append failed — {e}")

    return _reply(
        f"Added '{desc}' — ${amount:,.0f}. "
        "Anything else? Or say 'done'."
    )


def _handle_notes_input(session: dict, text: str,
                         client_id: str, employee_id: str) -> dict:
    """S9: Optional notes, then build the review chip."""
    session_pk = session[ES.ID]

    if not _is_done(text):
        _update_session(session_pk, {ES.NOTES: text.strip()})

    # Build review chip
    return _build_review_chip(session_pk, client_id, employee_id)


def _build_review_chip(session_pk: str, client_id: str, employee_id: str) -> dict:
    """
    S10: Compile all collected data and return a create_proposal chip.
    The chip fires the existing /pwa/api/job/new endpoint with
    explicit_amount so Claude never re-prices.
    """
    try:
        sb = get_supabase()
        result = sb.table(ES.TABLE).select("*").eq(ES.ID, session_pk).single().execute()
        session = result.data
    except Exception as e:
        return _error(f"Couldn't load estimate data — {e}")

    if not session:
        return _error("Estimate session not found.")

    customer_id  = session.get(ES.CUSTOMER_ID)
    job_type     = session.get(ES.JOB_TYPE, "service")
    primary_price = float(session.get(ES.PRIMARY_PRICE) or 0)
    line_items   = session.get(ES.LINE_ITEMS) or []
    if isinstance(line_items, str):
        line_items = json.loads(line_items)
    notes        = session.get(ES.NOTES, "")

    # Look up customer details for the chip
    customer_name = ""
    customer_phone = ""
    customer_address = ""
    if customer_id:
        try:
            cr = sb.table(C.TABLE).select(
                f"{C.CUSTOMER_NAME}, {C.CUSTOMER_PHONE}, {C.CUSTOMER_ADDRESS}"
            ).eq(C.ID, customer_id).single().execute()
            if cr.data:
                customer_name    = cr.data.get(C.CUSTOMER_NAME, "")
                customer_phone   = cr.data.get(C.CUSTOMER_PHONE, "")
                customer_address = cr.data.get(C.CUSTOMER_ADDRESS, "")
        except Exception:
            pass

    # Build description from job type + line items
    job_label = job_type.replace("_", " ").title()
    line_desc  = "; ".join(f"{li['description']} ${li['amount']:.0f}" for li in line_items)
    description = f"{job_label}" + (f" + {line_desc}" if line_desc else "")

    # Total = primary price + all line items
    line_total = sum(float(li.get("amount", 0)) for li in line_items)
    total      = primary_price + line_total

    # Build the review summary
    lines = [
        f"Customer:  {customer_name or 'Unknown'}",
        f"Job:       {job_label} — ${primary_price:,.0f}",
    ]
    for li in line_items:
        lines.append(f"           + {li['description']} — ${float(li['amount']):,.0f}")
    lines.append(f"Total:     ${total:,.0f}")
    summary = "\n".join(lines)

    # Mark session done
    _update_session(session_pk, {ES.STATUS: "done"})

    action = {
        "type":  "create_proposal",
        "label": f"Send estimate · ${int(total)}",
        "params": {
            "description":       description + (f"\n\nNotes: {notes}" if notes else ""),
            "customer_name":     customer_name,
            "customer_phone":    customer_phone,
            "customer_address":  customer_address,
            "amount":            total,
            # Pass line items so proposal_agent can build structured line items
            "line_items":        json.dumps([
                {"description": job_label, "amount": primary_price},
                *line_items,
            ]),
        },
        "endpoint": "/pwa/api/job/new",
        "method":   "POST",
    }

    return _reply(
        f"Here's the estimate:\n\n{summary}\n\nTap to review and send.",
        action=action,
    )


# ---------------------------------------------------------------------------
# Parsing utilities
# ---------------------------------------------------------------------------

def _parse_dollar_amount(text: str) -> float | None:
    """
    Parse a dollar amount from tech input.
    Handles: "$325", "325", "325.00", "1,250"
    Does NOT handle voice-to-text word numbers ("three twenty five") —
    that's a Phase 2 enhancement.
    """
    if not text:
        return None
    # Strip common noise
    cleaned = text.strip().replace(",", "").replace("$", "")
    # Match the first numeric value
    match = re.search(r'\b(\d+(?:\.\d{1,2})?)\b', cleaned)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


def _parse_line_item(text: str) -> tuple[str, float] | None:
    """
    Parse a line item from tech input.
    Expects format: "description $amount" or "description amount"

    Examples:
      "disposal fee $45"    → ("disposal fee", 45.0)
      "travel charge 75"    → ("travel charge", 75.0)
      "baffle"              → None (no amount)

    Returns (description, amount) tuple or None if no amount found.
    """
    if not text or not text.strip():
        return None

    # Match a dollar amount anywhere in the string
    amount_match = re.search(r'\$?(\d+(?:\.\d{1,2})?)', text)
    if not amount_match:
        return None

    try:
        amount = float(amount_match.group(1))
    except ValueError:
        return None

    # Description = everything before the amount match, stripped
    desc = text[:amount_match.start()].strip().rstrip("- ").strip()
    if not desc:
        # Description = everything after the amount
        desc = text[amount_match.end():].strip()
    if not desc:
        return None

    # Capitalise first letter
    desc = desc[0].upper() + desc[1:] if desc else desc

    return (desc, amount)
