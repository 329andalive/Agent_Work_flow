"""
pwa_chat.py — PWA AI chat agent (Step 6b — action chips)

Wraps Claude Haiku as a router (NOT an executor). The chat agent
classifies the tech's intent and, when appropriate, returns a
structured "action" dict that the PWA renders as a tappable chip.
The chip's tap handler hits the existing PWA endpoint (e.g.
/pwa/api/job/new) that owns the actual write. The chat agent
itself never calls proposal_agent.run() or any other write path.

Guided estimate intercept (added):
    Before calling Claude, chat() checks whether:
      (a) there is an active estimate_session for this chat session, OR
      (b) the tech's message matches an estimate intent trigger
    If either is true, the message is routed to guided_estimate.handle_input()
    or guided_estimate.start() instead of Claude. The guided flow returns
    the same {reply, action} shape so the chat UI is unaffected.

Return shape:
    {
        "success": True,
        "reply":   "I can draft that estimate for Alice — $325.",
        "action":  {
            "type":     "create_proposal",
            "label":    "Create estimate · $325",
            "params":   { ... endpoint-specific args ... },
            "endpoint": "/pwa/api/job/new",
            "method":   "POST",
        } or None,
        "model":   "haiku" | "guided_flow",
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
    if not text:
        return []
    pattern = re.compile(
        r"\b([A-Z][a-z'\-]+(?:\s+[A-Z][a-z'\-]+){0,2})\b"
    )
    matches = pattern.findall(text)
    out = []
    seen = set()
    for m in matches:
        words = m.split()
        non_block = [w for w in words if w not in _NAME_BLOCKLIST]
        if not non_block:
            continue
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
    candidates = _extract_candidate_names(text)
    if not candidates:
        return []
    found = []
    seen_ids = set()
    for name in candidates[:5]:
        cust = _find_customer(client_id, name)
        if cust and cust.get("id") not in seen_ids:
            seen_ids.add(cust["id"])
            found.append(cust)
        if len(found) >= 3:
            break
    return found


def _build_customer_context(customers: list[dict]) -> str:
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
        f"ACTIONS — always return valid JSON in this exact shape:\n"
        f'{{"reply": "short reply text", "action": {{"type": "ACTION_TYPE", "params": {{...}}}}}}\n'
        f"Or if no action needed:\n"
        f'{{"reply": "short reply text"}}\n\n'
        f"ACTION_TYPE must be one of:\n"
        f"- create_proposal — params: {{\"description\": str, \"customer_name\": str, \"customer_phone\": str, \"customer_address\": str, \"amount\": float}}\n"
        f"- mark_job_done — params: {{\"customer_name\": str}}\n"
        f"- start_job — params: {{\"customer_name\": str}}\n"
        f"- clock_in — params: {{}}\n"
        f"- clock_out — params: {{}}\n\n"
        f"EXAMPLE for 'Send Carol an estimate for riser replacement $750':\n"
        f'{{"reply": "Creating riser replacement estimate for Carol.", "action": {{"type": "create_proposal", "params": {{"description": "riser replacement", "customer_name": "Carol Vigue", "customer_phone": "+15555551937", "customer_address": "72 Tower Rd, Vienna, ME 04360", "amount": 750.0}}}}}}\n\n'
        f"If a customer was matched above, copy their phone/address verbatim into the params.\n"
        f"Never invent names or addresses. No markdown fences. "
        f"If unsure, reply without an action and ask one short clarifying question.\n\n"
        f"Style: short, plain, 1-3 sentences. Use the tech's name occasionally."
    )


def _route_summary_for_employee(client_id: str, employee_id: str) -> str:
    try:
        from execution.dispatch_chain import get_todays_route
        route = get_todays_route(client_id, employee_id)
        return _build_route_summary(route)
    except Exception as e:
        print(f"[{_ts()}] WARN pwa_chat: route summary failed — {e}")
        return "Today's route: unavailable."


MAX_HISTORY_TURNS = 10


def _strip_action_json(content: str) -> str:
    if not content:
        return ""
    s = content.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return content
    try:
        parsed = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return content
    if isinstance(parsed, dict) and isinstance(parsed.get("reply"), str):
        return parsed["reply"]
    return content


def _build_messages(history: list, new_user_message: str) -> list:
    cleaned = []
    for row in history:
        role = row.get("role")
        if role not in ("user", "assistant"):
            continue
        content = row.get("content", "") or ""
        if role == "assistant":
            content = _strip_action_json(content)
        content = content.strip()
        if not content:
            continue
        cleaned.append({"role": role, "content": content})

    cleaned = cleaned[-MAX_HISTORY_TURNS:]

    merged: list[dict] = []
    for row in cleaned:
        if merged and merged[-1]["role"] == row["role"]:
            merged[-1] = {
                "role": row["role"],
                "content": merged[-1]["content"] + "\n\n" + row["content"],
            }
        else:
            merged.append(dict(row))

    while merged and merged[0]["role"] == "assistant":
        merged.pop(0)

    new_text = (new_user_message or "").strip()
    if not new_text:
        return merged
    if merged and merged[-1]["role"] == "user":
        merged[-1] = {
            "role": "user",
            "content": merged[-1]["content"] + "\n\n" + new_text,
        }
    else:
        merged.append({"role": "user", "content": new_text})

    return merged


_ALLOWED_ACTIONS = {
    "create_proposal",
    "mark_job_done",
    "start_job",
    "clock_in",
    "clock_out",
}


def _strip_json_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _parse_claude_response(raw: str) -> dict:
    if not raw:
        return {"reply": "", "action": None}

    cleaned = _strip_json_fences(raw)

    if cleaned.lower().startswith("assistant:"):
        cleaned = cleaned.split(":", 1)[1].strip()

    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
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
    session_id: str = "",
) -> dict:
    """
    Main entry point. Routes to guided_estimate state machine when an
    estimate flow is active or triggered, otherwise calls Claude Haiku.

    Args:
        session_id: The pwa_chat_messages session UUID. Required for the
                    guided estimate flow to find/create the right session.
                    Defaults to empty string for backwards compatibility
                    with existing callers that don't pass it yet.
    """
    if not user_message or not user_message.strip():
        return {"success": False, "reply": "", "action": None, "error": "Empty message"}

    # ------------------------------------------------------------------
    # Guided estimate intercept — runs BEFORE any Claude call.
    #
    # If there is an active estimate session for this chat session, all
    # input goes to the state machine. If the message triggers an
    # estimate intent and no session exists yet, start one.
    #
    # The guided flow returns the same {reply, action, success, ...}
    # shape as the Claude path so the caller (pwa_routes.py) is
    # unaffected.
    # ------------------------------------------------------------------
    if session_id:
        try:
            from execution.guided_estimate import (
                get_active_session,
                handle_input,
                start,
                is_estimate_intent,
            )

            active_session = get_active_session(client_id, employee_id, session_id)

            if active_session:
                print(f"[{_ts()}] INFO pwa_chat: routing to guided_estimate (session active)")
                return handle_input(active_session, user_message, client_id, employee_id)

            if is_estimate_intent(user_message):
                print(f"[{_ts()}] INFO pwa_chat: estimate intent detected — starting guided flow")
                return start(client_id, employee_id, session_id)

        except Exception as e:
            # If the guided flow errors, fall through to Claude rather
            # than breaking the chat entirely. Log it prominently.
            print(f"[{_ts()}] ERROR pwa_chat: guided_estimate intercept failed — {e}")

    # ------------------------------------------------------------------
    # Guided job log intercept — runs BEFORE any Claude call.
    #
    # If there is an active job log session for this chat session, all
    # input goes to the state machine.
    # ------------------------------------------------------------------
    if session_id:
        try:
            from execution.job_log import (
                get_active_session as get_active_log_session,
                handle_input as handle_log_input,
                start as start_log,
                is_job_log_intent,
                check_missed_log,
            )
            active_log = get_active_log_session(client_id, employee_id, session_id)
            if active_log:
                print(f"[{_ts()}] INFO pwa_chat: routing to job_log (session active)")
                return handle_log_input(active_log, user_message, client_id, employee_id)
            if is_job_log_intent(user_message):
                print(f"[{_ts()}] INFO pwa_chat: job log intent detected")
                missed = check_missed_log(client_id, employee_id)
                return start_log(client_id, employee_id, session_id, missed_session=missed)
        except Exception as e:
            print(f"[{_ts()}] ERROR pwa_chat: job_log intercept failed — {e}")

    # ------------------------------------------------------------------
    # Work order intercept — runs BEFORE any Claude call.
    #
    # If there is an active work order session for this chat session,
    # all input goes to the state machine. If the message matches a
    # work order intent trigger and no session exists, start one.
    # ------------------------------------------------------------------
    if session_id:
        try:
            from execution.work_order import (
                get_active_session as get_active_wo_session,
                handle_input as handle_wo_input,
                start as start_wo,
                is_work_order_intent,
            )
            active_wo = get_active_wo_session(client_id, employee_id, session_id)
            if active_wo:
                print(f"[{_ts()}] INFO pwa_chat: routing to work_order (session active)")
                return handle_wo_input(active_wo, user_message, client_id, employee_id)
            if is_work_order_intent(user_message):
                print(f"[{_ts()}] INFO pwa_chat: work order intent detected — starting WO flow")
                return start_wo(client_id, employee_id, session_id)
        except Exception as e:
            print(f"[{_ts()}] ERROR pwa_chat: work_order intercept failed — {e}")

    # ------------------------------------------------------------------
    # Standard Claude path — free-form chat for everything else
    # ------------------------------------------------------------------
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

    char_count = len(system_prompt)
    if char_count > SYSTEM_PROMPT_CHAR_TARGET:
        print(
            f"[{_ts()}] WARN pwa_chat: system prompt is {char_count} chars "
            f"(~{char_count // 4} tokens), exceeds {SYSTEM_PROMPT_TOKEN_TARGET}-token target"
        )

    claude_messages = _build_messages(history, user_message)

    try:
        reply_text = call_claude(
            system_prompt=system_prompt,
            messages=claude_messages,
            model="sonnet",
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

    print(f"[{_ts()}] DEBUG pwa_chat: raw_response={reply_text[:300]}")

    parsed = _parse_claude_response(reply_text)
    reply = parsed["reply"] or "Got it."
    raw_action = parsed["action"]

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
