"""
dashboard_routes.py — Flask Blueprint for all admin dashboard pages

Blueprint: dashboard_bp
Routes:
    GET /dashboard/                  — Control Board
    GET /dashboard/index.html        — Control Board (alias)
    GET /dashboard/command.html      — Command Center
    GET /command                     — Command Center (short alias)
    GET /dashboard/office.html       — Office / Billing
    GET /dashboard/onboarding.html   — Client Onboarding
    GET /dashboard/book.html         — Customer booking (public, static)
    GET /book                        — Customer booking (public, static)
"""

import os
import sys
from datetime import datetime, timezone, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, render_template, request, redirect, send_from_directory

dashboard_bp = Blueprint("dashboard_bp", __name__)

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_supabase():
    from execution.db_connection import get_client
    return get_client()


def _resolve_client_id():
    """
    Resolve client_id for the current request.

    Dev mode (FLASK_ENV=development): allow ?client_id=XXX query param,
      then fall back to first active client.
    Production: client_id MUST come from session['client_id'].
      If missing, returns None -> caller redirects to /login.

    # TODO: replace with real session auth in Phase 3
    """
    if os.environ.get("FLASK_ENV") == "development":
        qp = request.args.get("client_id")
        if qp:
            return qp
        try:
            sb = _get_supabase()
            result = sb.table("clients").select("id").eq("active", True).order("created_at").limit(1).execute()
            if result.data:
                return result.data[0]["id"]
        except Exception as e:
            print(f"[{_ts()}] ERROR dashboard_routes: _resolve_client_id dev fallback failed — {e}")
        return None

    # Production: session only
    # TODO: replace with real session auth in Phase 3
    from flask import session
    return session.get("client_id")


def _load_client(client_id: str) -> dict:
    """Load the client record. Used for sidebar + query filters."""
    try:
        sb = _get_supabase()
        result = sb.table("clients").select("id, business_name, owner_name, phone, owner_mobile").eq("id", client_id).execute()
        if result.data:
            return result.data[0]
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: _load_client failed — {e}")
    return {"id": client_id, "business_name": "Bolts11", "owner_name": "", "phone": "", "owner_mobile": ""}


def _base_context(active_page: str, client_id: str) -> dict:
    """Common template context for every page."""
    client = _load_client(client_id)
    return {
        "active_page": active_page,
        "client_id": client_id,
        "business_name": client.get("business_name", "Bolts11"),
        "owner_name": client.get("owner_name", ""),
        "current_date": datetime.now().strftime("%a %b %d, %Y"),
        "today": date.today().strftime("%A, %B %-d"),
        "_client": client,
    }


# ---------------------------------------------------------------------------
# GET /dashboard/ — Control Board
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/")
@dashboard_bp.route("/dashboard/index.html")
def control_board():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/dashboard/onboarding.html")

    ctx = _base_context("control", client_id)
    client = ctx["_client"]
    sb = _get_supabase()
    today_str = date.today().isoformat()

    # Jobs scheduled today
    jobs = []
    try:
        result = sb.table("jobs").select(
            "id, job_type, job_description, status, scheduled_date, estimated_amount, actual_amount, customer_id"
        ).eq("client_id", client_id).eq("scheduled_date", today_str).order("created_at").execute()
        jobs = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: jobs query — {e}")

    jobs_complete = len([j for j in jobs if j.get("status") == "completed"])
    jobs_active = len([j for j in jobs if j.get("status") == "in_progress"])

    # Outstanding invoices
    invoice_count = 0
    invoice_total = 0.0
    try:
        result = sb.table("invoices").select("id, amount_due, status").eq("client_id", client_id).in_("status", ["sent", "overdue"]).execute()
        inv_open = result.data or []
        invoice_count = len(inv_open)
        invoice_total = sum(float(i.get("amount_due") or 0) for i in inv_open)
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: invoices query — {e}")

    # SMS count today
    sms_today = 0
    try:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        result = sb.table("messages").select("id").eq("client_id", client_id).gte("created_at", today_start).execute()
        sms_today = len(result.data or [])
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: sms count query — {e}")

    # Employees
    # TODO: add clock_in/clock_out columns to employees table or link via jobs.assigned_employee_id
    employees = []
    try:
        result = sb.table("employees").select("id, name, phone, role, active").eq("client_id", client_id).eq("active", True).execute()
        employees = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: employees query — {e}")

    ctx.update({
        "jobs": jobs,
        "jobs_today_count": len(jobs),
        "jobs_complete": jobs_complete,
        "jobs_active": jobs_active,
        "invoice_count": invoice_count,
        "invoice_total": invoice_total,
        "employees": employees,
        "sms_today": sms_today,
    })
    return render_template("dashboard/control.html", **ctx)


# ---------------------------------------------------------------------------
# GET /dashboard/command.html — Command Center
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/command.html")
@dashboard_bp.route("/command")
def command_center():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/dashboard/onboarding.html")

    ctx = _base_context("command", client_id)
    client = ctx["_client"]
    client_phone = client.get("phone", "")
    sb = _get_supabase()

    # Agent activity
    activity = []
    try:
        result = sb.table("agent_activity").select(
            "id, agent_name, action_taken, input_summary, output_summary, sms_sent, created_at"
        ).eq("client_phone", client_phone).order("created_at", desc=True).limit(50).execute()
        activity = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: command activity — {e}")

    # Pending clarifications
    pending = []
    try:
        result = sb.table("pending_clarifications").select("*").eq("client_id", client_id).execute()
        pending = result.data or []
    except Exception:
        pass  # table may be empty or have different filters

    ctx.update({
        "activity": activity,
        "pending": pending,
        "sms_active": bool(os.environ.get("SMS_10DLC_ACTIVE", "")),
        "client_phone": client_phone,
        "owner_mobile": client.get("owner_mobile", ""),
    })
    return render_template("dashboard/command.html", **ctx)


# ---------------------------------------------------------------------------
# GET /dashboard/office.html — Office / Billing
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/office.html")
def office_billing():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/dashboard/onboarding.html")

    ctx = _base_context("office", client_id)
    sb = _get_supabase()
    ninety_days_ago = (date.today() - timedelta(days=90)).isoformat()

    # Invoices last 90 days
    invoices = []
    try:
        result = sb.table("invoices").select(
            "id, amount_due, status, due_date, sent_at, paid_at, created_at, job_id"
        ).eq("client_id", client_id).gte("created_at", ninety_days_ago).order("created_at", desc=True).execute()
        invoices = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: office invoices — {e}")

    # Proposals last 90 days
    proposals = []
    try:
        result = sb.table("proposals").select(
            "id, amount_estimate, status, sent_at, created_at, response_type, lost_reason"
        ).eq("client_id", client_id).gte("created_at", ninety_days_ago).order("created_at", desc=True).execute()
        proposals = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: office proposals — {e}")

    # Calculated metrics
    total_billed = sum(float(i.get("amount_due") or 0) for i in invoices)
    total_paid = sum(float(i.get("amount_due") or 0) for i in invoices if i.get("status") == "paid")
    total_outstanding = total_billed - total_paid
    proposals_sent = len(proposals)
    proposals_won = len([p for p in proposals if p.get("status") == "accepted"])
    win_rate = round((proposals_won / proposals_sent * 100) if proposals_sent else 0)

    ctx.update({
        "invoices": invoices,
        "proposals": proposals,
        "total_billed": total_billed,
        "total_paid": total_paid,
        "total_outstanding": total_outstanding,
        "proposals_sent": proposals_sent,
        "proposals_won": proposals_won,
        "win_rate": win_rate,
    })
    return render_template("dashboard/office.html", **ctx)


# ---------------------------------------------------------------------------
# GET /dashboard/onboarding.html — Onboarding
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/onboarding.html")
def onboarding():
    client_id = _resolve_client_id()
    if client_id:
        ctx = _base_context("onboarding", client_id)
    else:
        ctx = {
            "active_page": "onboarding",
            "client_id": None,
            "business_name": "Bolts11",
            "owner_name": "",
            "current_date": datetime.now().strftime("%a %b %d, %Y"),
            "today": date.today().strftime("%A, %B %-d"),
        }
    return render_template("dashboard/onboarding.html", **ctx)


# ---------------------------------------------------------------------------
# Public routes — no sidebar, served as static files
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/book.html")
@dashboard_bp.route("/book")
def booking_form():
    dashboard_dir = os.path.join(_project_root, "dashboard")
    return send_from_directory(dashboard_dir, "book.html")
