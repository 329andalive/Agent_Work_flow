"""
pwa_jobs.py — PWA-facing job action handlers

Wraps the dispatch_chain primitives so the PWA can perform job actions
(start, done, back, parts, noshow, scope) without going through SMS.

DB writes are identical to the SMS path. The auto-invoice, scope hold,
and customer no-show flows all run the same way. Only the response
shape differs (JSON instead of SMS).

Public API:
    start_job(client_id, employee_id, job_id)
    complete_job(client_id, employee_id, job_id)
    set_status(client_id, employee_id, job_id, command)
    get_route(client_id, employee_id)
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase
from execution.dispatch_chain import (
    get_todays_route,
    get_current_job,
    advance_to_next_job,
    _start_job,
    _end_job,
)


STATUS_MAP = {
    "DONE":   "completed",
    "BACK":   "carry_forward",
    "PARTS":  "parts_pending",
    "NOSHOW": "no_show",
    "SCOPE":  "scope_review",
}

NOTE_MAP = {
    "BACK":   lambda worker, today: f"carry_forward_from={today}",
    "PARTS":  lambda worker, today: f"parts reported by {worker}",
    "NOSHOW": lambda worker, today: f"noshow reported by {worker}",
    "SCOPE":  lambda worker, today: f"Scope change reported by {worker}. Pending owner review.",
}


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _verify_job_belongs_to_route(client_id: str, employee_id: str, job_id: str) -> dict | None:
    """
    Confirm a job is in this employee's route_assignments today.
    Returns the matching job dict from the route, or None.
    Multi-tenant safe — only matches jobs assigned to this employee.
    """
    route = get_todays_route(client_id, employee_id)
    for job in route:
        if job.get("job_id") == job_id:
            return job
    return None


def _get_open_time_entry_id(client_id: str, employee_id: str) -> str | None:
    """Find the employee's most recent open time_entry."""
    try:
        sb = get_supabase()
        result = sb.table("time_entries").select("id").eq(
            "client_id", client_id
        ).eq("employee_id", employee_id).eq(
            "status", "open"
        ).order("clock_in", desc=True).limit(1).execute()
        if result.data:
            return result.data[0]["id"]
    except Exception:
        pass
    return None


def get_route(client_id: str, employee_id: str) -> dict:
    """
    Return today's route + current job state for the PWA route screen.
    """
    route = get_todays_route(client_id, employee_id)
    current = get_current_job(client_id, employee_id)
    completed = sum(1 for j in route if j.get("job_end"))
    return {
        "success": True,
        "route": route,
        "current_job_id": (current or {}).get("job_id"),
        "completed_today": completed,
        "total_jobs": len(route),
    }


def start_job(client_id: str, employee_id: str, job_id: str) -> dict:
    """
    Manually start a specific job (replaces SMS "JOB START [name]").
    Sets job_start, status=in_progress, links current_job to time_entry.
    """
    job = _verify_job_belongs_to_route(client_id, employee_id, job_id)
    if not job:
        return {"success": False, "error": "Job not in your route today"}

    if job.get("job_start") and not job.get("job_end"):
        return {"success": False, "error": "Job is already in progress"}

    if job.get("job_end"):
        return {"success": False, "error": "Job is already completed"}

    te_id = _get_open_time_entry_id(client_id, employee_id)
    started = _start_job(job_id, te_id)

    if not started:
        return {"success": False, "error": "Could not start job"}

    print(f"[{_ts()}] INFO pwa_jobs: Started job {job_id[:8]} for employee {employee_id[:8] if employee_id else ''}")
    return {
        "success": True,
        "job_id": job_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "customer_name": job.get("customer_name", ""),
    }


def complete_job(client_id: str, employee_id: str, job_id: str) -> dict:
    """
    Mark a job done (replaces SMS "DONE [name]"). Also:
      - Auto-creates a draft invoice if estimated_amount > 0
      - Advances to next job in route via advance_to_next_job()
      - Updates dispatch_decisions outcome
    """
    job = _verify_job_belongs_to_route(client_id, employee_id, job_id)
    if not job:
        return {"success": False, "error": "Job not in your route today"}

    if job.get("job_end"):
        return {"success": False, "error": "Job already completed"}

    sb = get_supabase()
    te_id = _get_open_time_entry_id(client_id, employee_id)

    # Look up full job + customer details for invoice + display
    customer_id = None
    estimated_amount = 0.0
    job_type_val = job.get("job_type", "Service")
    customer_name = job.get("customer_name", "Customer")

    try:
        jr = sb.table("jobs").select(
            "customer_id, estimated_amount, job_type"
        ).eq("id", job_id).execute()
        if jr.data:
            customer_id = jr.data[0].get("customer_id")
            estimated_amount = float(jr.data[0].get("estimated_amount") or 0)
            job_type_val = jr.data[0].get("job_type") or job_type_val
    except Exception as e:
        print(f"[{_ts()}] WARN pwa_jobs: job lookup failed — {e}")

    # Auto-invoice if conditions met
    invoice_id = None
    review_url = None
    if customer_id and estimated_amount > 0:
        try:
            invoice_desc = f"{job_type_val} — completed"
            inv_result = sb.table("invoices").insert({
                "client_id": client_id,
                "customer_id": customer_id,
                "job_id": job_id,
                "invoice_text": invoice_desc,
                "amount_due": estimated_amount,
                "status": "draft",
            }).execute()
            if inv_result.data:
                invoice_id = inv_result.data[0]["id"]
                print(f"[{_ts()}] INFO pwa_jobs: Auto-invoice created {invoice_id[:8]} amount=${estimated_amount:.2f}")

                # Look up edit_token to build review URL
                inv_row = sb.table("invoices").select("edit_token").eq("id", invoice_id).execute()
                if inv_row.data and inv_row.data[0].get("edit_token"):
                    base_url = os.environ.get("BOLTS11_BASE_URL", "https://app.bolts11.com")
                    review_url = f"{base_url.rstrip('/')}/doc/review/{inv_row.data[0]['edit_token']}?type=invoice"
        except Exception as e:
            print(f"[{_ts()}] WARN pwa_jobs: auto-invoice failed — {e}")

    # Notify owner of the review link (via notify router)
    if review_url:
        try:
            from execution.notify import notify
            client_check = sb.table("clients").select("owner_mobile, phone").eq("id", client_id).execute()
            if client_check.data:
                owner_phone = client_check.data[0].get("owner_mobile") or client_check.data[0].get("phone", "")
                if owner_phone:
                    notify(
                        client_id=client_id,
                        to_phone=owner_phone,
                        message=f"Invoice ready for {customer_name} — ${estimated_amount:.0f}\nReview & approve: {review_url}",
                        subject=f"Invoice ready for {customer_name} — ${estimated_amount:.0f}",
                        message_type="invoice",
                    )
        except Exception as e:
            print(f"[{_ts()}] WARN pwa_jobs: owner notify failed — {e}")

    # End the job and advance to the next one
    next_job = advance_to_next_job(client_id, employee_id, job_id, te_id)

    # Update dispatch_decisions
    try:
        sb.table("dispatch_decisions").update({
            "outcome_status": "completed",
            "outcome_at": datetime.now(timezone.utc).isoformat(),
        }).eq("job_id", job_id).eq("client_id", client_id).execute()
    except Exception:
        pass

    print(f"[{_ts()}] INFO pwa_jobs: Completed job {job_id[:8]} customer={customer_name}")
    return {
        "success": True,
        "job_id": job_id,
        "customer_name": customer_name,
        "invoice_id": invoice_id,
        "invoice_amount": estimated_amount if invoice_id else None,
        "next_job": next_job,
        "route_complete": next_job is None,
    }


def set_status(client_id: str, employee_id: str, job_id: str, command: str) -> dict:
    """
    Set a job to BACK, PARTS, NOSHOW, or SCOPE status.
    Each handles its own side effects.
    """
    command = command.upper()
    if command not in STATUS_MAP:
        return {"success": False, "error": f"Unknown command: {command}"}
    if command == "DONE":
        # Use complete_job for DONE because it has auto-advance + invoice
        return complete_job(client_id, employee_id, job_id)

    job = _verify_job_belongs_to_route(client_id, employee_id, job_id)
    if not job:
        return {"success": False, "error": "Job not in your route today"}

    sb = get_supabase()
    new_status = STATUS_MAP[command]

    # Resolve worker name for note text
    worker_name = "Worker"
    try:
        emp = sb.table("employees").select("name").eq("id", employee_id).execute()
        if emp.data:
            worker_name = emp.data[0].get("name", "Worker")
    except Exception:
        pass

    today_str = datetime.now(timezone.utc).date().isoformat()
    note = NOTE_MAP[command](worker_name, today_str) if command in NOTE_MAP else None

    update = {
        "status": new_status,
        "dispatch_status": new_status,
    }
    if note:
        update["job_notes"] = note
    if command == "SCOPE":
        update["scope_hold"] = True

    try:
        sb.table("jobs").update(update).eq("id", job_id).eq("client_id", client_id).execute()
    except Exception as e:
        print(f"[{_ts()}] ERROR pwa_jobs: status update failed — {e}")
        return {"success": False, "error": "Could not update job status"}

    # Update dispatch_decisions
    try:
        sb.table("dispatch_decisions").update({
            "outcome_status": new_status,
            "outcome_at": datetime.now(timezone.utc).isoformat(),
        }).eq("job_id", job_id).eq("client_id", client_id).execute()
    except Exception:
        pass

    customer_name = job.get("customer_name", "Customer")

    # Side effects per command
    if command == "NOSHOW":
        _handle_noshow_notification(sb, client_id, job, worker_name)

    if command == "SCOPE":
        _handle_scope_notification(sb, client_id, job_id, worker_name, customer_name)

    print(f"[{_ts()}] INFO pwa_jobs: Set job {job_id[:8]} → {new_status}")
    return {
        "success": True,
        "job_id": job_id,
        "status": new_status,
        "customer_name": customer_name,
    }


def _handle_noshow_notification(sb, client_id: str, job: dict, worker_name: str) -> None:
    """If customer has SMS consent, send a no-show follow-up via notify."""
    try:
        from execution.notify import notify
        customer_phone = job.get("customer_phone", "")
        if not customer_phone:
            return

        # Check consent
        cust = sb.table("customers").select("sms_consent, customer_name").eq(
            "customer_phone", customer_phone
        ).eq("client_id", client_id).execute()
        if not cust.data:
            return
        if not cust.data[0].get("sms_consent"):
            return

        biz = sb.table("clients").select("business_name").eq("id", client_id).execute()
        biz_name = (biz.data[0].get("business_name", "Your service") if biz.data else "Your service")

        notify(
            client_id=client_id,
            to_phone=customer_phone,
            message=f"Hi, {biz_name} arrived for your appointment but no one was available. Please call to reschedule.",
            subject="Missed appointment",
            message_type="no_show_followup",
        )
    except Exception as e:
        print(f"[{_ts()}] WARN pwa_jobs: noshow notify failed — {e}")


def _handle_scope_notification(sb, client_id: str, job_id: str, worker_name: str, customer_name: str) -> None:
    """Notify the owner that a scope change is pending review."""
    try:
        from execution.notify import notify
        client_row = sb.table("clients").select("owner_mobile, phone").eq("id", client_id).execute()
        if not client_row.data:
            return
        owner_phone = client_row.data[0].get("owner_mobile") or client_row.data[0].get("phone", "")
        if not owner_phone:
            return

        base_url = os.environ.get("BOLTS11_BASE_URL", "https://app.bolts11.com")
        review_url = f"{base_url.rstrip('/')}/dashboard/job/{job_id}"

        notify(
            client_id=client_id,
            to_phone=owner_phone,
            message=(
                f"⚠️ Scope change — {customer_name}\n"
                f"{worker_name} flagged a change on site. Review and approve before the invoice sends:\n"
                f"{review_url}"
            ),
            subject=f"Scope change flagged — {customer_name}",
            message_type="scope_review",
        )
    except Exception as e:
        print(f"[{_ts()}] WARN pwa_jobs: scope notify failed — {e}")
