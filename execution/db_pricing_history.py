"""
db_pricing_history.py — DB operations for job_pricing_history table.

Writes a row every time a proposal is approved and sent to a customer.
This is the data source for the "last 3 averaged $X" reference in the
guided estimate flow.

HARD RULE: This table is written only by /doc/send — the moment a
tech-approved proposal actually goes to a customer. It is NEVER written
by an AI agent, never written at draft time, and never written from an
estimate session. Only real sent prices become history.

Usage:
    from execution.db_pricing_history import record_sent_proposal
    record_sent_proposal(
        client_id=client_id,
        customer_id=customer_id,
        job_id=job_id,
        proposal_id=proposal_id,
        job_type=job_type,
        amount=total,
        employee_id=employee_id,
    )
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase
from execution.schema import JobPricingHistory as JPH


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def record_sent_proposal(
    client_id: str,
    amount: float,
    job_type: str,
    customer_id: str | None = None,
    job_id: str | None = None,
    proposal_id: str | None = None,
    description: str | None = None,
    employee_id: str | None = None,
) -> bool:
    """
    Write a pricing history row when a proposal is sent to a customer.

    Called from /doc/send in document_routes.py — proposals only, never
    invoices. Non-fatal: if it fails, the send still succeeds.

    Args:
        client_id:   Tenant UUID (required)
        amount:      The actual sent price — tech-entered, never AI
        job_type:    e.g. "pump_out" — used for history lookups
        customer_id: UUID of the customer (for per-customer history)
        job_id:      UUID of the job record
        proposal_id: UUID of the proposal being sent
        description: Optional job description text
        employee_id: UUID of the tech who created the estimate

    Returns:
        True on success, False on failure (non-fatal).
    """
    if not client_id or not amount or amount <= 0:
        print(f"[{_ts()}] WARN db_pricing_history: skipping — missing client_id or invalid amount")
        return False

    if not job_type or not job_type.strip():
        print(f"[{_ts()}] WARN db_pricing_history: skipping — missing job_type")
        return False

    try:
        sb = get_supabase()
        sb.table(JPH.TABLE).insert({
            JPH.CLIENT_ID:    client_id,
            JPH.CUSTOMER_ID:  customer_id,
            JPH.JOB_ID:       job_id,
            JPH.PROPOSAL_ID:  proposal_id,
            JPH.JOB_TYPE:     job_type.strip(),
            JPH.DESCRIPTION:  description,
            JPH.AMOUNT:       float(amount),
            JPH.EMPLOYEE_ID:  employee_id,
            JPH.COMPLETED_AT: datetime.now(timezone.utc).isoformat(),
        }).execute()
        print(
            f"[{_ts()}] INFO db_pricing_history: recorded ${amount:.2f} "
            f"for {job_type} (client={client_id[:8]}, "
            f"customer={customer_id[:8] if customer_id else 'none'})"
        )
        return True
    except Exception as e:
        print(f"[{_ts()}] WARN db_pricing_history: record_sent_proposal failed — {e}")
        return False
