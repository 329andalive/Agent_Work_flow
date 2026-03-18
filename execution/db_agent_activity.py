"""
db_agent_activity.py — Write to the agent_activity audit log

Every agent calls log_activity() at the end of its run so we have a complete
audit trail: which agent ran, for which client, what it did, and whether
an SMS was sent.

Schema (from CLAUDE.md):
  agent_activity table
    id             uuid (auto)
    client_phone   text
    agent_name     text
    action_taken   text
    input_summary  text
    output_summary text
    sms_sent       boolean
    created_at     timestamp (auto)

Usage:
    from execution.db_agent_activity import log_activity
    log_activity(
        client_phone=client_phone,
        agent_name="proposal_agent",
        action_taken="proposal_generated",
        input_summary=raw_input[:120],
        output_summary=f"proposal_id={proposal_id} amount=${amount}",
        sms_sent=True,
    )
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_activity(
    client_phone: str,
    agent_name: str,
    action_taken: str,
    input_summary: str = "",
    output_summary: str = "",
    sms_sent: bool = False,
) -> None:
    """
    Insert one row into agent_activity.

    Args:
        client_phone:   Client's Telnyx number (or client_id for cron agents
                        that don't have a phone in scope)
        agent_name:     e.g. "proposal_agent", "clock_agent"
        action_taken:   e.g. "proposal_generated", "clock_in"
        input_summary:  Short description of what came in (truncated to 500 chars)
        output_summary: Short description of what was produced (truncated to 500 chars)
        sms_sent:       True if at least one SMS was sent during this agent run

    Returns:
        None — failures are logged but never raised. Logging must never
        break the calling agent.
    """
    try:
        supabase = get_supabase()
        supabase.table("agent_activity").insert({
            "client_phone":  str(client_phone or "")[:50],
            "agent_name":    str(agent_name)[:50],
            "action_taken":  str(action_taken)[:100],
            "input_summary": str(input_summary or "")[:500],
            "output_summary": str(output_summary or "")[:500],
            "sms_sent":      bool(sms_sent),
        }).execute()
        print(
            f"[{_ts()}] INFO db_agent_activity: Logged {agent_name} "
            f"action={action_taken} sms_sent={sms_sent}"
        )
    except Exception as e:
        # Never raise — agent_activity is observability, not core flow
        print(f"[{_ts()}] WARN db_agent_activity: log_activity failed — {e}")
