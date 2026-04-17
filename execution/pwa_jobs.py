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
from datetime import datetime, timezone, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase
from execution.dispatch_chain import (
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


def _get_worker_route(client_id: str, worker_id: str) -> list:
    """
    Direct Supabase query for one worker's dispatched jobs today.

    Reads route_assignments filtered by (client_id, worker_id, dispatch_date),
    then loads the matching jobs + customer rows. Returns dicts with the
    field names the PWA route screen expects (job_id, customer_name, etc.)
    so this is a drop-in replacement for the buggy dispatch_chain helper
    that was filtering by jobs.scheduled_date instead of route_assignments.

    Multi-tenant safe — both queries filter by client_id and the
    assignments query also filters by worker_id, so a forged worker_id
    from another tenant returns nothing.
    """
    today = date.today().isoformat()
    sb = get_supabase()

    try:
        ra = sb.table("route_assignments").select(
            "job_id, sort_order"
        ).eq("client_id", client_id).eq(
            "worker_id", worker_id
        ).eq("dispatch_date", today).order("sort_order").execute()
    except Exception as e:
        print(f"[{_ts()}] ERROR pwa_jobs: route_assignments lookup failed — {e}")
        return []

    if not ra.data:
        return []

    job_ids = [r["job_id"] for r in ra.data]
    sort_map = {r["job_id"]: r.get("sort_order", 0) for r in ra.data}

    try:
        jobs = sb.table("jobs").select(
            "id, job_type, job_description, status, dispatch_status, "
            "estimated_amount, scheduled_date, customer_id, "
            "job_start, job_end"
        ).eq("client_id", client_id).in_("id", job_ids).execute()
    except Exception as e:
        print(f"[{_ts()}] ERROR pwa_jobs: jobs lookup failed — {e}")
        return []

    customer_ids = list({j.get("customer_id") for j in (jobs.data or []) if j.get("customer_id")})
    cust_map = {}
    if customer_ids:
        try:
            custs = sb.table("customers").select(
                "id, customer_name, customer_address, customer_phone"
            ).in_("id", customer_ids).execute()
            cust_map = {c["id"]: c for c in (custs.data or [])}
        except Exception as e:
            print(f"[{_ts()}] WARN pwa_jobs: customer enrich failed — {e}")

    route = []
    for job in (jobs.data or []):
        cust = cust_map.get(job.get("customer_id"), {})
        route.append({
            "job_id": job["id"],
            "sort_order": sort_map.get(job["id"], 0),
            "job_type": job.get("job_type", ""),
            "job_description": job.get("job_description", ""),
            "status": job.get("status", ""),
            "dispatch_status": job.get("dispatch_status", ""),
            "estimated_amount": job.get("estimated_amount"),
            "job_start": job.get("job_start"),
            "job_end": job.get("job_end"),
            "customer_name": cust.get("customer_name", ""),
            "customer_address": cust.get("customer_address", ""),
            "customer_phone": cust.get("customer_phone", ""),
        })
    route.sort(key=lambda j: j["sort_order"])
    return route


def _verify_job_belongs_to_route(client_id: str, employee_id: str, job_id: str) -> dict | None:
    """
    Confirm a job is in this employee's route_assignments today.
    Returns the matching job dict from the route, or None.
    Multi-tenant safe — only matches jobs assigned to this employee.
    """
    route = _get_worker_route(client_id, employee_id)
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
    Uses _get_worker_route() so jobs are filtered by route_assignments
    (worker_id + dispatch_date), NOT by jobs.scheduled_date.
    """
    route = _get_worker_route(client_id, employee_id)
    current = next(
        (j for j in route if j.get("job_start") and not j.get("job_end")),
        None,
    )
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


def get_schedule(client_id: str, employee_id: str, days: int = 5) -> dict:
    """
    Return jobs for the next `days` days (today through today+days-1)
    for this employee, plus carry_forward jobs shown at top of today.

    Returns:
    {
      "success": True,
      "days": [
        {
          "date": "2026-04-13",
          "label": "Today",
          "is_today": True,
          "jobs": [...],
          "carry_forward": [...]   # only present on today
        },
        ...
      ]
    }
    """
    from datetime import timedelta
    from collections import defaultdict
    sb    = get_supabase()
    today = date.today()

    date_range = [today + timedelta(days=i) for i in range(days)]
    date_strs  = [d.isoformat() for d in date_range]

    # Route assignments for this worker across the date window
    try:
        ra = sb.table("route_assignments").select(
            "job_id, sort_order, dispatch_date"
        ).eq("client_id", client_id).eq(
            "worker_id", employee_id
        ).in_("dispatch_date", date_strs).order("sort_order").execute()
    except Exception as e:
        print(f"[{_ts()}] ERROR pwa_jobs: get_schedule ra failed — {e}")
        return {"success": False, "error": str(e)}

    # Group by date — deduplicate so multiple dispatch sessions
    # for the same job don't cause it to appear multiple times.
    date_to_jobs: dict = defaultdict(list)
    date_sort_map: dict = defaultdict(dict)
    _seen: set = set()
    for row in (ra.data or []):
        d   = row["dispatch_date"]
        jid = row["job_id"]
        key = (d, jid)
        if key in _seen:
            continue
        _seen.add(key)
        date_to_jobs[d].append(jid)
        date_sort_map[d][jid] = row.get("sort_order", 0)

    # Fetch all jobs in one query
    all_job_ids = list({jid for jids in date_to_jobs.values() for jid in jids})
    jobs_map: dict = {}
    if all_job_ids:
        try:
            jobs_result = sb.table("jobs").select(
                "id, job_type, job_description, status, dispatch_status, "
                "estimated_amount, scheduled_date, customer_id, job_start, job_end"
            ).eq("client_id", client_id).in_("id", all_job_ids).execute()

            cust_ids = list({j.get("customer_id") for j in (jobs_result.data or []) if j.get("customer_id")})
            cust_map: dict = {}
            if cust_ids:
                custs = sb.table("customers").select(
                    "id, customer_name, customer_address, customer_phone"
                ).in_("id", cust_ids).execute()
                cust_map = {c["id"]: c for c in (custs.data or [])}

            for job in (jobs_result.data or []):
                cust = cust_map.get(job.get("customer_id"), {})
                jobs_map[job["id"]] = {
                    "job_id":           job["id"],
                    "job_type":         job.get("job_type", ""),
                    "job_description":  job.get("job_description", ""),
                    "status":           job.get("status", ""),
                    "dispatch_status":  job.get("dispatch_status", ""),
                    "estimated_amount": job.get("estimated_amount"),
                    "scheduled_date":   job.get("scheduled_date"),
                    "job_start":        job.get("job_start"),
                    "job_end":          job.get("job_end"),
                    "customer_name":    cust.get("customer_name", ""),
                    "customer_address": cust.get("customer_address", ""),
                    "customer_phone":   cust.get("customer_phone", ""),
                }
        except Exception as e:
            print(f"[{_ts()}] ERROR pwa_jobs: get_schedule jobs lookup failed — {e}")

    # Carry-forward jobs — priority queue for today
    carry_forward_jobs: list = []
    try:
        cf = sb.table("jobs").select(
            "id, job_type, job_description, status, dispatch_status, "
            "estimated_amount, scheduled_date, customer_id"
        ).eq("client_id", client_id).eq(
            "assigned_worker_id", employee_id
        ).eq("dispatch_status", "carry_forward").execute()

        cf_cust_ids = list({j.get("customer_id") for j in (cf.data or []) if j.get("customer_id")})
        cf_cust_map: dict = {}
        if cf_cust_ids:
            cf_custs = sb.table("customers").select(
                "id, customer_name, customer_address, customer_phone"
            ).in_("id", cf_cust_ids).execute()
            cf_cust_map = {c["id"]: c for c in (cf_custs.data or [])}

        for job in (cf.data or []):
            cust = cf_cust_map.get(job.get("customer_id"), {})
            carry_forward_jobs.append({
                "job_id":           job["id"],
                "job_type":         job.get("job_type", ""),
                "job_description":  job.get("job_description", ""),
                "status":           job.get("status", ""),
                "dispatch_status":  "carry_forward",
                "estimated_amount": job.get("estimated_amount"),
                "scheduled_date":   job.get("scheduled_date"),
                "job_start":        None,
                "job_end":          None,
                "customer_name":    cust.get("customer_name", ""),
                "customer_address": cust.get("customer_address", ""),
                "customer_phone":   cust.get("customer_phone", ""),
            })
    except Exception as e:
        print(f"[{_ts()}] WARN pwa_jobs: get_schedule carry_forward failed — {e}")

    # Build response
    day_labels    = ["Today", "Tomorrow"]
    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    result_days = []
    for i, (d, d_str) in enumerate(zip(date_range, date_strs)):
        label = day_labels[i] if i < len(day_labels) else weekday_names[d.weekday()] + " " + d.strftime("%b %-d")
        day_job_ids = date_to_jobs.get(d_str, [])
        sort_map    = date_sort_map.get(d_str, {})
        day_jobs    = [jobs_map[jid] for jid in day_job_ids if jid in jobs_map]
        day_jobs.sort(key=lambda j: sort_map.get(j["job_id"], 0))

        day_entry: dict = {
            "date":     d_str,
            "label":    label,
            "is_today": i == 0,
            "jobs":     day_jobs,
        }
        if i == 0:
            day_entry["carry_forward"] = carry_forward_jobs

        result_days.append(day_entry)

    return {"success": True, "days": result_days}


def pull_job_to_today(client_id: str, employee_id: str, job_id: str) -> dict:
    """
    Pull a future scheduled job into today's route.

    Steps:
      1. Find this worker's route_assignment for the job (any future date)
      2. Delete that future assignment
      3. Insert a new route_assignment for today at end of list
      4. Update jobs.scheduled_date = today, status = 'scheduled',
         dispatch_status = 'assigned'

    Job appears at bottom of today's list. Dispatcher can reorder from dashboard.
    """
    sb    = get_supabase()
    today = date.today().isoformat()

    try:
        ra = sb.table("route_assignments").select(
            "id, dispatch_date, sort_order"
        ).eq("client_id", client_id).eq(
            "worker_id", employee_id
        ).eq("job_id", job_id).execute()
    except Exception as e:
        print(f"[{_ts()}] ERROR pwa_jobs: pull_job_to_today ra lookup failed — {e}")
        return {"success": False, "error": str(e)}

    if not ra.data:
        return {"success": False, "error": "Job not found in your schedule"}

    existing_ra = ra.data[0]
    if existing_ra["dispatch_date"] == today:
        return {"success": False, "error": "Job is already on today's route"}

    # Max sort_order for today → append at end
    try:
        today_ra = sb.table("route_assignments").select(
            "sort_order"
        ).eq("client_id", client_id).eq(
            "worker_id", employee_id
        ).eq("dispatch_date", today).order("sort_order", desc=True).limit(1).execute()
        max_sort = (today_ra.data[0]["sort_order"] if today_ra.data else 0) + 10
    except Exception:
        max_sort = 100

    try:
        # Remove future assignment
        sb.table("route_assignments").delete().eq("id", existing_ra["id"]).execute()

        # Insert today's assignment
        sb.table("route_assignments").insert({
            "client_id":     client_id,
            "worker_id":     employee_id,
            "job_id":        job_id,
            "dispatch_date": today,
            "sort_order":    max_sort,
            "status":        "assigned",
        }).execute()

        # Update the job itself
        sb.table("jobs").update({
            "scheduled_date":  today,
            "status":          "scheduled",
            "dispatch_status": "assigned",
        }).eq("id", job_id).eq("client_id", client_id).execute()

        # Get customer name for response
        customer_name = ""
        try:
            jr = sb.table("jobs").select("customer_id").eq("id", job_id).execute()
            if jr.data and jr.data[0].get("customer_id"):
                cr = sb.table("customers").select("customer_name").eq(
                    "id", jr.data[0]["customer_id"]
                ).execute()
                if cr.data:
                    customer_name = cr.data[0].get("customer_name", "")
        except Exception:
            pass

        print(
            f"[{_ts()}] INFO pwa_jobs: Pulled job {job_id[:8]} to today "
            f"from {existing_ra['dispatch_date']} for worker {employee_id[:8]}"
        )
        return {
            "success":       True,
            "job_id":        job_id,
            "customer_name": customer_name,
            "from_date":     existing_ra["dispatch_date"],
        }

    except Exception as e:
        print(f"[{_ts()}] ERROR pwa_jobs: pull_job_to_today write failed — {e}")
        return {"success": False, "error": str(e)}
