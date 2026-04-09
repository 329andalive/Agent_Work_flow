"""
pwa_chat.py — PWA AI chat agent (Step 6a — text replies only)

Wraps Claude Haiku as a router (NOT an executor). When the tech asks
to do something that creates or modifies data, the agent acknowledges
and tells them an action button will appear in a future build. It
never calls proposal_agent.run() or any other write path directly.

Step 6b will add structured action chips: the agent will return
{reply, action} JSON, the PWA will render the chip, and the chip's
onclick will hit the existing /pwa/api/job/new endpoint that owns
the proposal creation. This file already returns a dict with a
"reply" key so the contract is stable when 6b lands.

System prompt budget: under 500 input tokens, repeated every turn.
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.call_claude import call_claude


# Hard ceiling on system prompt length. The chat is going to fire on
# every user message, so input tokens dominate cost. Stay tight.
SYSTEM_PROMPT_TOKEN_TARGET = 500
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


def _build_system_prompt(employee_name: str, employee_role: str,
                        business_name: str, route_summary: str) -> str:
    """
    Tight system prompt under the 500-token budget.

    Sections in order of importance:
      1. Identity (who is this AI, who is the tech)
      2. Today's context (route summary)
      3. What you can do (actions)
      4. How to respond (format)
    """
    role_label = (employee_role or "field tech").replace("_", " ")

    return (
        f"You are the AI assistant for {business_name}. "
        f"You are texting with {employee_name}, a {role_label} on the road.\n\n"
        f"{route_summary}\n\n"
        f"What you can do today: answer questions about the schedule, "
        f"acknowledge requests to create estimates, invoices, or new "
        f"customers, and explain how things work.\n\n"
        f"You CANNOT yet directly create things — when the tech asks to "
        f"create an estimate or add a customer, acknowledge what they "
        f"want and tell them an action button will appear in the next "
        f"update. Do NOT pretend you created it.\n\n"
        f"Style: short, plain, no corporate filler. Reply in 1-3 sentences "
        f"unless they ask for details. Use the tech's name occasionally."
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
    Main entry point. Send a user message + history to Claude Haiku
    and return the assistant's reply.

    Returns:
        {
            "success": bool,
            "reply": str,
            "model": str,
            "system_prompt_chars": int,  # for monitoring the budget
            "error": str or None,
        }

    Note: This function returns a dict (not a string) so 6b can add
    an "action" key without changing the route signature.
    """
    if not user_message or not user_message.strip():
        return {"success": False, "reply": "", "error": "Empty message"}

    # Build the system prompt with today's context
    route_summary = _route_summary_for_employee(client_id, employee_id)
    system_prompt = _build_system_prompt(
        employee_name=employee_name or "tech",
        employee_role=employee_role or "field tech",
        business_name=business_name or "your business",
        route_summary=route_summary,
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
        # array, but that's a bigger change. For 6a we encode the recent
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
            max_tokens=400,
        )
    except Exception as e:
        print(f"[{_ts()}] ERROR pwa_chat: Claude call failed — {e}")
        return {
            "success": False,
            "reply": "Sorry — I can't reach the AI right now. Try again in a moment.",
            "model": "haiku",
            "system_prompt_chars": char_count,
            "error": str(e),
        }

    if not reply_text or not reply_text.strip():
        return {
            "success": False,
            "reply": "I didn't get an answer back. Try rephrasing?",
            "model": "haiku",
            "system_prompt_chars": char_count,
            "error": "Empty response",
        }

    # Strip any leading "Assistant:" prefix the model might echo back
    cleaned = reply_text.strip()
    if cleaned.lower().startswith("assistant:"):
        cleaned = cleaned.split(":", 1)[1].strip()

    return {
        "success": True,
        "reply": cleaned,
        "model": "haiku",
        "system_prompt_chars": char_count,
        "error": None,
    }
