"""
reporting_agent.py — Closing rate and monthly outcome summaries

Reads proposal_outcomes table and produces SMS-ready summary strings.
Called by cron_runner.py on the first of each month and on-demand.

Usage:
    from execution.reporting_agent import get_closing_rate_summary, update_monthly_outcomes
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_lost_jobs import update_monthly_outcomes as _update_monthly_outcomes, get_monthly_outcomes
from execution.db_agent_activity import log_activity


def get_closing_rate_summary(client_id: str, months: int = 1) -> str:
    """
    Return a plain-text closing rate summary for SMS delivery.
    Pulls from the proposal_outcomes table for the current (or most recent) month.

    Args:
        client_id: UUID of the client
        months:    How many months back to look (default 1 = current month)

    Returns:
        Summary string under 300 characters, ready to send as SMS.
    """
    month = datetime.now().strftime("%Y-%m")
    outcomes = get_monthly_outcomes(client_id, month)

    if not outcomes:
        return f"No proposal data yet for {month}. Keep sending those estimates!"

    sent      = outcomes.get("proposals_sent", 0)
    accepted  = outcomes.get("proposals_accepted", 0)
    lost      = (outcomes.get("proposals_declined", 0) or 0) + (outcomes.get("proposals_cold", 0) or 0)
    won_rev   = outcomes.get("revenue_won") or 0
    lost_rev  = outcomes.get("revenue_lost") or 0
    top_reason = outcomes.get("top_lost_reason")

    if sent == 0:
        return f"No proposals sent in {month} yet."

    rate = int((accepted / sent) * 100) if sent > 0 else 0

    lines = [
        f"{month} recap: {accepted}/{sent} quotes won ({rate}%)",
        f"Revenue won: ${won_rev:,.0f} | Lost: ${lost_rev:,.0f}",
    ]

    if top_reason and lost > 0:
        lines.append(f"Top loss reason: {top_reason}")

    summary = " | ".join(lines)

    # Keep under 300 chars
    if len(summary) > 295:
        summary = summary[:292] + "..."

    try:
        log_activity(
            client_phone=client_id,   # no phone in scope — store client_id
            agent_name="reporting_agent",
            action_taken="monthly_report_generated",
            input_summary=month,
            output_summary=summary[:120],
            sms_sent=False,
        )
    except Exception:
        pass
    return summary


def update_monthly_outcomes(client_id: str) -> dict | None:
    """
    Recalculate and upsert the proposal_outcomes row for the current month.
    Thin wrapper around db_lost_jobs.update_monthly_outcomes so callers
    can import from reporting_agent without knowing the DB layer.

    Args:
        client_id: UUID of the client

    Returns:
        Updated outcomes dict, or None on failure.
    """
    return _update_monthly_outcomes(client_id)
