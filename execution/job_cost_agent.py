"""
job_cost_agent.py — Job cost tracking and margin reporting

Runs immediately after every invoice is generated. Pulls the full
costing breakdown, saves it to the job_costs table, and returns a
plain-English summary line for the owner.

This data is PRIVATE to the owner — it never goes to the customer.
The summary line is appended to the bottom of the invoice SMS so the
owner sees the invoice AND their margin in the same message.

Usage:
    from execution.job_cost_agent import calculate
    summary_line = calculate(job_id=job_id, client_id=client_id)
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_jobs import calculate_job_cost
from execution.db_connection import get_client as get_supabase


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _save_job_cost(costing: dict, client_id: str) -> str | None:
    """
    Persist the full job cost breakdown to the job_costs table.

    Uses a two-pass strategy: tries full insert first. If table or
    columns don't exist yet (migration not run), logs the error clearly
    and returns None without crashing the invoice flow.

    Args:
        costing:   Dict returned by db_jobs.calculate_job_cost()
        client_id: UUID of the client (for the FK)

    Returns:
        New job_cost UUID, or None on failure.
    """
    try:
        supabase = get_supabase()
        record = {
            "job_id":           costing.get("job_id"),
            "client_id":        client_id,
            "contract_type":    costing.get("contract_type"),
            "estimated_hours":  costing.get("estimated_hours"),
            "actual_hours":     costing.get("actual_hours"),
            "hour_variance":    costing.get("hour_variance"),
            "estimated_amount": costing.get("estimated_amount"),
            "actual_amount":    costing.get("actual_amount"),
            "amount_variance":  costing.get("amount_variance"),
            "labor_cost":       costing.get("labor_cost"),
            "job_margin":       costing.get("job_margin"),
            "result":           costing.get("result"),
            "summary_line":     costing.get("summary"),
        }
        result = supabase.table("job_costs").insert(record).execute()
        cost_id = result.data[0]["id"]
        print(f"[{timestamp()}] INFO job_cost_agent: Saved job_cost id={cost_id} result={costing.get('result')}")
        return cost_id

    except Exception as e:
        error_str = str(e).lower()
        if "schema" in error_str or "not in schema cache" in error_str or "relation" in error_str:
            print(
                f"[{timestamp()}] WARN job_cost_agent: job_costs table or columns missing — "
                f"run directives/supabase_migration_001.sql in Supabase SQL editor. Error: {e}"
            )
        else:
            print(f"[{timestamp()}] ERROR job_cost_agent: _save_job_cost failed — {e}")
        return None


def calculate(job_id: str, client_id: str) -> str:
    """
    Run the full job cost calculation, save to Supabase, and return
    the plain-English summary line for the owner.

    Called by invoice_agent immediately after saving the invoice.

    Args:
        job_id:    UUID of the completed job
        client_id: UUID of the client (business owner)

    Returns:
        Summary line string (1-2 lines, plain text, owner-only).
        Returns a safe fallback string on any error so invoice_agent
        can still complete its SMS send.
    """
    print(f"[{timestamp()}] INFO job_cost_agent: Calculating job cost for job_id={job_id}")

    try:
        # Step 1: Get the full costing breakdown from db_jobs
        costing = calculate_job_cost(job_id)

        if not costing:
            print(f"[{timestamp()}] WARN job_cost_agent: calculate_job_cost returned None")
            return "Job cost data unavailable — check job record."

        # Step 2: Log the full breakdown for debugging
        print(
            f"[{timestamp()}] INFO job_cost_agent: "
            f"est={costing['estimated_hours']}hrs actual={costing['actual_hours']}hrs "
            f"variance={costing['hour_variance']}hrs | "
            f"est=${costing['estimated_amount']} actual=${costing['actual_amount']} | "
            f"labor_cost=${costing['labor_cost']} margin=${costing['job_margin']} | "
            f"result={costing['result']}"
        )

        # Step 3: Save the full breakdown to job_costs table
        _save_job_cost(costing, client_id)

        # Step 4: Return just the summary line — this goes into the owner SMS
        return costing["summary"]

    except Exception as e:
        print(f"[{timestamp()}] ERROR job_cost_agent: calculate failed — {e}")
        return "Job cost calculation failed — check logs."
