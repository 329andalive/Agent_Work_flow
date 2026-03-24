"""
db_clarification.py — DB operations for clarification and customer approval flows

Manages pending_clarifications (multi-step intent gathering from field techs)
and customer_approvals (on-site estimate approval with 10-min expiry).

Usage:
    from execution.db_clarification import get_pending, create_pending, ...
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Pending clarifications
# ---------------------------------------------------------------------------

def get_pending(client_id: str, employee_phone: str) -> dict | None:
    """
    Get the most recent non-expired pending clarification for this employee.

    Args:
        client_id:      UUID of the client
        employee_phone: E.164 phone number of the field tech/employee

    Returns:
        Full row as dict, or None if no active clarification found.
    """
    try:
        supabase = get_supabase()
        result = (
            supabase.table("pending_clarifications")
            .select("*")
            .eq("client_id", client_id)
            .eq("employee_phone", employee_phone)
            .gt("expires_at", datetime.now(timezone.utc).isoformat())
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            print(f"[{timestamp()}] INFO db_clarification: Found pending clarification for {employee_phone} stage={result.data[0]['stage']}")
            return result.data[0]
        return None
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_clarification: get_pending failed — {e}")
        return None


def create_pending(
    client_id: str,
    employee_phone: str,
    original_message: str,
    stage: int = 1,
    collected_intent: str = None,
    collected_address: str = None,
    collected_customer_name: str = None,
    collected_scope: str = None,
) -> dict | None:
    """
    Create a new pending clarification record.

    Returns:
        Full inserted row as dict, or None on failure.
    """
    try:
        supabase = get_supabase()
        record = {
            "client_id": client_id,
            "employee_phone": employee_phone,
            "original_message": original_message,
            "stage": stage,
        }
        if collected_intent:
            record["collected_intent"] = collected_intent
        if collected_address:
            record["collected_address"] = collected_address
        if collected_customer_name:
            record["collected_customer_name"] = collected_customer_name
        if collected_scope:
            record["collected_scope"] = collected_scope

        result = supabase.table("pending_clarifications").insert(record).execute()
        if result.data:
            row = result.data[0]
            print(f"[{timestamp()}] INFO db_clarification: Created pending id={row['id']} stage={stage}")
            return row
        return None
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_clarification: create_pending failed — {e}")
        return None


def update_pending(
    pending_id: str,
    stage: int = None,
    collected_intent: str = None,
    collected_address: str = None,
    collected_customer_name: str = None,
    collected_scope: str = None,
) -> dict | None:
    """
    Update a pending clarification. Only non-None fields are set.

    Returns:
        Updated row as dict, or None on failure.
    """
    try:
        supabase = get_supabase()
        update = {}
        if stage is not None:
            update["stage"] = stage
        if collected_intent is not None:
            update["collected_intent"] = collected_intent
        if collected_address is not None:
            update["collected_address"] = collected_address
        if collected_customer_name is not None:
            update["collected_customer_name"] = collected_customer_name
        if collected_scope is not None:
            update["collected_scope"] = collected_scope

        if not update:
            return None

        result = (
            supabase.table("pending_clarifications")
            .update(update)
            .eq("id", pending_id)
            .execute()
        )
        if result.data:
            print(f"[{timestamp()}] INFO db_clarification: Updated pending id={pending_id} → {update}")
            return result.data[0]
        return None
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_clarification: update_pending failed — {e}")
        return None


def delete_pending(pending_id: str) -> bool:
    """Delete a pending clarification (clarification complete)."""
    try:
        supabase = get_supabase()
        supabase.table("pending_clarifications").delete().eq("id", pending_id).execute()
        print(f"[{timestamp()}] INFO db_clarification: Deleted pending id={pending_id}")
        return True
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_clarification: delete_pending failed — {e}")
        return False


def cleanup_expired(client_id: str) -> int:
    """
    Delete all expired clarifications for a client.
    Returns count of deleted rows.
    """
    try:
        supabase = get_supabase()
        now = datetime.now(timezone.utc).isoformat()
        result = (
            supabase.table("pending_clarifications")
            .select("id")
            .eq("client_id", client_id)
            .lt("expires_at", now)
            .execute()
        )
        expired = result.data or []
        for row in expired:
            supabase.table("pending_clarifications").delete().eq("id", row["id"]).execute()
        if expired:
            print(f"[{timestamp()}] INFO db_clarification: Cleaned up {len(expired)} expired clarifications")
        return len(expired)
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_clarification: cleanup_expired failed — {e}")
        return 0


# ---------------------------------------------------------------------------
# Customer approvals
# ---------------------------------------------------------------------------

def create_approval(
    client_id: str,
    customer_id: str,
    job_id: str,
    proposal_id: str,
    tech_phone: str,
    customer_phone: str,
    estimate_amount: float,
) -> dict | None:
    """
    Create a customer approval record for on-site work.

    Returns:
        Full inserted row as dict, or None on failure.
    """
    try:
        supabase = get_supabase()
        record = {
            "client_id": client_id,
            "customer_id": customer_id,
            "job_id": job_id,
            "proposal_id": proposal_id,
            "tech_phone": tech_phone,
            "customer_phone": customer_phone,
            "estimate_amount": estimate_amount,
        }
        result = supabase.table("customer_approvals").insert(record).execute()
        if result.data:
            row = result.data[0]
            print(f"[{timestamp()}] INFO db_clarification: Created approval id={row['id']} amount=${estimate_amount}")
            return row
        return None
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_clarification: create_approval failed — {e}")
        return None


def get_approval_by_job(job_id: str) -> dict | None:
    """Get the most recent pending approval for a job."""
    try:
        supabase = get_supabase()
        result = (
            supabase.table("customer_approvals")
            .select("*")
            .eq("job_id", job_id)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_clarification: get_approval_by_job failed — {e}")
        return None


def get_pending_approval_by_customer(client_id: str, customer_phone: str) -> dict | None:
    """Get a pending approval by customer phone — used when customer replies YES/NO."""
    try:
        supabase = get_supabase()
        result = (
            supabase.table("customer_approvals")
            .select("*")
            .eq("client_id", client_id)
            .eq("customer_phone", customer_phone)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_clarification: get_pending_approval_by_customer failed — {e}")
        return None


def update_approval_status(approval_id: str, status: str, field: str = None) -> bool:
    """
    Update approval status. Optionally stamp followup or approved timestamps.

    Args:
        approval_id: UUID of the approval
        status:      New status (pending/approved/declined/expired)
        field:       Optional: 'followup_1', 'followup_2' to stamp sent_at
    """
    try:
        supabase = get_supabase()
        now = datetime.now(timezone.utc).isoformat()
        update = {"status": status}

        if field == "followup_1":
            update["followup_1_sent_at"] = now
        elif field == "followup_2":
            update["followup_2_sent_at"] = now

        if status == "approved":
            update["approved_at"] = now

        supabase.table("customer_approvals").update(update).eq("id", approval_id).execute()
        print(f"[{timestamp()}] INFO db_clarification: Approval {approval_id} → status={status} field={field}")
        return True
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_clarification: update_approval_status failed — {e}")
        return False


def get_expired_approvals() -> list:
    """Get all pending approvals that have expired (expires_at < now)."""
    try:
        supabase = get_supabase()
        now = datetime.now(timezone.utc).isoformat()
        result = (
            supabase.table("customer_approvals")
            .select("*")
            .eq("status", "pending")
            .lt("expires_at", now)
            .execute()
        )
        approvals = result.data or []
        if approvals:
            print(f"[{timestamp()}] INFO db_clarification: Found {len(approvals)} expired approvals")
        return approvals
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_clarification: get_expired_approvals failed — {e}")
        return []
