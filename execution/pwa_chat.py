"""
pwa_chat.py — PWA AI chat agent (Step 6b — action chips)

Wraps Claude Haiku as a router (NOT an executor). The chat agent
classifies the tech's intent and, when appropriate, returns a
structured "action" dict that the PWA renders as a tappable chip.
The chip's tap handler hits the existing PWA endpoint (e.g.
/pwa/api/job/new) that owns the actual write. The chat agent
itself never calls proposal_agent.run() or any other write path.

Return shape:
    {
        "success": True,
        "reply":   "I can draft that estimate for Alice — $325.",
        "action":  {
            "type":     "create_proposal",   # or mark_job_done / start_job / clock_in / clock_out
            "label":    "Create estimate · $325",
            "params":   { ... endpoint-specific args ... },
            "endpoint": "/pwa/api/job/new",
            "method":   "POST",
        } or None,
        "model":   "haiku",
        "system_prompt_chars": int,
        "error":   str or None,
    }

System prompt budget: under 1000 input tokens, repeated every turn.
"""

import os
import sys
import json
import re
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.call_claude import call_claude
from execution.db_connection import get_client as get_supabase


# Hard ceiling on system prompt length. The chat is going to fire on
# every user message, so input tokens dominate cost. Stay tight.
SYSTEM_PROMPT_TOKEN_TARGET = 1000
SYSTEM_PROMPT_CHAR_TARGET = SYSTEM_PROMPT_TOKEN_TARGET * 4  # ~4 chars/token


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _build_route_summary(route: list) -> str:
    """
    Compact route summary for the system prompt. Aim for ~50 tokens
    on a typical 5-job day. No addresses or phone numbers — those are
    available on the route screen and would bloat input tokens.

    Format:
        Today's route (3 jobs, 1 done):
        1. ✓ Alice Smith — pump out — $325
        2. → Bob Jones — inspection — $250  (current)
        3. Carol Duggan — repair
    """
    if not route:
        return "Today's route: empty (no jobs dispatched)."

    done_count = sum(1 for j in route if j.get("job_end"))
    lines = [f"Today's route ({len(route)} jobs, {done_count} done):"]

    for i, job in enumerate(route, 1):
        cust = job.get("customer_name") or "Customer"
        jtype = (job.get("job_type") or "").replace("_", " ")
        amt = job.get("estimated_amount")

        is_done = bool(job.get("job_end"))
        is_current = bool(job.get("job_start") and not job.get("job_end"))

        marker = "✓" if is_done else ("→" if is_current else " ")
        line = f"{i}. {marker} {cust}"
        if jtype:
            line += f" — {jtype}"
        if amt:
            line += f" — ${int(amt)}"
        if is_current:
            line += "  (current)"
        lines.append(line)

    return "\n".join(lines)


# Words that look like names but aren't — used to filter the candidate
# name extractor below. Keep this list small; Claude can sort out edge
# cases on the actual prompt side.
_NAME_BLOCKLIST = {
    "Hey", "Hi", "Hello", "Yo", "Ok", "OK", "Okay",
    "Yes", "No", "Yeah", "Nope", "Sure", "Fine",
    "Today", "Tomorrow", "Yesterday", "Tonight", "Morning",
    "Need", "Want", "Send", "Create", "Make", "Add", "New",
    "Estimate", "Invoice", "Quote", "Bill", "Bid", "Pump", "Repair",
    "Mark", "Done", "Start", "Finish", "Clock", "In", "Out",
    "Bolts", "Bolts11",
}


def _extract_candidate_names(text: str) -> list[str]:
    """
    Pull title-cased word pairs (and singles) out of the tech's message
    that look like person names. The regex is intentionally permissive —
    we'd rather over-extract and let the DB lookup fail cheaply than
    miss a real customer mention.

    Examples:
        "Send Robert Poulin an estimate" → ["Robert Poulin"]
        "alice smith needs a pump out"   → []  (lowercase — the tech
                                                 should at least cap names)
        "DONE Bob Jones"                 → ["Bob Jones"]

    The blocklist filters greetings and command words that happen to
    be capitalized.
    """
    if not text:
        return []
    # Match runs of 1-3 title-cased words. Allows hyphens/apostrophes
    # inside (e.g. "O'Brien", "Smith-Jones"). Two-word matches preferred.
    pattern = re.compile(
        r"\b([A-Z][a-z'\-]+(?:\s+[A-Z][a-z'\-]+){0,2})\b"
    )
    matches = pattern.findall(text)
    out = []
    seen = set()
    for m in matches:
        # Drop pure-blocklist runs like "Hi" or "Send Bolts11"
        words = m.split()
        non_block = [w for w in words if w not in _NAME_BLOCKLIST]
        if not non_block:
            continue
        # Single-word matches are usually too noisy — only keep if it's
        # the only candidate AND it's at least 4 chars (filters "Bob"
        # but keeps "Roberts"). Two-plus-word matches always pass.
        if len(non_block) == 1 and len(non_block[0]) < 4:
            continue
        candidate = " ".join(non_block)
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _find_customer(client_id: str, name: str) -> dict | None:
    """
    Look up a customer by name in the tenant's customers table.
    Returns the first match (most recently created if multiple) or None.

    Multi-tenant safe: filters by client_id before the name match.
    """
    if not name or not name.strip():
        return None
    try:
        sb = get_supabase()
        result = sb.table("customers").select(
            "id, customer_name, customer_phone, customer_email, customer_address"
        ).eq("client_id", client_id).ilike(
            "customer_name", f"%{name.strip()}%"
        ).order("created_at", desc=True).limit(3).execute()
        if result.data:
            return result.data[0]
    except Exception as e:
        print(f"[{_ts()}] WARN pwa_chat: customer lookup failed for {name!r} — {e}")
    return None


def _find_customers_in_message(client_id: str, text: str) -> list[dict]:
    """
    Extract candidate names from the tech's message and look each one
    up in the customers table. Returns at most 3 hits, dedup'd by id.
    Empty list if nothing matched.

    This is the "DB before prompt" piece of bug #1 — the agent gets
    real customer info injected into the system prompt so it doesn't
    ask for fields the codebase already has.
    """
    candidates = _extract_candidate_names(text)
    if not candidates:
        return []

    found = []
    seen_ids = set()
    for name in candidates[:5]:  # cap candidates to avoid runaway lookups
        cust = _find_customer(client_id, name)
        if cust and cust.get("id") not in seen_ids:
            seen_ids.add(cust["id"])
            found.append(cust)
        if len(found) >= 3:
            break
    return found


def _build_customer_context(customers: list[dict]) -> str:
    """
    Render the matched-customer block that gets injected into the
    system prompt. Compact format — names + the fields the agent
    needs to skip asking for them. Keeps the prompt under budget.
    """
    if not customers:
        return ""
    lines = ["MATCHED CUSTOMERS (already in your DB — DO NOT ask for these fields):"]
    for c in customers:
        parts = [f"- {c.get('customer_name', 'Customer')}"]
        if c.get("customer_phone"):
            parts.append(f"phone {c['customer_phone']}")
        if c.get("customer_email"):
            parts.append(f"email {c['customer_email']}")
        if c.get("customer_address"):
            parts.append(f"addr {c['customer_address']}")
        lines.append(" · ".join(parts))
    return "\n".join(lines)


def _build_system_prompt(employee_name: str, employee_role: str,
                        business_name: str, route_summary: str,
                        customer_context: str = "") -> str:
    """
    System prompt with hard anti-hallucination rules and optional
    customer-context injection.

    Sections in order of importance:
      1. Identity
      2. CRITICAL RULES (anti-hallucination + action JSON contract)
      3. Today's route summary
      4. Matched customers (only when DB lookup found something)
      5. Available actions + JSON response shape
      6. Style rules
    """
    role_label = (employee_role or "field tech").replace("_", " ")

    customer_block = f"\n\n{customer_context}" if customer_context else ""

    return (
        f"AI assistant for {business_name}. Texting {employee_name}, a {role_label}.\n\n"
        f"CRITICAL RULES:\n"
        f"1. You CANNOT create or send anything directly.\n"
        f"2. You MUST return action JSON when the tech requests an action.\n"
        f"3. NEVER tell the tech you did something. Only return the action chip.\n"
        f"4. If you start to say \"I sent\" or \"I created\" — STOP and return action JSON.\n"
        f"5. If customer info is missing after the matched block, ask only for missing fields.\n\n"
        f"{route_summary}{customer_block}\n\n"
        f"ACTIONS — return {{\"reply\":..., \"action\":{{...}}}} when applicable, else {{\"reply\":...}}:\n"
        f"- create_proposal: tech describes a job to estimate. "
        f"params: {{description, customer_name?, customer_phone?, customer_address?, amount?}}. "
        f"If a customer was matched above, copy their fields verbatim.\n"
        f"- mark_job_done / start_job: tech finished/starting a route job. params: {{customer_name}}\n"
        f"- clock_in / clock_out: shift start/end. params: {{}}\n\n"
        f"FORMAT: always valid JSON, no markdown fences. Never invent names or addresses. "
        f"If unsure, reply without an action and ask one short clarifying question.\n\n"
        f"Style: short, plain, 1-3 sentences. Use the tech's name occasionally."
    )


def _route_summary_for_employee(client_id: str, employee_id: str) -> str:
    """Pull today's dispatched jobs for the prompt context."""
    try:
        from execution.dispatch_chain import get_todays_route
        route = get_todays_route(client_id, employee_id)
        return _build_route_summary(route)
    except Exception as e:
        print(f"[{_ts()}] WARN pwa_chat: route summary failed — {e}")
        return "Today's route: unavailable."


def _format_history_for_claude(history: list) -> list:
    """
    Convert pwa_chat_messages rows to Claude messages array.
    Drops anything that isn't 'user' or 'assistant'.
    """
    messages = []
    for row in history:
        role = row.get("role")
        if role not in ("user", "assistant"):
            continue
        content = row.get("content") or ""
        if not content.strip():
            continue
        messages.append({"role": role, "content": content})
    return messages


# Action types we accept from the model. Anything else is dropped.
_ALLOWED_ACTIONS = {
    "create_proposal",
    "mark_job_done",
    "start_job",
    "clock_in",
    "clock_out",
}


def _strip_json_fences(text: str) -> str:
    """
    Strip ```json ... ``` or ``` ... ``` fences if Claude wraps its
    response despite being told not to. Be permissive — model behavior
    drifts and we don't want a fenced response to break the chat.
    """
    s = text.strip()
    if s.startswith("```"):
        # Drop the opening fence (with optional language tag) and closing fence
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _parse_claude_response(raw: str) -> dict:
    """
    Parse Claude's response into {reply, action}.

    The model is instructed to always return JSON. Reality:
      - Sometimes it wraps the JSON in markdown fences. Strip them.
      - Sometimes it returns plain text instead of JSON. Treat the
        whole thing as the reply with no action.
      - Sometimes it returns valid JSON with an unknown action type.
        Drop the action, keep the reply.

    Returns:
        {"reply": str, "action": dict or None}
    """
    if not raw:
        return {"reply": "", "action": None}

    cleaned = _strip_json_fences(raw)

    # Strip a leading "Assistant:" prefix the model occasionally echoes.
    if cleaned.lower().startswith("assistant:"):
        cleaned = cleaned.split(":", 1)[1].strip()

    # Try JSON first
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        # Not JSON — treat the whole response as the reply text
        return {"reply": cleaned, "action": None}

    if not isinstance(parsed, dict):
        return {"reply": cleaned, "action": None}

    reply = parsed.get("reply") or ""
    if not isinstance(reply, str):
        reply = str(reply)
    reply = reply.strip()

    action = parsed.get("action")
    if not isinstance(action, dict):
        return {"reply": reply, "action": None}

    action_type = action.get("type")
    if action_type not in _ALLOWED_ACTIONS:
        # Unknown action — drop it, keep the reply
        return {"reply": reply, "action": None}

    params = action.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    return {
        "reply": reply,
        "action": {
            "type": action_type,
            "params": params,
        },
    }


def chat(
    client_id: str,
    employee_id: str,
    employee_name: str,
    employee_role: str,
    business_name: str,
    user_message: str,
    history: list,
) -> dict:
    """
    Main entry point. Send a user message + history to Claude Haiku,
    parse the JSON response, validate any embedded action, and return
    the assistant's reply + an optional decorated action chip.

    Returns:
        {
            "success": bool,
            "reply": str,
            "action": dict or None,  # see _decorate_action() for shape
            "model": str,
            "system_prompt_chars": int,
            "error": str or None,
        }
    """
    if not user_message or not user_message.strip():
        return {"success": False, "reply": "", "action": None, "error": "Empty message"}

    # Build the system prompt with today's context AND a DB-first lookup
    # of any customer the tech mentioned by name. Bug #1: agent must
    # consult the customers table BEFORE asking the tech for contact
    # info, otherwise it pesters the tech for fields the system already
    # has on file.
    route_summary = _route_summary_for_employee(client_id, employee_id)
    matched_customers = _find_customers_in_message(client_id, user_message)
    customer_context = _build_customer_context(matched_customers)

    system_prompt = _build_system_prompt(
        employee_name=employee_name or "tech",
        employee_role=employee_role or "field tech",
        business_name=business_name or "your business",
        route_summary=route_summary,
        customer_context=customer_context,
    )

    # Token budget guardrail — log if we're getting too long
    char_count = len(system_prompt)
    if char_count > SYSTEM_PROMPT_CHAR_TARGET:
        print(
            f"[{_ts()}] WARN pwa_chat: system prompt is {char_count} chars "
            f"(~{char_count // 4} tokens), exceeds {SYSTEM_PROMPT_TOKEN_TARGET}-token target"
        )

    # Format history (prior turns) + the new user message
    claude_messages = _format_history_for_claude(history)
    claude_messages.append({"role": "user", "content": user_message.strip()})

    # Call Claude — Haiku for cost, the chat agent doesn't need Sonnet's reasoning
    try:
        # call_claude takes a single user_prompt string in this codebase, so
        # we collapse history into the user_prompt and rely on the model's
        # behavior. Better long-term: extend call_claude to accept a messages
        # array, but that's a bigger change. For 6b we still encode recent
        # turns as a transcript inside the user message.
        transcript_lines = []
        for msg in claude_messages[:-1]:
            speaker = "Tech" if msg["role"] == "user" else "Assistant"
            transcript_lines.append(f"{speaker}: {msg['content']}")
        transcript_lines.append(f"Tech: {user_message.strip()}")
        user_prompt = "\n".join(transcript_lines) + "\nAssistant:"

        reply_text = call_claude(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model="haiku",
            max_tokens=1000,
        )
    except Exception as e:
        print(f"[{_ts()}] ERROR pwa_chat: Claude call failed — {e}")
        return {
            "success": False,
            "reply": "Sorry — I can't reach the AI right now. Try again in a moment.",
            "action": None,
            "model": "haiku",
            "system_prompt_chars": char_count,
            "error": str(e),
        }

    if not reply_text or not reply_text.strip():
        return {
            "success": False,
            "reply": "I didn't get an answer back. Try rephrasing?",
            "action": None,
            "model": "haiku",
            "system_prompt_chars": char_count,
            "error": "Empty response",
        }

    # Parse Claude's JSON response → {reply, action}
    parsed = _parse_claude_response(reply_text)
    reply = parsed["reply"] or "Got it."
    raw_action = parsed["action"]

    # Validate + decorate the action server-side (resolves customer
    # name to job_id, builds the chip label, attaches the endpoint).
    decorated_action = None
    if raw_action:
        try:
            from execution.pwa_chat_actions import decorate_action
            decorated_action = decorate_action(
                client_id=client_id,
                employee_id=employee_id,
                action_type=raw_action["type"],
                params=raw_action.get("params") or {},
            )
        except Exception as e:
            print(f"[{_ts()}] WARN pwa_chat: decorate_action failed — {e}")
            decorated_action = None

    return {
        "success": True,
        "reply": reply,
        "action": decorated_action,
        "model": "haiku",
        "system_prompt_chars": char_count,
        "error": None,
    }
