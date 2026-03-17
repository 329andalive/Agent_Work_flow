"""
sms_router.py — Routes inbound SMS to the correct AI agent

Flow:
    1. Receive parsed SMS data from sms_receive.py
    2. Look up the client by phone number via db_client.py
    3. Look up the sender in the employees table — determine role
    4. Priority-check for follow-up response intent (loss_reason, accepted, declined, lost_report)
    5. Scan message body for routing keywords
    6. Apply role-based permission check before dispatching
    7. Dispatch to the correct agent and run it
    8. Return the agent name that handled the message

Priority order for inbound messages:
    1. loss_reason    — owner answering "why did you lose it" (check pending lost_job_why first)
    2. accepted       — customer confirming a proposal
    3. declined       — customer declining a proposal
    4. lost_report    — owner proactively reporting a loss
    5. clock_agent    — field tech / foreman clocking in or out
    6. invoice_agent  — owner reporting a job completion
    7. proposal_agent — new job request (default)

Usage:
    from execution.sms_router import route_message
    agent = route_message(sms_data)
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_client import get_client_by_phone
from execution.db_employee import get_employee_by_phone
from execution.response_detector import detect_response_type
from execution.db_followups import get_pending_followups_by_type


def timestamp():
    """Return a formatted timestamp string for log lines."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Role-based permissions
# "all" grants access to every agent.
# ---------------------------------------------------------------------------
ROLE_PERMISSIONS = {
    "field_tech": ["clock_agent"],
    "foreman":    ["clock_agent", "proposal_agent", "scheduling_agent", "job_list_agent", "noshow_agent"],
    "office":     ["proposal_agent", "invoice_agent", "scheduling_agent", "job_list_agent", "noshow_agent"],
    "owner":      ["all"],
}


def has_permission(role: str, action: str) -> bool:
    """Return True if the role is allowed to invoke the given agent/action."""
    perms = ROLE_PERMISSIONS.get(role, [])
    return "all" in perms or action in perms


# ---------------------------------------------------------------------------
# Keyword routing table — only used when priority detection doesn't match.
# Clock keywords only trigger for field_tech / foreman (enforced in route_message).
# ---------------------------------------------------------------------------
ROUTING_TABLE = {
    # No-show response — foreman/owner responding to a no-show alert
    "noshow_agent": [
        "on it", "on my way", "got it", "handling it",
        "reassign", "re-assign", "find someone", "send someone",
    ],
    # Clock agent — field staff clocking in or out
    "clock_agent": [
        "on site", "clocking in", "clock in", "starting", "arrived",
        "clocking out", "clock out", "headed out",
    ],
    # Invoice agent — owner texting in that a job is done
    "invoice_agent": [
        "all done", "job done", "just finished", "we're done", "just wrapped",
        "wrapped up", "took me", "billed", "bill them", "send invoice",
        "send the invoice", "finished up", "worked", "spent",
        "3 hours", "2 hours", "4 hours", "5 hours", "1 hour", "half a day",
    ],
    # Job list agent — viewing the schedule for a date or range
    # Must appear before scheduling_agent so query phrases win on first match.
    "job_list_agent": [
        "jobs today", "jobs tomorrow", "jobs this week",
        "jobs monday", "jobs tuesday", "jobs wednesday",
        "jobs thursday", "jobs friday", "jobs saturday", "jobs sunday",
        "today's jobs", "tomorrow's jobs",
        "what's on", "what's scheduled",
        "schedule today", "schedule tomorrow", "schedule this week",
        "schedule monday", "schedule tuesday", "schedule wednesday",
        "schedule thursday", "schedule friday", "schedule saturday", "schedule sunday",
        "job list", "show schedule", "pull schedule",
    ],
    # Scheduling agent — booking jobs with a date/time
    "scheduling_agent": [
        "schedule", "book", "set up", "add to the schedule", "put on the schedule",
        "calendar", "appointment",
    ],
    # Proposal agent — new job request keywords
    "proposal_agent": ["quote", "estimate", "proposal", "pricing", "price", "cost", "bid"],
    # Review request
    "review_agent": ["review", "google", "feedback", "rating", "stars", "yelp"],
}

DEFAULT_AGENT = "proposal_agent"


def lookup_client(phone_number: str) -> dict | None:
    """
    Look up a client record by phone number via Supabase.
    Returns the full client record, or None if the number isn't registered.
    """
    return get_client_by_phone(phone_number)


def detect_agent(message_body: str) -> str:
    """
    Keyword-only routing fallback. Used when priority detection doesn't match.
    Falls back to DEFAULT_AGENT if no keywords match.
    """
    body_lower = message_body.lower()

    for agent_name, keywords in ROUTING_TABLE.items():
        for keyword in keywords:
            if keyword in body_lower:
                return agent_name

    return DEFAULT_AGENT


def dispatch(agent_name: str, sms_data: dict, employee: dict = None, role: str = "owner", **kwargs) -> None:
    """
    Call the correct agent with the parsed SMS data.

    Args:
        agent_name: Name of agent to run
        sms_data:   Parsed SMS dict with from_number, to_number, body, message_id
        employee:   Employee dict from db_employee (or None for unknown / owner)
        role:       Sender's role string (default "owner")
        **kwargs:   Extra args passed to specific agents (e.g. response_type)
    """
    from_number = sms_data.get("from_number")
    to_number   = sms_data.get("to_number")
    body        = sms_data.get("body", "")
    name        = employee.get("name", "there") if employee else "there"

    # ------------------------------------------------------------------
    # Permission check — deny access if role can't use this agent
    # ------------------------------------------------------------------
    if not has_permission(role, agent_name):
        msg = (
            f"Hey {name}, I can't process that request with your current access. "
            f"Contact your supervisor."
        )
        print(
            f"[{timestamp()}] WARN sms_router: {name} (role={role}) "
            f"attempted {agent_name} — denied"
        )
        from execution.sms_send import send_sms
        send_sms(to_number=from_number, message_body=msg, from_number=to_number)
        return

    try:
        if agent_name == "clock_agent":
            from execution.clock_agent import handle_clock
            from execution.db_client import get_client_by_phone
            full_client = get_client_by_phone(to_number)
            if full_client:
                handle_clock(
                    client=full_client,
                    employee=employee,
                    raw_input=body,
                    from_number=from_number,
                )
            else:
                print(f"[{timestamp()}] ERROR sms_router: clock_agent — client not found for {to_number}")

        elif agent_name == "noshow_agent":
            from execution.noshow_agent import handle_noshow_response
            from execution.db_client import get_client_by_phone
            full_client = get_client_by_phone(to_number)
            if full_client:
                handle_noshow_response(
                    client=full_client,
                    employee=employee,
                    raw_input=body,
                    from_number=from_number,
                )
            else:
                print(f"[{timestamp()}] ERROR sms_router: noshow_agent — "
                      f"client not found for {to_number}")

        elif agent_name == "scheduling_agent":
            from execution.scheduling_agent import handle_scheduling
            from execution.db_client import get_client_by_phone
            full_client = get_client_by_phone(to_number)
            if full_client:
                handle_scheduling(
                    client=full_client,
                    employee=employee,
                    raw_input=body,
                    from_number=from_number,
                )
            else:
                print(f"[{timestamp()}] ERROR sms_router: scheduling_agent — client not found for {to_number}")

        elif agent_name == "job_list_agent":
            from execution.job_list_agent import handle_job_list
            from execution.db_client import get_client_by_phone
            full_client = get_client_by_phone(to_number)
            if full_client:
                handle_job_list(
                    client=full_client,
                    employee=employee,
                    raw_input=body,
                    from_number=from_number,
                )
            else:
                print(f"[{timestamp()}] ERROR sms_router: job_list_agent — client not found for {to_number}")

        elif agent_name == "proposal_agent":
            from execution.proposal_agent import run as proposal_run
            proposal_run(
                client_phone=to_number,
                customer_phone=from_number,
                raw_input=body,
            )

        elif agent_name == "invoice_agent":
            from execution.invoice_agent import run as invoice_run
            invoice_run(
                client_phone=to_number,
                customer_phone=from_number,
                raw_input=body,
            )

        elif agent_name == "proposal_response":
            from execution.followup_agent import handle_proposal_response
            handle_proposal_response(
                client_phone=to_number,
                customer_phone=from_number,
                response_type=kwargs.get("response_type", "declined"),
            )

        elif agent_name == "loss_reason":
            from execution.followup_agent import handle_loss_reason
            handle_loss_reason(
                client_phone=to_number,
                customer_phone=from_number,
                raw_input=body,
            )

        elif agent_name == "lost_report":
            from execution.followup_agent import handle_lost_report
            handle_lost_report(
                client_phone=to_number,
                owner_phone=from_number,
            )

        elif agent_name == "noshow_response":
            from execution.noshow_agent import handle_noshow_response
            from execution.db_client import get_client_by_phone
            full_client = get_client_by_phone(to_number)
            if full_client:
                handle_noshow_response(
                    client=full_client,
                    employee=employee,
                    raw_input=body,
                    from_number=from_number,
                )
            else:
                print(f"[{timestamp()}] ERROR sms_router: noshow_response — client not found for {to_number}")

        elif agent_name == "review_agent":
            # Stub
            print(f"[{timestamp()}] INFO sms_router: review_agent not yet implemented")

        else:
            print(f"[{timestamp()}] WARN sms_router: Unknown agent '{agent_name}' — no dispatch")

    except Exception as e:
        print(f"[{timestamp()}] ERROR sms_router: dispatch to {agent_name} failed — {e}")


def route_message(sms_data: dict) -> str:
    """
    Main entry point. Resolves client + employee, priority-detects intent,
    applies role permissions, then falls back to keyword routing.

    Args:
        sms_data: dict with keys: from_number, to_number, body, message_id

    Returns:
        Agent name as a string (e.g. "proposal_agent")
    """
    try:
        from_number = sms_data.get("from_number", "unknown")
        to_number   = sms_data.get("to_number", "unknown")
        body        = sms_data.get("body", "")
        message_id  = sms_data.get("message_id", "unknown")

        print(f"[{timestamp()}] INFO sms_router: Routing message_id={message_id} from={from_number}")

        # ------------------------------------------------------------------
        # Step 1: Look up the client by their Telnyx number (to_number)
        # ------------------------------------------------------------------
        client = lookup_client(to_number)
        if not client:
            print(f"[{timestamp()}] WARN sms_router: No client found for Telnyx number {to_number}")
            return DEFAULT_AGENT
        print(f"[{timestamp()}] INFO sms_router: Client resolved → {client['business_name']}")

        client_id = client["id"]

        # ------------------------------------------------------------------
        # Step 2: Resolve sender identity and role from employees table.
        # Fail open — unknown senders are treated as owner so no legitimate
        # message gets silently dropped.
        # ------------------------------------------------------------------
        employee = get_employee_by_phone(client_id, from_number)
        if employee:
            role = employee["role"]
            print(
                f"[{timestamp()}] INFO sms_router: Employee resolved → "
                f"{employee['name']} role={role}"
            )
        else:
            print(
                f"[{timestamp()}] WARN sms_router: Unknown sender {from_number} "
                f"— treating as owner"
            )
            employee = {"name": "Unknown", "role": "owner"}
            role = "owner"

        # ------------------------------------------------------------------
        # Step 3: Priority detection — check intent before keyword routing.
        # These intents bypass keyword routing entirely.
        # ------------------------------------------------------------------
        intent = detect_response_type(body)

        # Priority 1: Owner answering loss reason — check if a lost_job_why is pending
        if intent == "loss_reason":
            pending = get_pending_followups_by_type(client_id, "lost_job_why")
            if pending:
                print(f"[{timestamp()}] INFO sms_router: Routed to → loss_reason (pending lost_job_why found)")
                dispatch("loss_reason", sms_data, employee=employee, role=role)
                return "loss_reason"
            print(f"[{timestamp()}] INFO sms_router: loss_reason detected but no pending why question — falling through")

        # Priority 2: Customer accepting a proposal
        elif intent == "accepted":
            print(f"[{timestamp()}] INFO sms_router: Routed to → proposal_response (accepted)")
            dispatch("proposal_response", sms_data, employee=employee, role=role, response_type="accepted")
            return "proposal_response"

        # Priority 3: Customer declining a proposal
        elif intent == "declined":
            print(f"[{timestamp()}] INFO sms_router: Routed to → proposal_response (declined)")
            dispatch("proposal_response", sms_data, employee=employee, role=role, response_type="declined")
            return "proposal_response"

        # Priority 4: Owner proactively reporting a loss
        elif intent == "lost_report":
            print(f"[{timestamp()}] INFO sms_router: Routed to → lost_report")
            dispatch("lost_report", sms_data, employee=employee, role=role)
            return "lost_report"

        # Priority 5: Foreman / owner responding to a no-show alert
        # Only check if the sender is owner or foreman — field techs don't receive alerts
        if role in ("owner", "foreman"):
            from execution.noshow_agent import _detect_response as _noshow_detect
            from execution.db_connection import get_client as _get_supabase
            noshow_intent = _noshow_detect(body)
            if noshow_intent:
                # Confirm an open alert exists before routing — prevents false positives
                # on messages like "on it" that could mean many things
                _supabase = _get_supabase()
                _alert_check = (
                    _supabase.table("noshow_alerts")
                    .select("id")
                    .eq("client_id", client_id)
                    .eq("status", "open")
                    .limit(1)
                    .execute()
                )
                if _alert_check.data:
                    print(f"[{timestamp()}] INFO sms_router: Routed to → noshow_response ({noshow_intent})")
                    dispatch("noshow_response", sms_data, employee=employee, role=role)
                    return "noshow_response"

        # ------------------------------------------------------------------
        # Step 4: Keyword routing fallback
        # ------------------------------------------------------------------
        agent = detect_agent(body)

        # noshow_agent only fires if there's actually an open alert
        if agent == "noshow_agent":
            from execution.db_noshow import has_open_noshow_alert
            if not has_open_noshow_alert(client_id):
                agent = DEFAULT_AGENT

        print(f"[{timestamp()}] INFO sms_router: Routed to → {agent} (body: '{body[:60]}')")
        dispatch(agent, sms_data, employee=employee, role=role)
        return agent

    except Exception as e:
        print(f"[{timestamp()}] ERROR sms_router: Unexpected error during routing — {e}")
        return DEFAULT_AGENT
