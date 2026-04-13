"""
work_order.py — State machine for the Work Order chat flow.

A Work Order is a verbal job — no estimate, no approval process.
Customer and tech agree on price on the spot. Job is created directly
in the jobs table and goes straight to the dispatch board.

This is different from the estimate flow:
  Estimate:   proposal → customer approves → job created
  Work Order: job created immediately → optional confirmation sent

State flow:
    IDLE
      → start()              creates session, asks "Who's the customer?"

    Main WO flow:
      ask_customer           → confirm_customer → ask_job_type → ask_price
                               → ask_when → ask_send_confirmation (if later)
                               → review → create_job chip

    New customer sub-flow (same as guided_estimate):
      add_new_customer → ask_customer_phone → ask_customer_address

    ask_when:
      "now"  → job status = 'in_progress', no confirmation needed → review
      "later" → job status = 'scheduled', ask if confirmation should be sent

    Terminal action:
      Returns a "create_work_order" action chip with all params.
      The PWA taps it → POST /pwa/api/workorder/new → job created in DB.
      If confirmation flagged, proposal is also created and sent.

Design rules (non-negotiable):
    - No Claude calls for pricing. Ever.
    - Tech provides every price. Pricebook history shown as reference only.
    - All state lives in estimate_sessions — same table, mode='work_order' in notes.
    - Returns same {reply, action} shape as pwa_chat.py.

Usage (from pwa_chat.py):
    from execution.work_order import handle_input, start, get_active_session, is_work_order_intent
"""

import os
import re
import sys
import json
from datetime import datetime, timezone

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
# Intent detection
# ---------------------------------------------------------------------------

_WO_TRIGGERS = re.compile(
    r'\b(new\s+work\s+order|create\s+work\s+order|work\s+order|new\s+w/?o|create\s+w/?o|add\s+job)\b',
    re.IGNORECASE,
)


def is_work_order_intent(message: str) -> bool:
    """Return True if the tech's message is trying to start a work order."""
    return bool(_WO_TRIGGERS.search(message.strip()))


# ---------------------------------------------------------------------------
# Shared regex helpers (mirrors guided_estimate — kept local to avoid coupling)
# ---------------------------------------------------------------------------

_CANCEL_RE     = re.compile(r'\b(cancel|stop|nevermind|never mind|abort|quit)\b', re.IGNORECASE)
_YES_BARE_RE   = re.compile(r'^\s*(yes|y|yep|yeah|yup|sure|ok|okay|correct|right)\s*$', re.IGNORECASE)
_YES_PREFIX_RE = re.compile(r'^\s*(yes|yep|yeah|yup)\b', re.IGNORECASE)
_NO_RE         = re.compile(r'^\s*(no|n|nope|nah|skip)\s*$', re.IGNORECASE)
_DONE_RE       = re.compile(r'^\s*(done|no|no more|that\'?s?\s*(it|all)|finish|finished)\s*$', re.IGNORECASE)
_NEW_CUSTOMER_RE = re.compile(
    r'^\s*(new|new customer|add new|add customer|add a new|add)\s*$', re.IGNORECASE
)

# "now" / "today" / "do it now" → in_progress
_NOW_RE = re.compile(
    r'^\s*(now|today|do it now|doing it now|right now|on site|in progress)\s*$',
    re.IGNORECASE,
)

# "later" / "schedule" / "send to office" → scheduled
_LATER_RE = re.compile(
    r'^\s*(later|schedule|scheduled|send to office|office|not today|another day|asap|as soon as possible)\s*$',
    re.IGNORECASE,
)

_INSTRUCTION_SIGNALS = re.compile(
    r'\b(add|put|call it|name it|price book|pricebook|wrong|incorrect|change|update|fix)\b',
    re.IGNORECASE,
)
_PRICE_ONLY_RE = re.compile(r'^\s*\$?[\d,]+(\.\d{1,2})?\s*$')


def _is_cancel(text: str) -> bool:
    return bool(_CANCEL_RE.search(text))


def _is_yes(text: str) -> bool:
    return bool(_YES_BARE_RE.match(text.strip()))


def _is_no(text: str) -> bool:
    return bool(_NO_RE.match(text.strip()))


def _is_done(text: str) -> bool:
    return bool(_DONE_RE.match(text.strip()))


def _is_new_customer(text: str) -> bool:
    return bool(_NEW_CUSTOMER_RE.match(text.strip()))


def _is_now(text: str) -> bool:
    return bool(_NOW_RE.match(text.strip()))


def _is_later(text: str) -> bool:
    return bool(_LATER_RE.match(text.strip()))


def _looks_like_price(text: str) -> bool:
    if _INSTRUCTION_SIGNALS.search(text):
        return False
    return bool(_PRICE_ONLY_RE.match(text.strip()))


def _normalize_phone(raw: str) -> str:
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if digits:
        return f"+{digits}"
    return ""


def _parse_dollar_amount(text: str) -> float | None:
    if not text:
        return None
    cleaned = text.strip().replace(",", "").replace("$", "")
    match = re.search(r'\b(\d+(?:\.\d{1,2})?)\b', cleaned)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------

def _reply(text: str, action: dict | None = None) -> dict:
    return {
        "success": True,
        "reply": text,
        "action": action,
        "model": "work_order_flow",
        "system_prompt_chars": 0,
        "error": None,
    }


def _error(text: str) -> dict:
    return {
        "success": False,
        "reply": text,
        "action": None,
        "model": "work_order_flow",
        "system_prompt_chars": 0,
        "error": text,
    }


# ---------------------------------------------------------------------------
# Session management — reuses estimate_sessions table.
# Work order sessions are identified by notes JSON containing mode='work_order'.
# ---------------------------------------------------------------------------

_WO_MODE = "work_order"


def get_active_session(client_id: str, employee_id: str, session_id: str) -> dict | None:
    """
    Return an active work order session for this employee + chat session, or None.
    Filters by mode='work_order' stored in the notes JSON field.
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
        ).limit(5).execute()

        rows = result.data or []
        for row in rows:
            # Identify work order sessions by the mode field in notes
            try:
                notes = json.loads(row.get(ES.NOTES) or "{}")
                if notes.get("mode") == _WO_MODE:
                    return row
            except Exception:
                pass
        return None
    except Exception as e:
        print(f"[{_ts()}] WARN work_order: get_active_session failed — {e}")
        return None


def _create_session(client_id: str, employee_id: str, session_id: str) -> dict | None:
    """Create a new work order session in the estimate_sessions table."""
    try:
        sb = get_supabase()
        result = sb.table(ES.TABLE).insert({
            ES.CLIENT_ID:    client_id,
            ES.EMPLOYEE_ID:  employee_id,
            ES.SESSION_ID:   session_id,
            ES.STATUS:       "gathering",
            ES.CURRENT_STEP: "ask_customer",
            ES.LINE_ITEMS:   [],
            ES.NOTES:        json.dumps({"mode": _WO_MODE}),
        }).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[{_ts()}] ERROR work_order: _create_session failed — {e}")
        return None


def _update_session(session_pk: str, updates: dict) -> bool:
    try:
        sb = get_supabase()
        updates[ES.UPDATED_AT] = datetime.now(timezone.utc).isoformat()
        sb.table(ES.TABLE).update(updates).eq(ES.ID, session_pk).execute()
        return True
    except Exception as e:
        print(f"[{_ts()}] ERROR work_order: _update_session failed — {e}")
        return False


def _cancel_session(session_pk: str) -> bool:
    return _update_session(session_pk, {ES.STATUS: "cancelled"})


# ---------------------------------------------------------------------------
# Notes scratchpad helpers
# ---------------------------------------------------------------------------

def _get_notes(session: dict) -> dict:
    try:
        raw = session.get(ES.NOTES) or "{}"
        return json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        return {}


def _set_notes(session_pk: str, data: dict) -> None:
    # Always preserve mode='work_order' in the notes
    data["mode"] = _WO_MODE
    _update_session(session_pk, {ES.NOTES: json.dumps(data)})


# ---------------------------------------------------------------------------
# DB lookups
# ---------------------------------------------------------------------------

def _find_customers(client_id: str, query: str) -> list[dict]:
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
        print(f"[{_ts()}] WARN work_order: _find_customers failed — {e}")
        return []


def _create_customer(client_id: str, name: str, phone: str,
                     address: str | None = None) -> str | None:
    """Create a new customer and return their UUID. Phone required (HARD RULE #1)."""
    try:
        sb = get_supabase()
        row = {
            C.CLIENT_ID:      client_id,
            C.CUSTOMER_NAME:  name.strip(),
            C.CUSTOMER_PHONE: phone,
            C.SMS_CONSENT:    False,
        }
        if address:
            row[C.CUSTOMER_ADDRESS] = address.strip()
        result = sb.table(C.TABLE).insert(row).execute()
        if result.data:
            return result.data[0][C.ID]
        return None
    except Exception as e:
        print(f"[{_ts()}] ERROR work_order: _create_customer failed — {e}")
        return None


def _get_pricing_reference(client_id: str, job_type: str,
                           customer_id: str | None = None) -> str | None:
    """Show last 3 averaged price for this job type. Reference only — never pre-filled."""
    try:
        sb = get_supabase()
        query = sb.table(JPH.TABLE).select(JPH.AMOUNT).eq(
            JPH.CLIENT_ID, client_id
        ).eq(JPH.JOB_TYPE, job_type)
        if customer_id:
            query = query.eq(JPH.CUSTOMER_ID, customer_id)
        result = query.order(JPH.COMPLETED_AT, desc=True).limit(5).execute()
        rows = result.data or []
        if len(rows) >= 2:
            amounts = [float(r[JPH.AMOUNT]) for r in rows]
            avg = round(sum(amounts) / len(amounts))
            return f"Last {len(amounts)} averaged ${avg}."
        return None
    except Exception as e:
        print(f"[{_ts()}] WARN work_order: _get_pricing_reference failed — {e}")
        return None


# Job type keywords — mirrors guided_estimate for consistency
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


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower().strip()).strip("_")
    slug = re.sub(r"_+", "_", slug)[:60]
    return slug if slug else "job"


def _classify_job_type(text: str) -> str:
    """Keyword match. Falls back to a slugified version of the input."""
    text_lower = text.lower()
    for job_type, keywords in _JOB_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return job_type

    # Check pricebook for this client's custom types
    # (client_id not available here — handled in the handler via _find_in_pricebook)
    return _slugify(text)


def _find_in_pricebook(client_id: str, text: str) -> dict | None:
    try:
        sb = get_supabase()
        result = sb.table("pricebook_items").select(
            "job_name, description, price_mid, unit_of_measure"
        ).eq("client_id", client_id).eq("is_active", True).ilike(
            "job_name", f"%{text.strip()}%"
        ).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[{_ts()}] WARN work_order: _find_in_pricebook failed — {e}")
        return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def start(client_id: str, employee_id: str, session_id: str) -> dict:
    """Create a new work order session and ask for the customer."""
    session = _create_session(client_id, employee_id, session_id)
    if not session:
        return _error("Couldn't start the work order. Try again.")
    return _reply("Work order — who's the customer?")


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

def handle_input(session: dict, user_message: str,
                 client_id: str, employee_id: str) -> dict:
    """Route the tech's message to the correct step handler."""
    session_pk   = session[ES.ID]
    current_step = session.get(ES.CURRENT_STEP, "ask_customer")

    if _is_cancel(user_message):
        _cancel_session(session_pk)
        return _reply("Work order cancelled.")

    # Customer steps
    if current_step == "ask_customer":
        return _handle_ask_customer(session, user_message, client_id)
    if current_step == "confirm_customer":
        return _handle_confirm_customer(session, user_message, client_id)
    if current_step == "disambiguate_customer":
        return _handle_disambiguate_customer(session, user_message, client_id)

    # New customer sub-flow
    if current_step == "add_new_customer":
        return _handle_add_new_customer(session, user_message, client_id)
    if current_step == "ask_customer_phone":
        return _handle_ask_customer_phone(session, user_message, client_id)
    if current_step == "ask_customer_address":
        return _handle_ask_customer_address(session, user_message, client_id)

    # Job details
    if current_step == "ask_job_type":
        return _handle_ask_job_type(session, user_message, client_id)
    if current_step == "ask_price":
        return _handle_ask_price(session, user_message, client_id)
    if current_step == "ask_when":
        return _handle_ask_when(session, user_message, client_id)
    if current_step == "ask_send_confirmation":
        return _handle_ask_send_confirmation(session, user_message, client_id)

    # Unknown step — restart
    _update_session(session_pk, {ES.CURRENT_STEP: "ask_customer"})
    return _reply("Let's start over. Who's the customer?")


# ---------------------------------------------------------------------------
# Step handlers
# ---------------------------------------------------------------------------

def _handle_ask_customer(session: dict, text: str, client_id: str) -> dict:
    """Look up customer by name. Intercept 'new' before the DB lookup."""
    session_pk = session[ES.ID]

    if _is_new_customer(text):
        notes = _get_notes(session)
        _set_notes(session_pk, notes)
        _update_session(session_pk, {ES.CURRENT_STEP: "add_new_customer"})
        return _reply("New customer — what's their name?")

    matches = _find_customers(client_id, text)

    if not matches:
        return _reply(
            f"I don't have a customer matching '{text}'. "
            "Try a different name, or type 'new' to add them."
        )

    if len(matches) == 1:
        cust = matches[0]
        name = cust.get(C.CUSTOMER_NAME, "")
        addr = cust.get(C.CUSTOMER_ADDRESS, "")
        addr_part = f" — {addr}" if addr else ""
        notes = _get_notes(session)
        notes["candidate"] = cust
        _set_notes(session_pk, notes)
        _update_session(session_pk, {
            ES.CURRENT_STEP: "confirm_customer",
            ES.STATUS: "confirming_customer",
        })
        return _reply(f"Found {name}{addr_part}. Correct?")

    lines = ["I found a few matches:"]
    for i, c in enumerate(matches[:5], 1):
        addr = c.get(C.CUSTOMER_ADDRESS, "")
        lines.append(f"  {i}) {c.get(C.CUSTOMER_NAME, '')} — {addr}")
    lines.append("Which one? (or 'new')")

    notes = _get_notes(session)
    notes["candidates"] = matches[:5]
    _set_notes(session_pk, notes)
    _update_session(session_pk, {
        ES.CURRENT_STEP: "disambiguate_customer",
        ES.STATUS: "gathering",
    })
    return _reply("\n".join(lines))


def _handle_confirm_customer(session: dict, text: str, client_id: str) -> dict:
    session_pk = session[ES.ID]

    if _is_no(text):
        notes = _get_notes(session)
        notes.pop("candidate", None)
        _set_notes(session_pk, notes)
        _update_session(session_pk, {ES.CURRENT_STEP: "ask_customer", ES.STATUS: "gathering"})
        return _reply("Who's the customer?")

    if _is_yes(text):
        notes = _get_notes(session)
        cust = notes.pop("candidate", {})
        _set_notes(session_pk, notes)
        _update_session(session_pk, {
            ES.CUSTOMER_ID:        cust.get(C.ID),
            ES.CUSTOMER_CONFIRMED: True,
            ES.CURRENT_STEP:       "ask_job_type",
            ES.STATUS:             "gathering",
        })
        return _reply("What type of job?")

    return _reply("Is that the right customer? (yes or no)")


def _handle_disambiguate_customer(session: dict, text: str, client_id: str) -> dict:
    session_pk = session[ES.ID]
    notes = _get_notes(session)
    candidates = notes.get("candidates", [])

    stripped = text.strip()
    if stripped.isdigit():
        idx = int(stripped) - 1
        if 0 <= idx < len(candidates):
            pick = candidates[idx]
            name = pick.get(C.CUSTOMER_NAME, "")
            addr = pick.get(C.CUSTOMER_ADDRESS, "")
            addr_part = f" — {addr}" if addr else ""
            notes.pop("candidates", None)
            notes["candidate"] = pick
            _set_notes(session_pk, notes)
            _update_session(session_pk, {
                ES.CURRENT_STEP: "confirm_customer",
                ES.STATUS: "confirming_customer",
            })
            return _reply(f"Found {name}{addr_part}. Correct?")

    if _is_new_customer(stripped):
        notes.pop("candidates", None)
        _set_notes(session_pk, notes)
        _update_session(session_pk, {ES.CURRENT_STEP: "add_new_customer"})
        return _reply("New customer — what's their name?")

    return _reply(f"Pick a number (1–{len(candidates)}) or type 'new'.")


# New customer sub-flow (identical logic to guided_estimate)

def _handle_add_new_customer(session: dict, text: str, client_id: str) -> dict:
    session_pk = session[ES.ID]
    name = text.strip()
    if not name or len(name) < 2:
        return _reply("What's the customer's name?")
    notes = _get_notes(session)
    notes["new_name"] = name
    _set_notes(session_pk, notes)
    _update_session(session_pk, {ES.CURRENT_STEP: "ask_customer_phone"})
    return _reply(f"{name} — what's their phone number?")


def _handle_ask_customer_phone(session: dict, text: str, client_id: str) -> dict:
    session_pk = session[ES.ID]
    phone = _normalize_phone(text.strip())
    if not phone:
        return _reply("I didn't catch a valid phone number. Try: 207-555-1234")
    notes = _get_notes(session)
    notes["new_phone"] = phone
    _set_notes(session_pk, notes)
    name = notes.get("new_name", "the customer")
    _update_session(session_pk, {ES.CURRENT_STEP: "ask_customer_address"})
    return _reply(f"Got it. What's {name}'s address? (or 'skip')")


def _handle_ask_customer_address(session: dict, text: str, client_id: str) -> dict:
    session_pk = session[ES.ID]
    notes = _get_notes(session)
    name  = notes.get("new_name", "")
    phone = notes.get("new_phone", "")

    if not name or not phone:
        _update_session(session_pk, {ES.CURRENT_STEP: "add_new_customer"})
        return _reply("Let's try again — what's the customer's name?")

    skip_words = {"skip", "no", "none", "-", "n/a"}
    address = None if text.strip().lower() in skip_words else text.strip()

    customer_id = _create_customer(client_id, name, phone, address)
    if not customer_id:
        return _reply("Something went wrong creating the customer. Try again or type 'cancel'.")

    print(f"[{_ts()}] INFO work_order: Created customer '{name}' for client {client_id[:8]}")

    # Clean up new_customer scratchpad keys
    notes.pop("new_name", None)
    notes.pop("new_phone", None)
    _set_notes(session_pk, notes)

    addr_str = f" at {address}" if address else ""
    _update_session(session_pk, {
        ES.CUSTOMER_ID:        customer_id,
        ES.CUSTOMER_CONFIRMED: True,
        ES.CURRENT_STEP:       "ask_job_type",
        ES.STATUS:             "gathering",
    })
    return _reply(f"Added {name}{addr_str}. What type of job?")


def _handle_ask_job_type(session: dict, text: str, client_id: str) -> dict:
    """Classify job type. Check pricebook first, then keyword match."""
    session_pk  = session[ES.ID]
    customer_id = session.get(ES.CUSTOMER_ID)

    # Check pricebook for custom types first
    pb_item = _find_in_pricebook(client_id, text)
    if pb_item:
        job_type  = _slugify(pb_item["job_name"])
        job_label = pb_item["job_name"]
        price_ref = _get_pricing_reference(client_id, job_type, customer_id)
        _update_session(session_pk, {
            ES.JOB_TYPE:           job_type,
            ES.JOB_TYPE_CONFIRMED: True,
            ES.CURRENT_STEP:       "ask_price",
            ES.STATUS:             "awaiting_price",
        })
        reply = f"What's the agreed price for {job_label}?"
        if pb_item.get("price_mid"):
            unit = pb_item.get("unit_of_measure", "per job")
            reply = f"Standard: ${pb_item['price_mid']:.0f} {unit}. " + reply
        if price_ref:
            reply = f"{price_ref} " + reply
        return _reply(reply)

    # Keyword classification
    job_type  = _classify_job_type(text)
    job_label = text.strip().title() if job_type == _slugify(text) else job_type.replace("_", " ").title()

    price_ref = _get_pricing_reference(client_id, job_type, customer_id)
    _update_session(session_pk, {
        ES.JOB_TYPE:           job_type,
        ES.JOB_TYPE_CONFIRMED: True,
        ES.CURRENT_STEP:       "ask_price",
        ES.STATUS:             "awaiting_price",
    })
    reply = f"What's the agreed price for the {job_label}?"
    if price_ref:
        reply = f"{price_ref} {reply}"
    return _reply(reply)


def _handle_ask_price(session: dict, text: str, client_id: str) -> dict:
    session_pk = session[ES.ID]

    if not _looks_like_price(text):
        return _reply("I need a dollar amount. What did you agree on? (e.g. 325)")

    amount = _parse_dollar_amount(text)
    if amount is None or amount <= 0:
        return _reply("Price needs to be greater than $0. What's the agreed price?")
    if amount > 100_000:
        return _reply(f"${amount:,.0f} looks high — double-check and re-enter.")

    _update_session(session_pk, {
        ES.PRIMARY_PRICE: amount,
        ES.CURRENT_STEP:  "ask_when",
        ES.STATUS:        "gathering",
    })
    return _reply(
        f"Got it — ${amount:,.0f}.\n"
        "Are you doing this job now or scheduling for later?\n"
        "  1) Now / today\n"
        "  2) Schedule for later"
    )


def _handle_ask_when(session: dict, text: str, client_id: str) -> dict:
    """
    'now'/'1'  → job status = 'in_progress' → skip to review chip
    'later'/'2' → job status = 'scheduled'  → ask about confirmation
    """
    session_pk = session[ES.ID]
    stripped   = text.strip()

    is_now_answer   = _is_now(stripped) or stripped == "1"
    is_later_answer = _is_later(stripped) or stripped == "2"

    if is_now_answer:
        notes = _get_notes(session)
        notes["job_status"]         = "in_progress"
        notes["send_confirmation"]  = False
        _set_notes(session_pk, notes)
        return _build_wo_chip(session, client_id)

    if is_later_answer:
        notes = _get_notes(session)
        notes["job_status"] = "scheduled"
        _set_notes(session_pk, notes)
        _update_session(session_pk, {ES.CURRENT_STEP: "ask_send_confirmation"})
        return _reply("Got it — we'll get it scheduled. Send the customer a confirmation with the details?")

    return _reply(
        "Are you doing this job now or scheduling it?\n"
        "  1) Now / today\n"
        "  2) Schedule for later"
    )


def _handle_ask_send_confirmation(session: dict, text: str, client_id: str) -> dict:
    """
    Tech decides whether to send a courtesy confirmation document.
    Yes → send_confirmation = True in the chip params.
    No  → skip, just create the job.
    """
    session_pk = session[ES.ID]
    notes = _get_notes(session)

    if _is_yes(text):
        notes["send_confirmation"] = True
    elif _is_no(text):
        notes["send_confirmation"] = False
    else:
        return _reply("Send a confirmation to the customer? (yes or no)")

    _set_notes(session_pk, notes)
    return _build_wo_chip(session, client_id)


def _build_wo_chip(session: dict, client_id: str) -> dict:
    """
    Assemble the final work order review and return the action chip.
    The chip's tap handler POSTs to /pwa/api/workorder/new.
    No DB writes happen here — the chip tap does the write.
    """
    session_pk = session[ES.ID]
    notes      = _get_notes(session)

    customer_id   = session.get(ES.CUSTOMER_ID)
    job_type      = session.get(ES.JOB_TYPE, "service")
    primary_price = float(session.get(ES.PRIMARY_PRICE) or 0)
    job_status    = notes.get("job_status", "scheduled")
    send_conf     = notes.get("send_confirmation", False)

    # Fetch customer details for the chip params
    customer_name    = ""
    customer_phone   = ""
    customer_address = ""
    if customer_id:
        try:
            sb = get_supabase()
            cr = sb.table(C.TABLE).select(
                f"{C.CUSTOMER_NAME}, {C.CUSTOMER_PHONE}, {C.CUSTOMER_ADDRESS}"
            ).eq(C.ID, customer_id).limit(1).execute()
            if cr.data:
                customer_name    = cr.data[0].get(C.CUSTOMER_NAME, "")
                customer_phone   = cr.data[0].get(C.CUSTOMER_PHONE, "")
                customer_address = cr.data[0].get(C.CUSTOMER_ADDRESS, "")
        except Exception:
            pass

    job_label  = job_type.replace("_", " ").title()
    status_str = "Starting now" if job_status == "in_progress" else "Scheduled"
    conf_str   = " · Confirmation will be sent" if send_conf else ""

    summary = (
        f"Customer:  {customer_name or 'Unknown'}\n"
        f"Job:       {job_label} — ${primary_price:,.0f}\n"
        f"Status:    {status_str}{conf_str}"
    )

    # Mark session done — the chip tap does the actual job write
    _update_session(session_pk, {ES.STATUS: "done"})

    action = {
        "type":  "create_work_order",
        "label": f"Create work order · ${int(primary_price)}",
        "params": {
            "customer_id":        customer_id,
            "customer_name":      customer_name,
            "customer_phone":     customer_phone,
            "customer_address":   customer_address,
            "job_type":           job_type,
            "description":        job_label,
            "amount":             primary_price,
            "job_status":         job_status,         # 'in_progress' or 'scheduled'
            "send_confirmation":  send_conf,           # bool
        },
        "endpoint": "/pwa/api/workorder/new",
        "method":   "POST",
    }

    return _reply(
        f"Here's the work order:\n\n{summary}\n\nTap to create.",
        action=action,
    )
