"""
job_log.py — Daily job log state machine for multi-day work orders.

Handles the foreman's end-of-day (or start-of-day) logging conversation:
  - Who was on the job today (crew presence)
  - What equipment was on site
  - What materials were received or consumed

This is NOT timecard/payroll. It is job-costing data — the raw material
for the bid-vs-actual report and partial invoicing on large jobs.

State flow:
    [intent detected in pwa_chat.py]
      → missed_log_check   if prior unclosed session exists from an earlier date
      → select_job         numbered list of open work_orders, sorted by updated_at DESC
      → confirm_crew       multi-select from active employees; "me" = logged-in foreman
      → confirm_equipment  yesterday's equipment if any, else ask fresh; loop
      → log_materials      ask yes/no; if yes: name → qty → unit → supplier? → loop
      → day_close          confirm summary → write all three log tables → done

Key design decisions:
    - log_date is a DATE, not a timestamp. Enables backdating missed close-outs.
    - Equipment is presence-only for MVP. Hours field added in phase 2.
    - Materials: name + qty + unit required. Supplier optional free text.
    - billed=false on all new rows. Invoice assembly flips to true.
    - One foreman can log crew across multiple jobs on the same day
      (Joe at Paul Jones' site then Bill Murray's site — two job_crew_log rows).
    - All state lives in job_log_sessions — same resume-on-reconnect pattern
      as guided_estimate.py.

Usage (from pwa_chat.py):
    from execution.job_log import (
        get_active_session,
        handle_input,
        start,
        is_job_log_intent,
        check_missed_log,
    )
"""

import re
import sys
import json
import os
from datetime import datetime, timezone, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase
from execution.schema import (
    JobLogSessions  as JLS,
    JobCrewLog      as JCL,
    JobEquipmentLog as JEL,
    JobMaterialLog  as JML,
    Employees       as EMP,
    Jobs,
)


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

_JOB_LOG_TRIGGERS = re.compile(
    r'\b(start(?:ing)?\s+(?:the\s+)?job|on[\s\-]?site|at\s+the\s+job|job\s+log'
    r'|log\s+material|material\s+deliver|done\s+for\s+(?:the\s+)?day'
    r'|wrapping\s+up|clock(?:ing)?\s+(?:on|off)\s+(?:the\s+)?job'
    r'|end\s+(?:of\s+)?day\s+log|day\s+log)\b',
    re.IGNORECASE,
)


def is_job_log_intent(message: str) -> bool:
    """Return True if the tech's message is trying to start a daily job log."""
    return bool(_JOB_LOG_TRIGGERS.search(message.strip()))


# ---------------------------------------------------------------------------
# Missed log check — call this before starting a new session
# ---------------------------------------------------------------------------

def check_missed_log(client_id: str, employee_id: str) -> dict | None:
    """
    Check if this employee has an unclosed job_log_session from a prior date.

    Returns the unclosed session dict if found, or None.
    Called by pwa_chat.py before starting a new day's log so the foreman
    can be prompted to close out yesterday before starting today.
    """
    try:
        sb = get_supabase()
        result = sb.table(JLS.TABLE).select("*").eq(
            JLS.CLIENT_ID, client_id
        ).eq(
            JLS.EMPLOYEE_ID, employee_id
        ).eq(
            JLS.STATUS, "open"
        ).lt(
            JLS.LOG_DATE, _today()   # strictly before today
        ).order(JLS.LOG_DATE, desc=True).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[{_ts()}] WARN job_log: check_missed_log failed — {e}")
        return None


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def get_active_session(client_id: str, employee_id: str, session_id: str) -> dict | None:
    """
    Return an in-progress job_log_session for this employee + chat session,
    or None if no active session exists.
    """
    try:
        sb = get_supabase()
        result = sb.table(JLS.TABLE).select("*").eq(
            JLS.CLIENT_ID, client_id
        ).eq(
            JLS.EMPLOYEE_ID, employee_id
        ).eq(
            JLS.SESSION_ID, session_id
        ).not_.in_(JLS.STATUS, ["closed", "abandoned"]).order(
            JLS.CREATED_AT, desc=True
        ).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[{_ts()}] WARN job_log: get_active_session failed — {e}")
        return None


def _create_session(client_id: str, employee_id: str, session_id: str,
                    log_date: str | None = None) -> dict | None:
    try:
        sb = get_supabase()
        result = sb.table(JLS.TABLE).insert({
            JLS.CLIENT_ID:   client_id,
            JLS.EMPLOYEE_ID: employee_id,
            JLS.SESSION_ID:  session_id,
            JLS.LOG_DATE:    log_date or _today(),
            JLS.STATUS:      "open",
            JLS.CURRENT_STEP: "select_job",
        }).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[{_ts()}] ERROR job_log: _create_session failed — {e}")
        return None


def _update_session(session_pk: str, updates: dict) -> bool:
    try:
        sb = get_supabase()
        updates[JLS.UPDATED_AT] = datetime.now(timezone.utc).isoformat()
        sb.table(JLS.TABLE).update(updates).eq(JLS.ID, session_pk).execute()
        return True
    except Exception as e:
        print(f"[{_ts()}] ERROR job_log: _update_session failed — {e}")
        return False


# ---------------------------------------------------------------------------
# Scratchpad helpers (state stored in JLS.NOTES as JSON)
# ---------------------------------------------------------------------------

def _get_state(session: dict) -> dict:
    try:
        raw = session.get(JLS.NOTES) or "{}"
        return json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        return {}


def _set_state(session_pk: str, data: dict) -> None:
    _update_session(session_pk, {JLS.NOTES: json.dumps(data)})


# ---------------------------------------------------------------------------
# DB lookups
# ---------------------------------------------------------------------------

def _get_open_jobs(client_id: str) -> list[dict]:
    """
    Return open work_order jobs for this client, most recently updated first.
    These are the multi-day jobs that need daily logging.
    """
    try:
        sb = get_supabase()
        result = sb.table(Jobs.TABLE).select(
            f"{Jobs.ID}, {Jobs.JOB_DESCRIPTION}, {Jobs.JOB_TYPE}, "
            f"{Jobs.STATUS}, {Jobs.CUSTOMER_ID}"
        ).eq(Jobs.CLIENT_ID, client_id).eq(
            Jobs.STATUS, "work_order"
        ).order("updated_at", desc=True).limit(20).execute()

        jobs = result.data or []

        # Attach customer names
        if jobs:
            cust_ids = list({j[Jobs.CUSTOMER_ID] for j in jobs if j.get(Jobs.CUSTOMER_ID)})
            if cust_ids:
                cust_result = sb.table("customers").select(
                    "id, customer_name, customer_address"
                ).in_("id", cust_ids).execute()
                cust_map = {c["id"]: c for c in (cust_result.data or [])}
                for j in jobs:
                    c = cust_map.get(j.get(Jobs.CUSTOMER_ID) or "")
                    j["customer_name"] = c.get("customer_name", "Unknown") if c else "Unknown"
                    j["customer_address"] = c.get("customer_address", "") if c else ""

        return jobs
    except Exception as e:
        print(f"[{_ts()}] WARN job_log: _get_open_jobs failed — {e}")
        return []


def _get_active_employees(client_id: str) -> list[dict]:
    """Return active employees for this client, sorted by name."""
    try:
        sb = get_supabase()
        result = sb.table(EMP.TABLE).select(
            f"{EMP.ID}, {EMP.NAME}, {EMP.ROLE}"
        ).eq(EMP.CLIENT_ID, client_id).eq(
            EMP.ACTIVE, True
        ).order(EMP.NAME).execute()
        return result.data or []
    except Exception as e:
        print(f"[{_ts()}] WARN job_log: _get_active_employees failed — {e}")
        return []


def _get_yesterday_equipment(client_id: str, job_id: str) -> list[str]:
    """
    Return the equipment names logged on the most recent prior day for this job.
    Used to surface the "same as yesterday?" prompt.
    """
    try:
        sb = get_supabase()
        # Find the most recent log_date for this job (excluding today)
        result = sb.table(JEL.TABLE).select(
            f"{JEL.EQUIPMENT_NAME}, {JEL.LOG_DATE}"
        ).eq(JEL.CLIENT_ID, client_id).eq(
            JEL.JOB_ID, job_id
        ).lt(JEL.LOG_DATE, _today()).order(
            JEL.LOG_DATE, desc=True
        ).limit(20).execute()

        rows = result.data or []
        if not rows:
            return []

        # Get the most recent date only
        most_recent_date = rows[0][JEL.LOG_DATE]
        return [r[JEL.EQUIPMENT_NAME] for r in rows
                if r[JEL.LOG_DATE] == most_recent_date]
    except Exception as e:
        print(f"[{_ts()}] WARN job_log: _get_yesterday_equipment failed — {e}")
        return []


def _find_employee_by_name(client_id: str, name: str,
                            employees: list[dict]) -> dict | None:
    """Fuzzy match a name against the employee list."""
    name_lower = name.lower().strip()
    # Exact match first
    for e in employees:
        if (e.get(EMP.NAME) or "").lower() == name_lower:
            return e
    # Partial match — last name or first name
    parts = name_lower.split()
    for e in employees:
        emp_name = (e.get(EMP.NAME) or "").lower()
        for part in parts:
            if len(part) >= 3 and part in emp_name:
                return e
    return None


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------

def _reply(text: str, action: dict | None = None) -> dict:
    return {
        "success": True,
        "reply": text,
        "action": action,
        "model": "job_log_flow",
        "system_prompt_chars": 0,
        "error": None,
    }


def _error(text: str) -> dict:
    return {
        "success": False,
        "reply": text,
        "action": None,
        "model": "job_log_flow",
        "system_prompt_chars": 0,
        "error": text,
    }


# ---------------------------------------------------------------------------
# Crew parsing helpers
# ---------------------------------------------------------------------------

_ME_RE = re.compile(r'\b(me|i|myself)\b', re.IGNORECASE)
_AND_RE = re.compile(r'\band\b|,|\+', re.IGNORECASE)
_JUST_ME_RE = re.compile(r'^\s*(just\s+me|only\s+me|me\s+only|solo)\s*$', re.IGNORECASE)
_DONE_RE = re.compile(r'^\s*(done|no|no more|that\'?s?\s*(it|all)|finish|finished|just those)\s*$', re.IGNORECASE)
_YES_RE = re.compile(r'^\s*(yes|y|yep|yeah|yup|sure|ok|okay|correct|right|same)\s*$', re.IGNORECASE)
_NO_RE = re.compile(r'^\s*(no|n|nope|nah|skip|none|nobody|nothing)\s*$', re.IGNORECASE)


def _is_yes(text: str) -> bool:
    return bool(_YES_RE.match(text.strip()))


def _is_no(text: str) -> bool:
    return bool(_NO_RE.match(text.strip()))


def _is_done(text: str) -> bool:
    return bool(_DONE_RE.match(text.strip()))


def _parse_crew_input(text: str, logged_in_id: str,
                      employees: list[dict]) -> tuple[list[str], list[str]]:
    """
    Parse crew input like "me and Jim" or "1 and 3" or "just me".

    Returns:
        (resolved_ids, unresolved_names)
        resolved_ids: list of employee UUIDs
        unresolved_names: names that couldn't be matched (for disambiguation)
    """
    resolved = []
    unresolved = []

    # "just me" shortcut
    if _JUST_ME_RE.match(text.strip()):
        return [logged_in_id], []

    # Split on "and", comma, "+"
    parts = [p.strip() for p in _AND_RE.split(text) if p.strip()]

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # "me" / "I" → logged-in employee
        if _ME_RE.fullmatch(part.strip()):
            if logged_in_id not in resolved:
                resolved.append(logged_in_id)
            continue

        # Numeric index — "1", "2", etc.
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(employees):
                eid = employees[idx][EMP.ID]
                if eid not in resolved:
                    resolved.append(eid)
            else:
                unresolved.append(part)
            continue

        # Name fuzzy match
        emp = _find_employee_by_name(None, part, employees)
        if emp:
            eid = emp[EMP.ID]
            if eid not in resolved:
                resolved.append(eid)
        else:
            unresolved.append(part)

    # Always include the logged-in foreman if nothing resolved to them
    if logged_in_id not in resolved and resolved:
        pass  # Foreman explicitly excluded — that's valid
    elif not resolved:
        resolved.append(logged_in_id)

    return resolved, unresolved


# ---------------------------------------------------------------------------
# Unit parsing for materials
# ---------------------------------------------------------------------------

_UNIT_SYNONYMS = {
    "yard": "yards", "yds": "yards", "yd": "yards",
    "ton": "tons", "t": "tons",
    "foot": "feet", "ft": "feet", "lf": "lf", "linear foot": "lf", "linear feet": "lf",
    "each": "each", "ea": "each", "pc": "each", "piece": "each", "pieces": "each",
    "bag": "bags", "bundle": "bundles",
    "gallon": "gallons", "gal": "gallons",
    "box": "boxes", "bx": "boxes",
}


def _normalize_unit(raw: str) -> str:
    clean = raw.strip().lower()
    return _UNIT_SYNONYMS.get(clean, clean)


def _parse_quantity_unit(text: str) -> tuple[float | None, str | None]:
    """
    Try to extract quantity and unit from text like "34 yards" or "100 feet".
    Returns (quantity, unit) or (None, None) if not parseable.
    """
    match = re.match(
        r'^\s*(\d+(?:\.\d+)?)\s*([a-zA-Z\s]+)?\s*$',
        text.strip()
    )
    if match:
        qty = float(match.group(1))
        unit_raw = (match.group(2) or "each").strip()
        return qty, _normalize_unit(unit_raw)
    # Try just a number
    num_match = re.match(r'^\s*(\d+(?:\.\d+)?)\s*$', text.strip())
    if num_match:
        return float(num_match.group(1)), None
    return None, None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def start(client_id: str, employee_id: str, session_id: str,
          missed_session: dict | None = None) -> dict:
    """
    Start a new job log session.

    If missed_session is provided, the flow begins by handling the
    unclosed prior day before starting today.
    """
    if missed_session:
        # Resume the missed session to close it out
        _update_session(missed_session[JLS.ID], {
            JLS.CURRENT_STEP: "missed_log_check",
            JLS.SESSION_ID: session_id,  # re-link to current chat session
        })
        job_desc = missed_session.get("job_description") or "your job"
        missed_date = missed_session.get(JLS.LOG_DATE, "yesterday")
        return _reply(
            f"You never closed out the log for {missed_date}. "
            f"What time did you wrap up that day? (e.g. '4:30' or '4pm')"
        )

    session = _create_session(client_id, employee_id, session_id)
    if not session:
        return _error("Couldn't start the job log. Try again.")

    return _show_job_list(session, client_id)


def _show_job_list(session: dict, client_id: str) -> dict:
    """Present the open work_order job list."""
    session_pk = session[JLS.ID]
    jobs = _get_open_jobs(client_id)

    if not jobs:
        _update_session(session_pk, {JLS.STATUS: "abandoned"})
        return _reply(
            "No open work orders found. "
            "Jobs need to be in 'work_order' status to appear here."
        )

    lines = ["Which job are you on today?"]
    for i, j in enumerate(jobs[:10], 1):
        cust = j.get("customer_name", "Unknown")
        addr = j.get("customer_address", "")
        desc = j.get(Jobs.JOB_DESCRIPTION) or j.get(Jobs.JOB_TYPE) or "work order"
        addr_part = f" — {addr}" if addr else ""
        lines.append(f"  {i}) {cust}{addr_part} ({desc})")

    state = {"jobs": [j[Jobs.ID] for j in jobs[:10]],
              "job_labels": [j.get("customer_name", "Job") for j in jobs[:10]]}
    _set_state(session_pk, state)
    _update_session(session_pk, {JLS.CURRENT_STEP: "select_job"})

    return _reply("\n".join(lines))


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

def handle_input(session: dict, user_message: str,
                 client_id: str, employee_id: str) -> dict:
    """Route the foreman's message to the correct state handler."""
    session_pk   = session[JLS.ID]
    current_step = session.get(JLS.CURRENT_STEP, "select_job")

    # Global cancel
    if re.search(r'\b(cancel|stop|nevermind|abort|quit)\b',
                 user_message, re.IGNORECASE):
        _update_session(session_pk, {JLS.STATUS: "abandoned"})
        return _reply("Job log cancelled.")

    if current_step == "missed_log_check":
        return _handle_missed_log(session, user_message, client_id, employee_id)
    if current_step == "missed_materials":
        return _handle_missed_materials(session, user_message, client_id, employee_id)
    if current_step == "select_job":
        return _handle_select_job(session, user_message, client_id, employee_id)
    if current_step == "confirm_crew":
        return _handle_confirm_crew(session, user_message, client_id, employee_id)
    if current_step == "confirm_equipment":
        return _handle_confirm_equipment(session, user_message, client_id, employee_id)
    if current_step == "add_equipment":
        return _handle_add_equipment(session, user_message, client_id, employee_id)
    if current_step == "log_materials":
        return _handle_log_materials(session, user_message, client_id, employee_id)
    if current_step == "material_qty":
        return _handle_material_qty(session, user_message, client_id, employee_id)
    if current_step == "material_unit":
        return _handle_material_unit(session, user_message, client_id, employee_id)
    if current_step == "material_supplier":
        return _handle_material_supplier(session, user_message, client_id, employee_id)
    if current_step == "day_close":
        return _handle_day_close(session, user_message, client_id, employee_id)

    return _show_job_list(session, client_id)


# ---------------------------------------------------------------------------
# State handlers
# ---------------------------------------------------------------------------

def _handle_missed_log(session: dict, text: str,
                        client_id: str, employee_id: str) -> dict:
    """
    Foreman forgot to close out yesterday.
    Ask what time they left, then whether there were any materials.
    """
    session_pk = session[JLS.ID]
    state = _get_state(session)

    # Try to parse a time from the input
    time_match = re.search(
        r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b',
        text, re.IGNORECASE
    )

    if not time_match:
        return _reply(
            "I didn't catch a time. "
            "What time did you finish up yesterday? (e.g. '4:30pm' or '4:30')"
        )

    # We have a time — store it and ask about missed materials
    state["missed_end_time"] = text.strip()
    _set_state(session_pk, state)
    _update_session(session_pk, {JLS.CURRENT_STEP: "missed_materials"})

    return _reply(
        f"Got it — {text.strip()}. "
        "Any materials from yesterday you want to log before we start today?"
    )


def _handle_missed_materials(session: dict, text: str,
                              client_id: str, employee_id: str) -> dict:
    """After handling missed close-out, start today's fresh log."""
    session_pk = session[JLS.ID]
    state = _get_state(session)

    if _is_yes(text):
        # They want to log missed materials — pivot to material logging for yesterday
        state["logging_for_yesterday"] = True
        _set_state(session_pk, state)
        _update_session(session_pk, {JLS.CURRENT_STEP: "log_materials"})
        return _reply("What material? (e.g. '34 yards of loam')")

    # No missed materials or done — close out yesterday, start today
    _update_session(session_pk, {
        JLS.STATUS: "closed",
        JLS.CURRENT_STEP: "select_job",
    })

    # Create a fresh session for today
    session = _create_session(client_id, employee_id, session.get(JLS.SESSION_ID, ""))
    if not session:
        return _error("Couldn't start today's log. Try again.")

    return _show_job_list(session, client_id)


def _handle_select_job(session: dict, text: str,
                        client_id: str, employee_id: str) -> dict:
    """Tech picks a job from the numbered list."""
    session_pk = session[JLS.ID]
    state = _get_state(session)
    job_ids = state.get("jobs", [])
    job_labels = state.get("job_labels", [])

    stripped = text.strip()
    if not stripped.isdigit():
        return _reply(
            f"Pick a number from the list. "
            f"(1–{len(job_ids)})"
        )

    idx = int(stripped) - 1
    if not (0 <= idx < len(job_ids)):
        return _reply(f"Pick a number between 1 and {len(job_ids)}.")

    job_id = job_ids[idx]
    job_label = job_labels[idx] if idx < len(job_labels) else "this job"

    state["job_id"] = job_id
    state["job_label"] = job_label
    _set_state(session_pk, state)
    _update_session(session_pk, {
        JLS.JOB_ID: job_id,
        JLS.CURRENT_STEP: "confirm_crew",
    })

    # Build employee list for selection
    employees = _get_active_employees(client_id)
    state["employees"] = [
        {EMP.ID: e[EMP.ID], EMP.NAME: e[EMP.NAME], EMP.ROLE: e[EMP.ROLE]}
        for e in employees
    ]
    _set_state(session_pk, state)

    lines = [f"Got it — {job_label}. Who's on the job today?"]
    for i, e in enumerate(employees[:12], 1):
        role = (e.get(EMP.ROLE) or "").replace("_", " ")
        lines.append(f"  {i}) {e[EMP.NAME]}" + (f" ({role})" if role else ""))
    lines.append("Type numbers or names — e.g. 'me and 2' or 'me, Jim, and Mike'")

    return _reply("\n".join(lines))


def _handle_confirm_crew(session: dict, text: str,
                          client_id: str, employee_id: str) -> dict:
    """Parse crew input and confirm."""
    session_pk = session[JLS.ID]
    state = _get_state(session)
    employees = state.get("employees", [])

    resolved_ids, unresolved = _parse_crew_input(text, employee_id, employees)

    if unresolved:
        # Build disambiguation list for unresolved names
        lines = [f"I couldn't find: {', '.join(unresolved)}. Pick from the list:"]
        for i, e in enumerate(employees[:12], 1):
            lines.append(f"  {i}) {e[EMP.NAME]}")
        return _reply("\n".join(lines))

    # Resolved — build readable names for confirmation
    emp_map = {e[EMP.ID]: e[EMP.NAME] for e in employees}
    # Also include logged-in employee by fetching if not in list
    names = []
    for eid in resolved_ids:
        name = emp_map.get(eid)
        if not name:
            try:
                sb = get_supabase()
                r = sb.table(EMP.TABLE).select(EMP.NAME).eq(EMP.ID, eid).limit(1).execute()
                name = r.data[0][EMP.NAME] if r.data else eid[:8]
            except Exception:
                name = eid[:8]
        names.append(name)

    state["crew_ids"] = resolved_ids
    state["crew_names"] = names
    _set_state(session_pk, state)

    crew_str = ", ".join(names)
    job_label = state.get("job_label", "the job")

    # Check for yesterday's equipment
    job_id = state.get("job_id")
    yesterday_equip = _get_yesterday_equipment(client_id, job_id) if job_id else []
    state["yesterday_equipment"] = yesterday_equip
    _set_state(session_pk, state)

    _update_session(session_pk, {
        JLS.CREW_CONFIRMED: True,
        JLS.CURRENT_STEP: "confirm_equipment",
    })

    if yesterday_equip:
        equip_list = ", ".join(yesterday_equip)
        return _reply(
            f"Crew: {crew_str}. Same equipment as last time?\n"
            f"({equip_list})\n"
            f"Say yes, or list what's different."
        )
    else:
        return _reply(
            f"Crew: {crew_str}.\n"
            "What equipment are you using today? (or 'none')"
        )


def _handle_confirm_equipment(session: dict, text: str,
                               client_id: str, employee_id: str) -> dict:
    """
    Confirm or replace equipment list.
    'yes' → copy yesterday's list
    'none' / 'no equipment' → no equipment today
    anything else → treat as new list, then ask "anything else?"
    """
    session_pk = session[JLS.ID]
    state = _get_state(session)
    yesterday = state.get("yesterday_equipment", [])

    text_lower = text.strip().lower()

    if _is_yes(text) and yesterday:
        # Use yesterday's equipment
        state["equipment"] = yesterday
        _set_state(session_pk, state)
        _update_session(session_pk, {
            JLS.EQUIPMENT_CONFIRMED: True,
            JLS.CURRENT_STEP: "log_materials",
        })
        return _reply(
            f"Equipment: {', '.join(yesterday)}.\n"
            "Any material deliveries today? (yes or no)"
        )

    if text_lower in ("none", "no equipment", "no machines", "nothing"):
        state["equipment"] = []
        _set_state(session_pk, state)
        _update_session(session_pk, {
            JLS.EQUIPMENT_CONFIRMED: True,
            JLS.CURRENT_STEP: "log_materials",
        })
        return _reply("No equipment logged. Any material deliveries today?")

    # Treat as new equipment entry — parse comma/and separated list
    items = [p.strip() for p in re.split(r',|\band\b', text, flags=re.IGNORECASE)
             if p.strip()]
    state["equipment"] = items
    _set_state(session_pk, state)
    _update_session(session_pk, {JLS.CURRENT_STEP: "add_equipment"})

    equip_str = ", ".join(items)
    return _reply(f"Got: {equip_str}. Anything else? (or 'done')")


def _handle_add_equipment(session: dict, text: str,
                           client_id: str, employee_id: str) -> dict:
    """Loop for adding more equipment."""
    session_pk = session[JLS.ID]
    state = _get_state(session)

    if _is_done(text) or _is_no(text):
        equip = state.get("equipment", [])
        _update_session(session_pk, {
            JLS.EQUIPMENT_CONFIRMED: True,
            JLS.CURRENT_STEP: "log_materials",
        })
        equip_str = ", ".join(equip) if equip else "none"
        return _reply(
            f"Equipment: {equip_str}.\n"
            "Any material deliveries today? (yes or no)"
        )

    # Add to equipment list
    new_items = [p.strip() for p in re.split(r',|\band\b', text, flags=re.IGNORECASE)
                 if p.strip()]
    existing = state.get("equipment", [])
    existing.extend(new_items)
    state["equipment"] = existing
    _set_state(session_pk, state)

    return _reply(f"Added {', '.join(new_items)}. Anything else? (or 'done')")


def _handle_log_materials(session: dict, text: str,
                           client_id: str, employee_id: str) -> dict:
    """Ask if there are materials, then start the material entry loop."""
    session_pk = session[JLS.ID]
    state = _get_state(session)

    # Initialize materials list if not present
    if "materials" not in state:
        state["materials"] = []
        _set_state(session_pk, state)

    if _is_no(text) or _is_done(text):
        # No materials → go to day close
        _update_session(session_pk, {JLS.CURRENT_STEP: "day_close"})
        return _show_day_summary(session, client_id)

    if _is_yes(text):
        return _reply("What material? (e.g. '34 yards of loam' or just 'loam')")

    # They typed a material name directly — start the entry
    state["current_material"] = {"name": text.strip()}
    _set_state(session_pk, state)
    _update_session(session_pk, {JLS.CURRENT_STEP: "material_qty"})
    return _reply(f"How much {text.strip()}?")


def _handle_material_qty(session: dict, text: str,
                          client_id: str, employee_id: str) -> dict:
    """Parse quantity (and optional unit) for current material."""
    session_pk = session[JLS.ID]
    state = _get_state(session)

    qty, unit = _parse_quantity_unit(text)

    if qty is None:
        return _reply("I didn't catch a quantity. How much? (e.g. '34' or '34 yards')")

    current = state.get("current_material", {})
    current["qty"] = qty

    if unit:
        # Got unit inline — ask for supplier
        current["unit"] = unit
        state["current_material"] = current
        _set_state(session_pk, state)
        _update_session(session_pk, {JLS.CURRENT_STEP: "material_supplier"})
        return _reply(f"{qty} {unit}. Supplier? (or 'skip')")
    else:
        # Need unit separately
        state["current_material"] = current
        _set_state(session_pk, state)
        _update_session(session_pk, {JLS.CURRENT_STEP: "material_unit"})
        return _reply(
            f"{qty} — what unit?\n"
            "yards / tons / feet / lf / each / bags / gallons"
        )


def _handle_material_unit(session: dict, text: str,
                           client_id: str, employee_id: str) -> dict:
    """Accept unit for current material."""
    session_pk = session[JLS.ID]
    state = _get_state(session)

    unit = _normalize_unit(text.strip())
    current = state.get("current_material", {})
    current["unit"] = unit
    state["current_material"] = current
    _set_state(session_pk, state)
    _update_session(session_pk, {JLS.CURRENT_STEP: "material_supplier"})

    name = current.get("name", "")
    qty = current.get("qty", "")
    return _reply(f"{qty} {unit} of {name}. Supplier? (or 'skip')")


def _handle_material_supplier(session: dict, text: str,
                                client_id: str, employee_id: str) -> dict:
    """Record supplier (optional) and save the material entry."""
    session_pk = session[JLS.ID]
    state = _get_state(session)

    current = state.get("current_material", {})
    skip_words = {"skip", "no", "n", "none", "unknown", "-"}
    supplier = None if text.strip().lower() in skip_words else text.strip()
    current["supplier"] = supplier

    # Append to materials list
    materials = state.get("materials", [])
    materials.append(current)
    state["materials"] = materials
    state["current_material"] = {}
    _set_state(session_pk, state)
    _update_session(session_pk, {JLS.CURRENT_STEP: "log_materials"})

    name = current.get("name", "")
    qty = current.get("qty", "")
    unit = current.get("unit", "")
    sup_str = f" from {supplier}" if supplier else ""

    return _reply(
        f"Logged: {qty} {unit} of {name}{sup_str}.\n"
        "Any more materials? (or 'done')"
    )


def _show_day_summary(session: dict, client_id: str) -> dict:
    """Build and display the day summary before writing to DB."""
    state = _get_state(session)
    session_pk = session[JLS.ID]

    job_label  = state.get("job_label", "the job")
    crew_names = state.get("crew_names", [])
    equipment  = state.get("equipment", [])
    materials  = state.get("materials", [])
    log_date   = session.get(JLS.LOG_DATE, _today())

    lines = [f"Here's today's log for {job_label} ({log_date}):"]
    lines.append(f"\nCrew:      {', '.join(crew_names) if crew_names else 'None logged'}")

    if equipment:
        lines.append(f"Equipment: {', '.join(equipment)}")
    else:
        lines.append("Equipment: None")

    if materials:
        lines.append("Materials:")
        for m in materials:
            sup_str = f" ({m.get('supplier')})" if m.get("supplier") else ""
            lines.append(
                f"  • {m.get('qty', '')} {m.get('unit', '')} of "
                f"{m.get('name', '')}{sup_str}"
            )
    else:
        lines.append("Materials: None")

    lines.append("\nLooks right? (yes to save, no to cancel)")

    _update_session(session_pk, {JLS.CURRENT_STEP: "day_close"})
    return _reply("\n".join(lines))


def _handle_day_close(session: dict, text: str,
                       client_id: str, employee_id: str) -> dict:
    """Confirm and write all three log tables."""
    session_pk = session[JLS.ID]
    state = _get_state(session)

    if _is_no(text):
        _update_session(session_pk, {JLS.STATUS: "abandoned"})
        return _reply("Log cancelled. Nothing was saved.")

    if not _is_yes(text):
        return _show_day_summary(session, client_id)

    # Write to DB
    job_id     = state.get("job_id") or session.get(JLS.JOB_ID)
    log_date   = session.get(JLS.LOG_DATE, _today())
    crew_ids   = state.get("crew_ids", [employee_id])
    equipment  = state.get("equipment", [])
    materials  = state.get("materials", [])

    try:
        sb = get_supabase()

        # 1. Write crew log — one row per crew member
        for eid in crew_ids:
            try:
                sb.table(JCL.TABLE).insert({
                    JCL.CLIENT_ID:   client_id,
                    JCL.JOB_ID:      job_id,
                    JCL.EMPLOYEE_ID: eid,
                    JCL.LOG_DATE:    log_date,
                    JCL.LOGGED_BY:   employee_id,
                    JCL.BILLED:      False,
                }).execute()
            except Exception as e:
                # Unique constraint violation = already logged today, non-fatal
                print(f"[{_ts()}] WARN job_log: crew insert skipped for {eid[:8]} — {e}")

        # 2. Write equipment log — one row per piece of equipment
        for equip_name in equipment:
            try:
                sb.table(JEL.TABLE).insert({
                    JEL.CLIENT_ID:      client_id,
                    JEL.JOB_ID:         job_id,
                    JEL.LOGGED_BY:      employee_id,
                    JEL.EQUIPMENT_NAME: equip_name,
                    JEL.LOG_DATE:       log_date,
                    JEL.BILLED:         False,
                }).execute()
            except Exception as e:
                print(f"[{_ts()}] WARN job_log: equipment insert failed — {e}")

        # 3. Write material log — one row per material entry
        for mat in materials:
            try:
                sb.table(JML.TABLE).insert({
                    JML.CLIENT_ID:     client_id,
                    JML.JOB_ID:        job_id,
                    JML.LOGGED_BY:     employee_id,
                    JML.MATERIAL_NAME: mat.get("name", ""),
                    JML.QUANTITY:      float(mat.get("qty", 0)),
                    JML.UNIT:          mat.get("unit", "each"),
                    JML.SUPPLIER:      mat.get("supplier"),
                    JML.LOG_DATE:      log_date,
                    JML.BILLABLE:      True,
                    JML.BILLED:        False,
                }).execute()
            except Exception as e:
                print(f"[{_ts()}] WARN job_log: material insert failed — {e}")

        # 4. Close the session
        _update_session(session_pk, {JLS.STATUS: "closed"})

        # 5. Touch the job's updated_at so it floats to top of list tomorrow
        try:
            sb.table(Jobs.TABLE).update({
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq(Jobs.ID, job_id).execute()
        except Exception:
            pass

        crew_count  = len(crew_ids)
        equip_count = len(equipment)
        mat_count   = len(materials)

        print(
            f"[{_ts()}] INFO job_log: Day log saved — job={job_id[:8]} "
            f"crew={crew_count} equip={equip_count} materials={mat_count} date={log_date}"
        )

        return _reply(
            f"Saved. {crew_count} crew, {equip_count} equipment, "
            f"{mat_count} material {'entry' if mat_count == 1 else 'entries'} "
            f"logged for {log_date}."
        )

    except Exception as e:
        print(f"[{_ts()}] ERROR job_log: day_close write failed — {e}")
        return _error("Something went wrong saving the log. Please try again.")
