"""
guided_estimate.py — State machine for the guided estimate flow.

This module handles the multi-turn conversation that walks a tech through
creating an estimate without ever letting the AI invent a price.

State flow:
    IDLE
      → start()              creates estimate_session, asks "Who's the customer?"
    gathering
      → handle_input()       routes to the right handler based on current_step

    Main estimate flow:
      ask_customer           → confirm_customer → ask_job_type → ask_price
                               → ask_line_items → ask_notes → review chip

      New customer sub-flow (triggered by typing 'new' at ask_customer):
      add_new_customer       → tech types customer name
      ask_customer_phone     → tech types phone number (E.164 normalized)
      ask_customer_address   → tech types address (or 'skip')
                               → customer created in DB → advance to ask_job_type

    Add-job-type sub-flow (triggered when job type is unrecognised):
      offer_add_job_type     → tech says yes/no
      add_jt_name            → tech confirms or renames the job
      add_jt_description     → tech provides a 1-line scope description
      add_jt_unit            → tech sets the pricing unit (per job / per foot / etc.)
      add_jt_price           → tech sets the standard price (never AI-generated)
      add_jt_confirm         → tech confirms → saved to pricebook → resumes at ask_price

    done / cancelled         terminal states

Design rules (non-negotiable):
    - No Claude calls for pricing. Ever.
    - One Haiku call: job type classification from keywords.
    - All state lives in estimate_sessions table — not in memory.
    - Tech provides every price. History is reference only, never pre-filled.
    - New job types are saved to pricebook_items so they exist next time.
    - Returns same {reply, action} shape as pwa_chat.py.

Usage (from pwa_chat.py):
    from execution.guided_estimate import handle_input, start, get_active_session

    session = get_active_session(client_id, employee_id, chat_session_id)
    if session:
        return handle_input(session, user_message, client_id, employee_id)
    elif is_estimate_intent(user_message):
        return start(client_id, employee_id, chat_session_id)
"""

import os
import re
import sys
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase
from execution.call_claude import call_claude          # module-level import for testability
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

_ESTIMATE_TRIGGERS = re.compile(
    r'\b(create|new|start|make|write|draft)\s+(?:\w+\s+)?(estimate|quote|bid|proposal)\b'
    r'|^estimate\b'
    r'|\bestimate\s+for\b',
    re.IGNORECASE,
)


def is_estimate_intent(message: str) -> bool:
    return bool(_ESTIMATE_TRIGGERS.search(message.strip()))


# ---------------------------------------------------------------------------
# Global command detection
# ---------------------------------------------------------------------------

_CANCEL_RE = re.compile(r'\b(cancel|stop|nevermind|never mind|abort|quit)\b', re.IGNORECASE)
_DONE_RE   = re.compile(r'^\s*(done|no|no more|that\'?s?\s*(it|all)|finish|finished)\s*$', re.IGNORECASE)

_YES_BARE_RE   = re.compile(r'^\s*(yes|y|yep|yeah|yup|sure|ok|okay|correct|right)\s*$', re.IGNORECASE)
_YES_PREFIX_RE = re.compile(r'^\s*(yes|yep|yeah|yup)\b', re.IGNORECASE)
_NO_RE         = re.compile(r'^\s*(no|n|nope|nah|skip)\s*$', re.IGNORECASE)

# "new" command — triggers add-new-customer flow at the ask_customer step
_NEW_CUSTOMER_RE = re.compile(
    r'^\s*(new|new customer|add new|add customer|add a new|add)\s*$',
    re.IGNORECASE,
)


def _is_cancel(text: str) -> bool:
    return bool(_CANCEL_RE.search(text))


def _is_done(text: str) -> bool:
    return bool(_DONE_RE.match(text))


def _is_yes(text: str) -> bool:
    return bool(_YES_BARE_RE.match(text.strip()))


def _is_yes_prefix(text: str) -> bool:
    return bool(_YES_PREFIX_RE.match(text.strip()))


def _yes_remainder(text: str) -> str:
    return _YES_PREFIX_RE.sub("", text.strip()).strip().lstrip(",-").strip()


def _is_no(text: str) -> bool:
    return bool(_NO_RE.match(text.strip()))


def _is_new_customer(text: str) -> bool:
    return bool(_NEW_CUSTOMER_RE.match(text.strip()))


# ---------------------------------------------------------------------------
# Instruction-vs-price detection
# ---------------------------------------------------------------------------

_INSTRUCTION_SIGNALS = re.compile(
    r'\b(add|put|call it|name it|price book|pricebook|wrong|incorrect|change|update|fix)\b',
    re.IGNORECASE,
)
_PRICE_ONLY_RE = re.compile(r'^\s*\$?[\d,]+(\.\d{1,2})?\s*$')


def _looks_like_price(text: str) -> bool:
    if _INSTRUCTION_SIGNALS.search(text):
        return False
    return bool(_PRICE_ONLY_RE.match(text.strip()))


# ---------------------------------------------------------------------------
# Phone normalization
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_active_session(client_id: str, employee_id: str, session_id: str) -> dict | None:
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
    try:
        sb = get_supabase()
        result = sb.table(ES.TABLE).insert({
            ES.CLIENT_ID:    client_id,
            ES.EMPLOYEE_ID:  employee_id,
            ES.SESSION_ID:   session_id,
            ES.STATUS:       "gathering",
            ES.CURRENT_STEP: "ask_customer",
            ES.LINE_ITEMS:   [],
        }).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[{_ts()}] ERROR guided_estimate: _create_session failed — {e}")
        return None


def _update_session(session_id_pk: str, updates: dict) -> bool:
    try:
        sb = get_supabase()
        updates[ES.UPDATED_AT] = datetime.now(timezone.utc).isoformat()
        sb.table(ES.TABLE).update(updates).eq(ES.ID, session_id_pk).execute()
        return True
    except Exception as e:
        print(f"[{_ts()}] ERROR guided_estimate: _update_session failed — {e}")
        return False


def _cancel_session(session_id_pk: str) -> bool:
    return _update_session(session_id_pk, {ES.STATUS: "cancelled"})


# ---------------------------------------------------------------------------
# Pricing history reference
# ---------------------------------------------------------------------------

def get_pricing_reference(client_id: str, job_type: str,
                          customer_id: str | None = None) -> str | None:
    try:
        sb = get_supabase()

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
# Customer lookup
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
        print(f"[{_ts()}] WARN guided_estimate: _find_customers failed — {e}")
        return []


def _create_customer(client_id: str, name: str, phone: str,
                     address: str | None = None) -> str | None:
    """
    Create a new customer record and return their UUID.
    Phone is required (HARD RULE #1).
    """
    try:
        sb = get_supabase()
        row = {
            C.CLIENT_ID:       client_id,
            C.CUSTOMER_NAME:   name.strip(),
            C.CUSTOMER_PHONE:  phone,
            C.SMS_CONSENT:     False,
        }
        if address:
            row[C.CUSTOMER_ADDRESS] = address.strip()
        result = sb.table(C.TABLE).insert(row).execute()
        if result.data:
            return result.data[0][C.ID]
        return None
    except Exception as e:
        print(f"[{_ts()}] ERROR guided_estimate: _create_customer failed — {e}")
        return None


# ---------------------------------------------------------------------------
# Job type classification — keyword-first, Haiku fallback
# ---------------------------------------------------------------------------

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


def _slugify_job_type(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower().strip()).strip("_")
    slug = re.sub(r"_+", "_", slug)[:60]
    return f"custom_{slug}" if slug else "custom_job"


def _classify_job_type(text: str, vertical_key: str = "sewer_drain") -> str | None:
    text_lower = text.lower()

    for job_type, keywords in _JOB_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return job_type

    try:
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
        print(f"[{_ts()}] WARN guided_estimate: Haiku classification failed — {e}")

    return None


def _find_in_pricebook(client_id: str, text: str) -> dict | None:
    try:
        sb = get_supabase()
        result = sb.table("pricebook_items").select(
            "job_name, description, price_mid, unit_of_measure"
        ).eq("client_id", client_id).eq("is_active", True).ilike(
            "job_name", f"%{text.strip()}%"
        ).limit(1).execute()
        if result.data:
            return result.data[0]
    except Exception as e:
        print(f"[{_ts()}] WARN guided_estimate: pricebook fuzzy lookup failed — {e}")
    return None


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------

def _reply(text: str, action: dict | None = None) -> dict:
    return {
        "success": True,
        "reply": text,
        "action": action,
        "model": "guided_flow",
        "system_prompt_chars": 0,
        "error": None,
    }


def _error(text: str) -> dict:
    return {"success": False, "reply": text, "action": None,
            "model": "guided_flow", "system_prompt_chars": 0, "error": text}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def start(client_id: str, employee_id: str, session_id: str) -> dict:
    session = _create_session(client_id, employee_id, session_id)
    if not session:
        return _error("Couldn't start the estimate flow. Try again.")
    return _reply("Who's the customer?")


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

def handle_input(session: dict, user_message: str,
                 client_id: str, employee_id: str) -> dict:
    session_pk   = session[ES.ID]
    current_step = session.get(ES.CURRENT_STEP, "ask_customer")

    if _is_cancel(user_message):
        _cancel_session(session_pk)
        return _reply("Estimate cancelled. Start a new one anytime.")

    # Main estimate flow
    if current_step == "ask_customer":
        return _handle_customer_input(session, user_message, client_id)
    if current_step == "confirm_customer":
        return _handle_customer_confirm(session, user_message, client_id)
    if current_step == "disambiguate_customer":
        return _handle_customer_disambiguate(session, user_message, client_id)

    # New customer sub-flow
    if current_step == "add_new_customer":
        return _handle_add_new_customer(session, user_message, client_id)
    if current_step == "ask_customer_phone":
        return _handle_ask_customer_phone(session, user_message, client_id)
    if current_step == "ask_customer_address":
        return _handle_ask_customer_address(session, user_message, client_id)

    if current_step == "ask_job_type":
        return _handle_job_type_input(session, user_message, client_id)
    if current_step == "ask_price":
        return _handle_price_input(session, user_message, client_id)
    if current_step == "ask_line_items":
        return _handle_line_item_input(session, user_message, client_id, employee_id)
    if current_step == "ask_notes":
        return _handle_notes_input(session, user_message, client_id, employee_id)

    # Add-job-type sub-flow
    if current_step == "offer_add_job_type":
        return _handle_offer_add_job_type(session, user_message, client_id)
    if current_step == "add_jt_name":
        return _handle_add_jt_name(session, user_message, client_id)
    if current_step == "add_jt_description":
        return _handle_add_jt_description(session, user_message, client_id)
    if current_step == "add_jt_unit":
        return _handle_add_jt_unit(session, user_message, client_id)
    if current_step == "add_jt_price":
        return _handle_add_jt_price(session, user_message, client_id)
    if current_step == "add_jt_confirm":
        return _handle_add_jt_confirm(session, user_message, client_id)

    _update_session(session_pk, {ES.CURRENT_STEP: "ask_customer"})
    return _reply("Let's start over. Who's the customer?")


# ---------------------------------------------------------------------------
# Main estimate state handlers
# ---------------------------------------------------------------------------

def _handle_customer_input(session: dict, text: str, client_id: str) -> dict:
    """
    Look up a customer by name. Intercepts 'new' before the DB lookup
    so the tech can add a new customer mid-flow without dead-ending.
    """
    session_pk = session[ES.ID]

    # Intercept 'new' / 'new customer' / 'add' before any DB lookup.
    # Without this guard, "new" gets passed to _find_customers() which
    # searches for a customer literally named "New" and finds nothing.
    if _is_new_customer(text):
        _update_session(session_pk, {
            ES.CURRENT_STEP: "add_new_customer",
            ES.NOTES: json.dumps({}),
        })
        return _reply("New customer — what's their name?")

    matches = _find_customers(client_id, text)

    if not matches:
        _update_session(session_pk, {ES.CURRENT_STEP: "ask_customer"})
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
            ES.NOTES: json.dumps({"candidate": cust}),
        })
        return _reply(f"Found {name}{addr_part}. Correct?")

    lines = ["I found a few matches:"]
    for i, c in enumerate(matches[:5], 1):
        addr = c.get(C.CUSTOMER_ADDRESS, "")
        lines.append(f"  {i}) {c.get(C.CUSTOMER_NAME, '')} — {addr}")
    lines.append("Which one? (or 'new' to add a new customer)")

    _update_session(session_pk, {
        ES.CURRENT_STEP: "disambiguate_customer",
        ES.STATUS: "gathering",
        ES.NOTES: json.dumps({"candidates": matches[:5]}),
    })
    return _reply("\n".join(lines))


def _handle_customer_confirm(session: dict, text: str, client_id: str) -> dict:
    session_pk = session[ES.ID]
    if _is_no(text):
        _update_session(session_pk, {
            ES.CURRENT_STEP: "ask_customer",
            ES.STATUS: "gathering",
            ES.NOTES: None,
        })
        return _reply("Who's the customer?")

    if _is_yes(text):
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
            ES.NOTES: None,
        })
        return _reply("What type of job?")

    return _reply("Is that the right customer? (yes or no)")


def _handle_customer_disambiguate(session: dict, text: str, client_id: str) -> dict:
    session_pk = session[ES.ID]
    try:
        stored = json.loads(session.get(ES.NOTES) or "{}")
        candidates = stored.get("candidates", [])
    except Exception:
        candidates = []

    stripped = text.strip()
    if stripped.isdigit():
        idx = int(stripped) - 1
        if 0 <= idx < len(candidates):
            pick = candidates[idx]
            name = pick.get(C.CUSTOMER_NAME, "")
            addr = pick.get(C.CUSTOMER_ADDRESS, "")
            addr_part = f" — {addr}" if addr else ""
            _update_session(session_pk, {
                ES.CURRENT_STEP: "confirm_customer",
                ES.STATUS: "confirming_customer",
                ES.NOTES: json.dumps({"candidate": pick}),
            })
            return _reply(f"Found {name}{addr_part}. Correct?")

    if _is_new_customer(stripped):
        _update_session(session_pk, {
            ES.CURRENT_STEP: "add_new_customer",
            ES.NOTES: json.dumps({}),
        })
        return _reply("New customer — what's their name?")

    return _reply(f"Pick a number (1–{len(candidates)}) or type 'new'.")


# ---------------------------------------------------------------------------
# New customer sub-flow
#
# Triggered when the tech types 'new' at the ask_customer step.
# Collects name → phone → address (optional) → creates customer in DB
# → advances to ask_job_type with the new customer_id set.
#
# State stored in ES.NOTES as JSON:
#   { "new_name": "Michael Jackson", "new_phone": "+12075551234" }
# ---------------------------------------------------------------------------

def _handle_add_new_customer(session: dict, text: str, client_id: str) -> dict:
    """Tech typed the new customer's name."""
    session_pk = session[ES.ID]
    name = text.strip()

    if not name or len(name) < 2:
        return _reply("What's the customer's name?")

    _update_session(session_pk, {
        ES.CURRENT_STEP: "ask_customer_phone",
        ES.NOTES: json.dumps({"new_name": name}),
    })
    return _reply(f"{name} — what's their phone number?")


def _handle_ask_customer_phone(session: dict, text: str, client_id: str) -> dict:
    """Tech typed the new customer's phone number."""
    session_pk = session[ES.ID]

    try:
        stored = json.loads(session.get(ES.NOTES) or "{}")
    except Exception:
        stored = {}

    phone = _normalize_phone(text.strip())
    if not phone:
        return _reply(
            "I didn't catch a valid phone number. "
            "Enter it like: 207-555-1234 or 2075551234"
        )

    stored["new_phone"] = phone
    _update_session(session_pk, {
        ES.CURRENT_STEP: "ask_customer_address",
        ES.NOTES: json.dumps(stored),
    })
    name = stored.get("new_name", "the customer")
    return _reply(f"Got it. What's {name}'s address? (or 'skip')")


def _handle_ask_customer_address(session: dict, text: str,
                                  client_id: str) -> dict:
    """Tech typed the address (or 'skip'). Create the customer and advance."""
    session_pk = session[ES.ID]

    try:
        stored = json.loads(session.get(ES.NOTES) or "{}")
    except Exception:
        stored = {}

    name  = stored.get("new_name", "")
    phone = stored.get("new_phone", "")

    skip_words = {"skip", "no", "none", "-", "n/a"}
    address = None if text.strip().lower() in skip_words else text.strip()

    if not name or not phone:
        # Something went wrong — restart the sub-flow
        _update_session(session_pk, {
            ES.CURRENT_STEP: "add_new_customer",
            ES.NOTES: json.dumps({}),
        })
        return _reply("Let's try again — what's the customer's name?")

    # Create the customer
    customer_id = _create_customer(client_id, name, phone, address)
    if not customer_id:
        return _reply(
            "Something went wrong creating the customer. "
            "Try again or type 'cancel'."
        )

    print(f"[{_ts()}] INFO guided_estimate: Created customer '{name}' {phone} for client {client_id[:8]}")

    # Advance to job type with new customer set
    _update_session(session_pk, {
        ES.CUSTOMER_ID:        customer_id,
        ES.CUSTOMER_CONFIRMED: True,
        ES.CURRENT_STEP:       "ask_job_type",
        ES.STATUS:             "gathering",
        ES.NOTES:              None,
    })

    addr_str = f" at {address}" if address else ""
    return _reply(f"Added {name}{addr_str}. What type of job?")


# ---------------------------------------------------------------------------
# Job type handling
# ---------------------------------------------------------------------------

def _handle_job_type_input(session: dict, text: str, client_id: str) -> dict:
    """
    Resolve the tech's job type input:
      1. Keyword match against seeded types
      2. Fuzzy match against this client's pricebook (catches custom types)
      3. Haiku classification
      4. Unrecognised → offer to add it to the pricebook
    """
    session_pk  = session[ES.ID]
    customer_id = session.get(ES.CUSTOMER_ID)

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
        pb_item = _find_in_pricebook(client_id, text)
        if pb_item:
            job_type = _slugify_job_type(pb_item["job_name"])
            job_label = pb_item["job_name"]
            price_ref = get_pricing_reference(client_id, job_type, customer_id)
            _update_session(session_pk, {
                ES.JOB_TYPE: job_type,
                ES.JOB_TYPE_CONFIRMED: True,
                ES.CURRENT_STEP: "ask_price",
                ES.STATUS: "awaiting_price",
            })
            reply = f"{price_ref}\nWhat's your price for the {job_label}?" if price_ref else \
                    f"What's your price for the {job_label}?"
            if pb_item.get("price_mid"):
                unit = pb_item.get("unit_of_measure", "per job")
                reply = f"Standard: ${pb_item['price_mid']:.0f} {unit}.\n{reply}"
            return _reply(reply)

    if not job_type or job_type == "other":
        raw_text = text.strip()
        if not raw_text:
            return _reply("What type of job is this?")

        _update_session(session_pk, {
            ES.CURRENT_STEP: "offer_add_job_type",
            ES.NOTES: json.dumps({"raw_job_type": raw_text}),
        })
        return _reply(
            f"I don't have '{raw_text}' in your job types. "
            f"Would you like to add it to your pricebook?"
        )

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
    session_pk = session[ES.ID]

    if not _looks_like_price(text):
        return _reply(
            "I need a dollar amount for this job. "
            "What's the price? (e.g. 325, $1200)"
        )

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
    session_pk = session[ES.ID]

    if _is_done(text):
        _update_session(session_pk, {ES.CURRENT_STEP: "ask_notes"})
        return _reply("Any notes to add? Or say 'done'.")

    parsed = _parse_line_item(text)
    if not parsed:
        return _reply(
            "I didn't catch a price for that. "
            "Try: 'disposal fee $45' or say 'done' if you're finished."
        )

    desc, amount = parsed
    try:
        existing = session.get(ES.LINE_ITEMS) or []
        if isinstance(existing, str):
            existing = json.loads(existing)
        existing.append({"description": desc, "amount": amount})
        _update_session(session_pk, {ES.LINE_ITEMS: existing})
    except Exception as e:
        print(f"[{_ts()}] WARN guided_estimate: line item append failed — {e}")

    return _reply(f"Added '{desc}' — ${amount:,.0f}. Anything else? Or say 'done'.")


def _handle_notes_input(session: dict, text: str,
                         client_id: str, employee_id: str) -> dict:
    session_pk = session[ES.ID]
    if not _is_done(text):
        _update_session(session_pk, {ES.NOTES: text.strip()})
    return _build_review_chip(session_pk, client_id, employee_id)


def _build_review_chip(session_pk: str, client_id: str, employee_id: str) -> dict:
    try:
        sb = get_supabase()
        result = sb.table(ES.TABLE).select("*").eq(ES.ID, session_pk).limit(1).execute()
        rows = result.data or []
        session = rows[0] if rows else None
    except Exception as e:
        return _error(f"Couldn't load estimate data — {e}")

    if not session:
        return _error("Estimate session not found.")

    customer_id   = session.get(ES.CUSTOMER_ID)
    job_type      = session.get(ES.JOB_TYPE, "service")
    primary_price = float(session.get(ES.PRIMARY_PRICE) or 0)
    line_items    = session.get(ES.LINE_ITEMS) or []
    if isinstance(line_items, str):
        line_items = json.loads(line_items)
    notes = session.get(ES.NOTES, "")

    customer_name = ""
    customer_phone = ""
    customer_address = ""
    if customer_id:
        try:
            cr = sb.table(C.TABLE).select(
                f"{C.CUSTOMER_NAME}, {C.CUSTOMER_PHONE}, {C.CUSTOMER_ADDRESS}"
            ).eq(C.ID, customer_id).limit(1).execute()
            if cr.data:
                customer_name    = cr.data[0].get(C.CUSTOMER_NAME, "")
                customer_phone   = cr.data[0].get(C.CUSTOMER_PHONE, "")
                customer_address = cr.data[0].get(C.CUSTOMER_ADDRESS, "")
        except Exception:
            pass

    raw_job_type = job_type.replace("custom_", "", 1) if job_type.startswith("custom_") else job_type
    job_label = raw_job_type.replace("_", " ").title()

    line_desc   = "; ".join(f"{li['description']} ${li['amount']:.0f}" for li in line_items)
    description = job_label + (f" + {line_desc}" if line_desc else "")

    line_total = sum(float(li.get("amount", 0)) for li in line_items)
    total      = primary_price + line_total

    lines = [
        f"Customer:  {customer_name or 'Unknown'}",
        f"Job:       {job_label} — ${primary_price:,.0f}",
    ]
    for li in line_items:
        lines.append(f"           + {li['description']} — ${float(li['amount']):,.0f}")
    lines.append(f"Total:     ${total:,.0f}")
    summary = "\n".join(lines)

    _update_session(session_pk, {ES.STATUS: "done"})

    action = {
        "type":  "create_proposal",
        "label": f"Send estimate · ${int(total)}",
        "params": {
            "description":      description + (f"\n\nNotes: {notes}" if notes else ""),
            "customer_name":    customer_name,
            "customer_phone":   customer_phone,
            "customer_address": customer_address,
            "amount":           total,
            "line_items":       json.dumps([
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
# Add-job-type sub-flow
# ---------------------------------------------------------------------------

def _get_add_jt_state(session: dict) -> dict:
    try:
        raw = session.get(ES.NOTES) or "{}"
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {}


def _set_add_jt_state(session_pk: str, data: dict) -> None:
    _update_session(session_pk, {ES.NOTES: json.dumps(data)})


def _handle_offer_add_job_type(session: dict, text: str, client_id: str) -> dict:
    session_pk = session[ES.ID]
    state = _get_add_jt_state(session)
    raw = state.get("raw_job_type", text.strip())

    if _is_no(text):
        job_type  = _slugify_job_type(raw)
        job_label = raw.title()
        _update_session(session_pk, {
            ES.JOB_TYPE: job_type,
            ES.JOB_TYPE_CONFIRMED: True,
            ES.CURRENT_STEP: "ask_price",
            ES.STATUS: "awaiting_price",
            ES.NOTES: None,
        })
        return _reply(f"Got it. What's your price for the {job_label}?")

    if _is_yes_prefix(text):
        remainder = _yes_remainder(text)

        name_match = re.search(
            r'\b(?:add it as|call it|name it|as a|as)\s+(.+)$',
            remainder,
            re.IGNORECASE,
        )
        if name_match:
            inline_name = name_match.group(1).strip().strip('"\'')
        elif remainder:
            inline_name = remainder.strip().strip('"\'')
        else:
            inline_name = ""

        if inline_name:
            state["raw_job_type"] = raw
            state["jt_name"] = inline_name
            _set_add_jt_state(session_pk, state)
            _update_session(session_pk, {ES.CURRENT_STEP: "add_jt_description"})
            return _reply(
                f"'{inline_name}' — write a short description for the estimate.\n"
                f"Example: 'Install or replace culvert at property entrance'"
            )
        else:
            state["jt_name"] = raw
            _set_add_jt_state(session_pk, state)
            _update_session(session_pk, {ES.CURRENT_STEP: "add_jt_name"})
            return _reply(
                f"What should we call this job?\n"
                f"(I'll use '{raw.title()}' if you just say yes)"
            )

    return _reply(f"Add '{raw}' to your job types? (yes or no)")


def _handle_add_jt_name(session: dict, text: str, client_id: str) -> dict:
    session_pk = session[ES.ID]
    state = _get_add_jt_state(session)
    raw_default = state.get("jt_name") or state.get("raw_job_type", "")

    if _is_yes(text):
        name = raw_default.strip()
    else:
        name = text.strip()

    if not name:
        return _reply("What should we call this job type?")

    state["jt_name"] = name
    _set_add_jt_state(session_pk, state)
    _update_session(session_pk, {ES.CURRENT_STEP: "add_jt_description"})

    return _reply(
        f"'{name}' — write a short description for the estimate.\n"
        f"Example: 'Install or replace culvert at property entrance'"
    )


def _handle_add_jt_description(session: dict, text: str, client_id: str) -> dict:
    session_pk = session[ES.ID]
    state = _get_add_jt_state(session)

    desc = text.strip()
    if not desc:
        return _reply("What's a short description for this job?")

    state["jt_description"] = desc
    _set_add_jt_state(session_pk, state)
    _update_session(session_pk, {ES.CURRENT_STEP: "add_jt_unit"})

    return _reply(
        "How do you charge for this job?\n"
        "  1) Per job (flat rate)\n"
        "  2) Per foot\n"
        "  3) Per hour\n"
        "  4) Per unit\n"
        "Type the number or write it out."
    )


_UNIT_MAP = {
    "1": "per job", "per job": "per job", "flat": "per job", "job": "per job",
    "2": "per foot", "per foot": "per foot", "foot": "per foot", "ft": "per foot",
    "3": "per hour", "per hour": "per hour", "hour": "per hour", "hr": "per hour",
    "4": "per unit", "per unit": "per unit", "unit": "per unit",
}


def _handle_add_jt_unit(session: dict, text: str, client_id: str) -> dict:
    session_pk = session[ES.ID]
    state = _get_add_jt_state(session)

    raw = text.strip().lower()
    unit = _UNIT_MAP.get(raw) or f"per {raw}" if raw else "per job"

    state["jt_unit"] = unit
    _set_add_jt_state(session_pk, state)
    _update_session(session_pk, {ES.CURRENT_STEP: "add_jt_price"})

    jt_name = state.get("jt_name", "this job")
    return _reply(f"What's your standard price for {jt_name} ({unit})?")


def _handle_add_jt_price(session: dict, text: str, client_id: str) -> dict:
    session_pk = session[ES.ID]
    state = _get_add_jt_state(session)

    amount = _parse_dollar_amount(text)
    if amount is None or amount <= 0:
        return _reply("I didn't catch a price. Enter a dollar amount, like 60.")

    state["jt_price"] = amount
    _set_add_jt_state(session_pk, state)
    _update_session(session_pk, {ES.CURRENT_STEP: "add_jt_confirm"})

    name = state.get("jt_name", "")
    desc = state.get("jt_description", "")
    unit = state.get("jt_unit", "per job")

    return _reply(
        f"Here's what I'll add to your pricebook:\n\n"
        f"  Job type:    {name}\n"
        f"  Description: {desc}\n"
        f"  Price:       ${amount:,.0f} {unit}\n\n"
        f"Add it?"
    )


def _handle_add_jt_confirm(session: dict, text: str, client_id: str) -> dict:
    session_pk = session[ES.ID]
    state = _get_add_jt_state(session)

    if _is_no(text):
        _update_session(session_pk, {ES.CURRENT_STEP: "add_jt_name"})
        return _reply("No problem — what's the job name?")

    if not _is_yes(text):
        return _reply("Add this job type? (yes or no)")

    name  = state.get("jt_name", "")
    desc  = state.get("jt_description", "")
    unit  = state.get("jt_unit", "per job")
    price = float(state.get("jt_price") or 0)

    if not name or not price:
        _update_session(session_pk, {ES.CURRENT_STEP: "add_jt_name"})
        return _reply("Something went wrong — let's try again. What's the job name?")

    try:
        from execution.db_pricebook import add_job_type
        add_job_type(
            client_id=client_id,
            job_name=name,
            description=desc,
            price_mid=price,
            unit_of_measure=unit,
        )
        print(f"[{_ts()}] INFO guided_estimate: Added job type '{name}' to pricebook for client {client_id[:8]}")
    except Exception as e:
        print(f"[{_ts()}] WARN guided_estimate: pricebook save failed — {e}")

    job_type = _slugify_job_type(name)

    _update_session(session_pk, {
        ES.JOB_TYPE: job_type,
        ES.JOB_TYPE_CONFIRMED: True,
        ES.CURRENT_STEP: "ask_price",
        ES.STATUS: "awaiting_price",
        ES.NOTES: None,
    })

    return _reply(
        f"Added. '{name}' is now in your pricebook at ${price:,.0f} {unit}.\n\n"
        f"How much for this job?"
    )


# ---------------------------------------------------------------------------
# Parsing utilities
# ---------------------------------------------------------------------------

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


def _parse_line_item(text: str) -> tuple[str, float] | None:
    if not text or not text.strip():
        return None
    amount_match = re.search(r'\$?(\d+(?:\.\d{1,2})?)', text)
    if not amount_match:
        return None
    try:
        amount = float(amount_match.group(1))
    except ValueError:
        return None

    desc = text[:amount_match.start()].strip().rstrip("- ").strip()
    if not desc:
        desc = text[amount_match.end():].strip()
    if not desc:
        return None

    desc = desc[0].upper() + desc[1:] if desc else desc
    return (desc, amount)
