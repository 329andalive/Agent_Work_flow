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
    GET /dashboard/new-job           — New Job form
    POST /api/jobs/create            — Create job from dashboard
    GET /dashboard/book.html         — Customer booking (public, static)
    GET /book                        — Customer booking (public, static)
"""

import os
import re
import sys
import json
from datetime import datetime, timezone, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, render_template, request, redirect, session, send_from_directory, current_app, flash, abort, jsonify

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

    Dev mode (debug=True or FLASK_ENV=development):
      Allow ?client_id=XXX query param, fall back to first active client.
    Production:
      client_id comes from session['client_id'] (set at /login).
      Returns None if no session → caller redirects to /login.
    """
    if current_app.debug or os.environ.get("FLASK_ENV") == "development":
        qp = request.args.get("client_id")
        if qp:
            return qp
        # Check session first even in dev
        cid = session.get("client_id")
        if cid:
            return cid
        # Dev fallback: first active client
        try:
            sb = _get_supabase()
            result = sb.table("clients").select("id").eq("active", True).order("created_at").limit(1).execute()
            if result.data:
                return result.data[0]["id"]
        except Exception as e:
            print(f"[{_ts()}] ERROR dashboard_routes: _resolve_client_id dev fallback — {e}")
        return None

    # Production: session only
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


def fmt_date(d):
    """Format ISO date as 'March 21, 2026'."""
    if not d:
        return "—"
    try:
        return datetime.fromisoformat(d.replace("Z", "+00:00")).strftime("%B %-d, %Y")
    except Exception:
        return d[:10] if len(d) >= 10 else d


def fmt_phone(p):
    """Format E.164 phone as (207) 555-1234."""
    if not p:
        return "—"
    digits = re.sub(r'\D', '', p)
    if len(digits) == 11:
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return p


def fmt_activity_time(ts):
    """Format timestamp as 'Today 2:32 PM' or 'Mar 19 at 10:45 AM'."""
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - dt
        if delta.days == 0:
            return "Today " + dt.strftime("%-I:%M %p")
        elif delta.days == 1:
            return "Yesterday " + dt.strftime("%-I:%M %p")
        else:
            return dt.strftime("%b %-d at %-I:%M %p")
    except Exception:
        return ts[:16] if len(ts) >= 16 else ts


def fmt_short_date(d):
    """Format ISO date as 'Mar 21'."""
    if not d:
        return "—"
    try:
        return datetime.fromisoformat(d.replace("Z", "+00:00")).strftime("%b %-d")
    except Exception:
        return d[:10] if len(d) >= 10 else d


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
        return redirect("/login")

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
        return redirect("/login")

    ctx = _base_context("command", client_id)
    client = ctx["_client"]
    client_phone = client.get("phone", "")
    sb = _get_supabase()

    # Agent activity — last 5 only for sidebar
    activity = []
    try:
        result = sb.table("agent_activity").select(
            "id, agent_name, action_taken, input_summary, output_summary, sms_sent, created_at"
        ).eq("client_phone", client_phone).order("created_at", desc=True).limit(5).execute()
        activity = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: command activity — {e}")

    ctx.update({
        "activity": activity,
        "sms_active": bool(os.environ.get("SMS_10DLC_ACTIVE", "")),
        "client_phone": client_phone,
        "owner_mobile": client.get("owner_mobile", ""),
        "fmt_activity_time": fmt_activity_time,
    })
    return render_template("dashboard/command.html", **ctx)


# ---------------------------------------------------------------------------
# GET /dashboard/office.html — Office / Billing
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/office.html")
def office_billing():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    ctx = _base_context("office", client_id)
    sb = _get_supabase()
    ninety_days_ago = (date.today() - timedelta(days=90)).isoformat()

    # Invoices last 90 days
    invoices = []
    try:
        result = sb.table("invoices").select(
            "id, amount_due, status, due_date, sent_at, paid_at, created_at, job_id, customer_id, invoice_text"
        ).eq("client_id", client_id).gte("created_at", ninety_days_ago).order("created_at", desc=True).execute()
        invoices = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: office invoices — {e}")

    # Proposals last 90 days
    proposals = []
    try:
        result = sb.table("proposals").select(
            "id, amount_estimate, status, sent_at, created_at, response_type, lost_reason, customer_id"
        ).eq("client_id", client_id).gte("created_at", ninety_days_ago).order("created_at", desc=True).execute()
        proposals = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: office proposals — {e}")

    # Build customer name map
    customer_ids = list(set(
        [i["customer_id"] for i in invoices if i.get("customer_id")] +
        [p["customer_id"] for p in proposals if p.get("customer_id")]
    ))
    cust_map = {}
    if customer_ids:
        try:
            custs = sb.table("customers").select("id, customer_name, customer_phone").in_("id", customer_ids).execute().data or []
            cust_map = {c["id"]: c for c in custs}
        except Exception as e:
            print(f"[{_ts()}] ERROR dashboard_routes: customer map query — {e}")

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
        "cust_map": cust_map,
        "total_billed": total_billed,
        "total_paid": total_paid,
        "total_outstanding": total_outstanding,
        "proposals_sent": proposals_sent,
        "proposals_won": proposals_won,
        "win_rate": win_rate,
        "fmt_short_date": fmt_short_date,
    })
    return render_template("dashboard/office.html", **ctx)


# ---------------------------------------------------------------------------
# GET /dashboard/estimates/ — Estimates (proposals) page
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/estimates/")
def estimates_page():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    ctx = _base_context("estimates", client_id)
    sb = _get_supabase()
    ninety_days_ago = (date.today() - timedelta(days=90)).isoformat()

    proposals = []
    try:
        result = sb.table("proposals").select(
            "id, amount_estimate, status, sent_at, created_at, response_type, lost_reason, customer_id"
        ).eq("client_id", client_id).gte("created_at", ninety_days_ago).order("created_at", desc=True).execute()
        proposals = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: estimates proposals — {e}")

    # Customer name map
    customer_ids = list(set(p["customer_id"] for p in proposals if p.get("customer_id")))
    cust_map = {}
    if customer_ids:
        try:
            custs = sb.table("customers").select("id, customer_name, customer_phone").in_("id", customer_ids).execute().data or []
            cust_map = {c["id"]: c for c in custs}
        except Exception as e:
            print(f"[{_ts()}] ERROR dashboard_routes: estimates cust map — {e}")

    proposals_sent = len(proposals)
    proposals_won = len([p for p in proposals if p.get("status") == "accepted"])
    proposals_outstanding = len([p for p in proposals if p.get("status") in ("sent", "pending")])
    win_rate = round((proposals_won / proposals_sent * 100) if proposals_sent else 0)

    ctx.update({
        "proposals": proposals,
        "cust_map": cust_map,
        "proposals_sent": proposals_sent,
        "proposals_won": proposals_won,
        "proposals_outstanding": proposals_outstanding,
        "win_rate": win_rate,
        "fmt_short_date": fmt_short_date,
    })
    return render_template("dashboard/estimates.html", **ctx)


# ---------------------------------------------------------------------------
# GET /dashboard/estimates/new — New Estimate form
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/estimates/new")
def new_estimate_page():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    ctx = _base_context("estimates", client_id)
    sb = _get_supabase()

    customers_list = []
    try:
        result = sb.table("customers").select(
            "id, customer_name, customer_phone, customer_address"
        ).eq("client_id", client_id).order("customer_name").execute()
        customers_list = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: new estimate customers — {e}")

    jobs_list = []
    try:
        result = sb.table("jobs").select(
            "id, job_type, scheduled_date, customer_id, status"
        ).eq("client_id", client_id).neq("status", "cancelled").order("scheduled_date", desc=True).execute()
        jobs_list = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: new estimate jobs — {e}")

    ctx.update({
        "customers": customers_list,
        "customers_json": json.dumps(customers_list),
        "jobs_json": json.dumps(jobs_list),
    })
    return render_template("dashboard/new_estimate.html", **ctx)


# ---------------------------------------------------------------------------
# POST /api/estimates/create — Create estimate from dashboard
# ---------------------------------------------------------------------------

@dashboard_bp.route("/api/estimates/create", methods=["POST"])
def api_create_estimate():
    client_id = _resolve_client_id()
    if not client_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "Invalid JSON body"}), 400

    customer_id = data.get("customer_id")
    description = (data.get("description") or "").strip()
    amount_estimate = data.get("amount_estimate")
    job_id = data.get("job_id") or None

    if not customer_id:
        return jsonify({"success": False, "error": "Customer is required"}), 400
    if not description:
        return jsonify({"success": False, "error": "Description is required"}), 400
    if amount_estimate is None:
        return jsonify({"success": False, "error": "Estimate amount is required"}), 400

    try:
        amount_estimate = float(amount_estimate)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Amount must be a number"}), 400

    sb = _get_supabase()

    # Verify customer belongs to this client
    try:
        cust = sb.table("customers").select("id").eq("id", customer_id).eq("client_id", client_id).execute()
        if not cust.data:
            return jsonify({"success": False, "error": "Customer not found"}), 404
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: estimate create customer check — {e}")
        return jsonify({"success": False, "error": "Failed to verify customer"}), 500

    # Verify job if provided
    if job_id:
        try:
            job = sb.table("jobs").select("id").eq("id", job_id).eq("client_id", client_id).execute()
            if not job.data:
                return jsonify({"success": False, "error": "Job not found"}), 404
        except Exception as e:
            print(f"[{_ts()}] ERROR dashboard_routes: estimate create job check — {e}")
            return jsonify({"success": False, "error": "Failed to verify job"}), 500

    row = {
        "client_id": client_id,
        "customer_id": customer_id,
        "proposal_text": description,
        "amount_estimate": amount_estimate,
        "status": "draft",
    }
    if job_id:
        row["job_id"] = job_id

    try:
        result = sb.table("proposals").insert(row).execute()
        if not result.data:
            return jsonify({"success": False, "error": "Failed to insert estimate"}), 500
        estimate_id = result.data[0]["id"]
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: estimate create insert — {e}")
        return jsonify({"success": False, "error": f"Database error: {e}"}), 500

    return jsonify({"success": True, "estimate_id": estimate_id, "redirect": "/dashboard/estimates/"})


# ---------------------------------------------------------------------------
# GET /dashboard/invoices/ — Invoices page
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/invoices/")
def invoices_page():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    ctx = _base_context("invoices", client_id)
    sb = _get_supabase()
    ninety_days_ago = (date.today() - timedelta(days=90)).isoformat()

    invoices = []
    try:
        result = sb.table("invoices").select(
            "id, amount_due, status, due_date, sent_at, paid_at, created_at, job_id, customer_id, invoice_text"
        ).eq("client_id", client_id).gte("created_at", ninety_days_ago).order("created_at", desc=True).execute()
        invoices = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: invoices query — {e}")

    customer_ids = list(set(i["customer_id"] for i in invoices if i.get("customer_id")))
    cust_map = {}
    if customer_ids:
        try:
            custs = sb.table("customers").select("id, customer_name, customer_phone").in_("id", customer_ids).execute().data or []
            cust_map = {c["id"]: c for c in custs}
        except Exception as e:
            print(f"[{_ts()}] ERROR dashboard_routes: invoices cust map — {e}")

    total_billed = sum(float(i.get("amount_due") or 0) for i in invoices)
    total_paid = sum(float(i.get("amount_due") or 0) for i in invoices if i.get("status") == "paid")
    total_outstanding = total_billed - total_paid

    ctx.update({
        "invoices": invoices,
        "cust_map": cust_map,
        "total_billed": total_billed,
        "total_paid": total_paid,
        "total_outstanding": total_outstanding,
        "fmt_short_date": fmt_short_date,
    })
    return render_template("dashboard/invoices.html", **ctx)


# ---------------------------------------------------------------------------
# GET /dashboard/invoices/new — New Invoice form
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/invoices/new")
def new_invoice_page():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    ctx = _base_context("invoices", client_id)
    sb = _get_supabase()

    customers_list = []
    try:
        result = sb.table("customers").select(
            "id, customer_name, customer_phone, customer_address"
        ).eq("client_id", client_id).order("customer_name").execute()
        customers_list = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: new invoice customers — {e}")

    jobs_list = []
    try:
        result = sb.table("jobs").select(
            "id, job_type, scheduled_date, customer_id, status"
        ).eq("client_id", client_id).neq("status", "cancelled").order("scheduled_date", desc=True).execute()
        jobs_list = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: new invoice jobs — {e}")

    ctx.update({
        "customers": customers_list,
        "customers_json": json.dumps(customers_list),
        "jobs_json": json.dumps(jobs_list),
    })
    return render_template("dashboard/new_invoice.html", **ctx)


# ---------------------------------------------------------------------------
# POST /api/invoices/create — Create invoice from dashboard
# ---------------------------------------------------------------------------

@dashboard_bp.route("/api/invoices/create", methods=["POST"])
def api_create_invoice():
    client_id = _resolve_client_id()
    if not client_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "Invalid JSON body"}), 400

    customer_id = data.get("customer_id")
    description = (data.get("description") or "").strip()
    amount_due = data.get("amount_due")
    due_date = data.get("due_date") or None
    job_id = data.get("job_id") or None

    if not customer_id:
        return jsonify({"success": False, "error": "Customer is required"}), 400
    if not description:
        return jsonify({"success": False, "error": "Description is required"}), 400
    if amount_due is None:
        return jsonify({"success": False, "error": "Amount is required"}), 400

    try:
        amount_due = float(amount_due)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Amount must be a number"}), 400

    sb = _get_supabase()

    # Verify customer belongs to this client
    try:
        cust = sb.table("customers").select("id").eq("id", customer_id).eq("client_id", client_id).execute()
        if not cust.data:
            return jsonify({"success": False, "error": "Customer not found"}), 404
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: invoice create customer check — {e}")
        return jsonify({"success": False, "error": "Failed to verify customer"}), 500

    # Verify job if provided
    if job_id:
        try:
            job = sb.table("jobs").select("id").eq("id", job_id).eq("client_id", client_id).execute()
            if not job.data:
                return jsonify({"success": False, "error": "Job not found"}), 404
        except Exception as e:
            print(f"[{_ts()}] ERROR dashboard_routes: invoice create job check — {e}")
            return jsonify({"success": False, "error": "Failed to verify job"}), 500

    row = {
        "client_id": client_id,
        "customer_id": customer_id,
        "invoice_text": description,
        "amount_due": amount_due,
        "status": "draft",
    }
    if job_id:
        row["job_id"] = job_id
    if due_date:
        row["due_date"] = due_date

    try:
        result = sb.table("invoices").insert(row).execute()
        if not result.data:
            return jsonify({"success": False, "error": "Failed to insert invoice"}), 500
        invoice_id = result.data[0]["id"]
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: invoice create insert — {e}")
        return jsonify({"success": False, "error": f"Database error: {e}"}), 500

    return jsonify({"success": True, "invoice_id": invoice_id, "redirect": "/dashboard/invoices/"})


# ---------------------------------------------------------------------------
# GET /dashboard/payments/ — Payments page (paid invoices only)
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/payments/")
def payments_page():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    ctx = _base_context("payments", client_id)
    sb = _get_supabase()
    ninety_days_ago = (date.today() - timedelta(days=90)).isoformat()

    payments = []
    try:
        result = sb.table("invoices").select(
            "id, amount_due, status, paid_at, created_at, customer_id"
        ).eq("client_id", client_id).eq("status", "paid").gte("created_at", ninety_days_ago).order("paid_at", desc=True).execute()
        payments = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: payments query — {e}")

    customer_ids = list(set(p["customer_id"] for p in payments if p.get("customer_id")))
    cust_map = {}
    if customer_ids:
        try:
            custs = sb.table("customers").select("id, customer_name, customer_phone").in_("id", customer_ids).execute().data or []
            cust_map = {c["id"]: c for c in custs}
        except Exception as e:
            print(f"[{_ts()}] ERROR dashboard_routes: payments cust map — {e}")

    total_collected = sum(float(p.get("amount_due") or 0) for p in payments)
    payment_count = len(payments)
    avg_payment = round(total_collected / payment_count, 2) if payment_count else 0

    ctx.update({
        "payments": payments,
        "cust_map": cust_map,
        "total_collected": total_collected,
        "payment_count": payment_count,
        "avg_payment": avg_payment,
        "fmt_short_date": fmt_short_date,
        "fmt_date": fmt_date,
    })
    return render_template("dashboard/payments.html", **ctx)


# ---------------------------------------------------------------------------
# GET /dashboard/proposal/<id> — Proposal document view
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/proposal/<proposal_id>")
def proposal_view(proposal_id):
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    ctx = _base_context("office", client_id)
    sb = _get_supabase()

    # Load proposal — must belong to this client
    try:
        result = sb.table("proposals").select("*").eq("id", proposal_id).eq("client_id", client_id).execute()
        if not result.data:
            abort(404)
        proposal = result.data[0]
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: proposal view query — {e}")
        abort(404)

    # Load job
    job = {}
    if proposal.get("job_id"):
        try:
            result = sb.table("jobs").select("*").eq("id", proposal["job_id"]).execute()
            if result.data:
                job = result.data[0]
        except Exception:
            pass

    # Load customer
    customer = {}
    if proposal.get("customer_id"):
        try:
            result = sb.table("customers").select("*").eq("id", proposal["customer_id"]).execute()
            if result.data:
                customer = result.data[0]
        except Exception:
            pass

    # Parse line items
    raw_items = proposal.get("line_items")
    if isinstance(raw_items, str):
        try:
            line_items = json.loads(raw_items)
        except Exception:
            line_items = []
    elif isinstance(raw_items, list):
        line_items = raw_items
    else:
        line_items = []

    # Fallback: show amount as single line if no line items
    if not line_items and proposal.get("amount_estimate"):
        line_items = [{"description": (proposal.get("proposal_text") or "Service")[:60], "amount": float(proposal.get("amount_estimate") or 0)}]

    subtotal = sum(float(li.get("total") or li.get("amount") or 0) for li in line_items)
    tax_rate = float(proposal.get("tax_rate") or 0)
    tax_amount = round(subtotal * tax_rate, 2)
    total = round(subtotal + tax_amount, 2) or float(proposal.get("amount_estimate") or 0)

    ctx.update({
        "proposal": proposal,
        "job": job,
        "customer": customer,
        "line_items": line_items,
        "subtotal": subtotal,
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "total": total,
        "fmt_date": fmt_date,
        "fmt_phone": fmt_phone,
    })
    return render_template("dashboard/proposal_view.html", **ctx)


@dashboard_bp.route("/dashboard/proposal/<proposal_id>/action", methods=["POST"])
def proposal_action(proposal_id):
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    action = request.form.get("action", "")
    sb = _get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    try:
        # Verify ownership
        result = sb.table("proposals").select("id").eq("id", proposal_id).eq("client_id", client_id).execute()
        if not result.data:
            abort(404)

        if action == "accepted":
            sb.table("proposals").update({"status": "accepted", "accepted_at": now}).eq("id", proposal_id).execute()
            flash("Proposal marked as accepted.", "success")
        elif action == "lost":
            sb.table("proposals").update({"status": "declined", "response_type": "declined"}).eq("id", proposal_id).execute()
            flash("Proposal marked as lost.", "info")
        elif action == "send":
            flash("SMS sending queued. Will send when 10DLC is active.", "info")
        else:
            flash("Unknown action.", "error")
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: proposal action — {e}")
        flash("Action failed.", "error")

    return redirect(f"/dashboard/proposal/{proposal_id}")


# ---------------------------------------------------------------------------
# GET /dashboard/invoice/<id> — Invoice document view
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/invoice/<invoice_id>")
def invoice_view(invoice_id):
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    ctx = _base_context("office", client_id)
    sb = _get_supabase()

    # Load invoice — must belong to this client
    try:
        result = sb.table("invoices").select("*").eq("id", invoice_id).eq("client_id", client_id).execute()
        if not result.data:
            abort(404)
        invoice = result.data[0]
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: invoice view query — {e}")
        abort(404)

    # Load job
    job = {}
    if invoice.get("job_id"):
        try:
            result = sb.table("jobs").select("*").eq("id", invoice["job_id"]).execute()
            if result.data:
                job = result.data[0]
        except Exception:
            pass

    # Load customer
    customer = {}
    if invoice.get("customer_id"):
        try:
            result = sb.table("customers").select("*").eq("id", invoice["customer_id"]).execute()
            if result.data:
                customer = result.data[0]
        except Exception:
            pass

    # Parse line items
    raw_items = invoice.get("line_items")
    if isinstance(raw_items, str):
        try:
            line_items = json.loads(raw_items)
        except Exception:
            line_items = []
    elif isinstance(raw_items, list):
        line_items = raw_items
    else:
        line_items = []

    if not line_items and invoice.get("amount_due"):
        line_items = [{"description": (invoice.get("invoice_text") or "Service")[:60], "amount": float(invoice.get("amount_due") or 0)}]

    subtotal = sum(float(li.get("total") or li.get("amount") or 0) for li in line_items)
    tax_rate = float(invoice.get("tax_rate") or 0)
    tax_amount = round(subtotal * tax_rate, 2)
    total = round(subtotal + tax_amount, 2) or float(invoice.get("amount_due") or 0)

    ctx.update({
        "invoice": invoice,
        "job": job,
        "customer": customer,
        "line_items": line_items,
        "subtotal": subtotal,
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "total": total,
        "fmt_date": fmt_date,
        "fmt_phone": fmt_phone,
    })
    return render_template("dashboard/invoice_view.html", **ctx)


@dashboard_bp.route("/dashboard/invoice/<invoice_id>/action", methods=["POST"])
def invoice_action(invoice_id):
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    action = request.form.get("action", "")
    sb = _get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    try:
        result = sb.table("invoices").select("id").eq("id", invoice_id).eq("client_id", client_id).execute()
        if not result.data:
            abort(404)

        if action == "paid":
            sb.table("invoices").update({"status": "paid", "paid_at": now}).eq("id", invoice_id).execute()
            flash("Invoice marked as paid.", "success")
        elif action == "send":
            flash("SMS sending queued. Will send when 10DLC is active.", "info")
        else:
            flash("Unknown action.", "error")
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: invoice action — {e}")
        flash("Action failed.", "error")

    return redirect(f"/dashboard/invoice/{invoice_id}")


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
# GET /dashboard/customers/new — Add Customer form
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/customers/new")
def new_customer():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    ctx = _base_context("control", client_id)
    return render_template("dashboard/new_customer.html", **ctx)


# ---------------------------------------------------------------------------
# POST /api/customers/create — Create customer from dashboard
# ---------------------------------------------------------------------------

def _normalize_phone(raw: str) -> str:
    """Strip non-digits and normalize to E.164 (+1XXXXXXXXXX)."""
    digits = re.sub(r'\D', '', raw)
    if len(digits) == 10:
        digits = '1' + digits
    if len(digits) == 11 and digits[0] == '1':
        return '+' + digits
    # Already has country code or non-US — return as-is with +
    if digits and not digits.startswith('+'):
        return '+' + digits
    return raw.strip()


@dashboard_bp.route("/api/customers/create", methods=["POST"])
def api_create_customer():
    client_id = _resolve_client_id()
    if not client_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "Invalid JSON body"}), 400

    # Hard Rule #1: phone is required
    raw_phone = (data.get("customer_phone") or "").strip()
    if not raw_phone:
        return jsonify({"success": False, "error": "Phone number is required (Hard Rule #1)"}), 400

    phone = _normalize_phone(raw_phone)
    customer_name = (data.get("customer_name") or "").strip()
    customer_email = (data.get("customer_email") or "").strip()
    customer_address = (data.get("customer_address") or "").strip()
    notes = (data.get("notes") or "").strip()

    sb = _get_supabase()

    # Duplicate check: same phone under same client
    try:
        dup = sb.table("customers").select("id").eq("client_id", client_id).eq("customer_phone", phone).execute()
        if dup.data:
            return jsonify({"success": False, "error": "A customer with this phone number already exists"}), 409
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: customer dup check — {e}")
        return jsonify({"success": False, "error": "Failed to check for duplicates"}), 500

    # Insert — Hard Rule #2: sms_consent always false
    row = {
        "client_id": client_id,
        "customer_phone": phone,
        "customer_name": customer_name or None,
        "customer_email": customer_email or None,
        "customer_address": customer_address or None,
        "notes": notes or None,
        "sms_consent": False,
    }

    try:
        result = sb.table("customers").insert(row).execute()
        if not result.data:
            return jsonify({"success": False, "error": "Failed to insert customer"}), 500
        new_cust = result.data[0]
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: customer insert — {e}")
        return jsonify({"success": False, "error": f"Database error: {e}"}), 500

    return jsonify({
        "success": True,
        "customer_id": new_cust["id"],
        "customer_name": new_cust.get("customer_name", ""),
        "customer_address": new_cust.get("customer_address", ""),
    })


# ---------------------------------------------------------------------------
# GET /dashboard/new-job — New Job form
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/new-job")
def new_job():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    ctx = _base_context("control", client_id)
    sb = _get_supabase()

    # Load customers for the select dropdown
    customers = []
    try:
        result = sb.table("customers").select(
            "id, customer_name, customer_phone, customer_address"
        ).eq("client_id", client_id).order("customer_name").execute()
        customers = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: new-job customers query — {e}")

    ctx.update({
        "customers": customers,
        "customers_json": json.dumps(customers),
    })
    return render_template("dashboard/new_job.html", **ctx)


# ---------------------------------------------------------------------------
# POST /api/jobs/create — Create job from dashboard
# ---------------------------------------------------------------------------

@dashboard_bp.route("/api/jobs/create", methods=["POST"])
def api_create_job():
    client_id = _resolve_client_id()
    if not client_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "Invalid JSON body"}), 400

    # Validate required fields
    customer_id = data.get("customer_id")
    job_type = data.get("job_type")
    scheduled_date = data.get("scheduled_date")

    if not customer_id:
        return jsonify({"success": False, "error": "Customer is required"}), 400
    if not job_type:
        return jsonify({"success": False, "error": "Job type is required"}), 400
    if not scheduled_date:
        return jsonify({"success": False, "error": "Scheduled date is required"}), 400

    # Build scheduled_date with optional time
    scheduled_time = data.get("scheduled_time", "")
    if scheduled_time:
        scheduled_dt = f"{scheduled_date}T{scheduled_time}:00"
    else:
        scheduled_dt = scheduled_date

    notes = data.get("notes", "").strip()
    generate_proposal = data.get("generate_proposal", False)

    sb = _get_supabase()

    # Verify customer belongs to this client (multi-tenancy)
    try:
        cust_result = sb.table("customers").select("id, customer_name, customer_phone, customer_address").eq("id", customer_id).eq("client_id", client_id).execute()
        if not cust_result.data:
            return jsonify({"success": False, "error": "Customer not found"}), 404
        customer = cust_result.data[0]
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: create job customer check — {e}")
        return jsonify({"success": False, "error": "Failed to verify customer"}), 500

    # Insert job
    # Pricing fields from the New Job form
    estimated_amount = data.get("estimated_amount")
    estimated_hours = data.get("estimated_hours")
    contract_type = data.get("contract_type")

    job_row = {
        "client_id": client_id,
        "customer_id": customer_id,
        "job_type": job_type,
        "status": "scheduled",
        "dispatch_status": "unassigned",
        "scheduled_date": scheduled_dt,
        "job_notes": notes,
        "raw_input": "Created via dashboard New Job form",
    }

    # Add pricing if provided
    if estimated_amount is not None:
        try:
            job_row["estimated_amount"] = float(estimated_amount)
        except (ValueError, TypeError):
            pass
    if estimated_hours is not None:
        try:
            job_row["estimated_hours"] = float(estimated_hours)
        except (ValueError, TypeError):
            pass
    if contract_type:
        job_row["contract_type"] = contract_type

    # Include address if provided
    address = data.get("address", "").strip()
    if address:
        job_row["job_description"] = address

    try:
        result = sb.table("jobs").insert(job_row).execute()
        if not result.data:
            return jsonify({"success": False, "error": "Failed to insert job"}), 500
        job_id = result.data[0]["id"]
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: create job insert — {e}")
        return jsonify({"success": False, "error": f"Database error: {e}"}), 500

    # Optionally trigger proposal_agent — synchronous, matching command_routes pattern
    proposal_id = None
    proposal_summary = None
    proposal_error = None

    if generate_proposal:
        client = _load_client(client_id)
        client_phone = client.get("phone", "")
        customer_phone = customer.get("customer_phone", "")
        customer_name = customer.get("customer_name", "Customer")

        # Build natural language description — same pattern as command_routes.py
        raw_input_text = f"{job_type} job for {customer_name} at {address or 'address on file'}. Notes: {notes}" if notes else f"{job_type} job for {customer_name} at {address or 'address on file'}"

        try:
            from execution.proposal_agent import run as proposal_run
            output = proposal_run(client_phone=client_phone, customer_phone=customer_phone, raw_input=raw_input_text)

            if output:
                # Fetch the proposal_id from the most recent proposal for this job
                try:
                    prop_result = sb.table("proposals").select("id, amount_estimate").eq("client_id", client_id).eq("customer_id", customer_id).order("created_at", desc=True).limit(1).execute()
                    if prop_result.data:
                        proposal_id = prop_result.data[0]["id"]
                        amount = prop_result.data[0].get("amount_estimate", 0)
                        proposal_summary = f"Proposal for {customer_name} — ${float(amount):.0f}"
                except Exception as e:
                    print(f"[{_ts()}] WARN dashboard_routes: could not fetch proposal_id — {e}")
                    proposal_summary = "Proposal drafted successfully."

                print(f"[{_ts()}] INFO dashboard_routes: proposal_agent completed for job {job_id}")
            else:
                proposal_error = "Proposal agent returned no output — try from Command Center."
                print(f"[{_ts()}] WARN dashboard_routes: proposal_agent returned None for job {job_id}")

        except Exception as e:
            proposal_error = f"Proposal generation failed: {e}"
            print(f"[{_ts()}] ERROR dashboard_routes: proposal_agent failed for job {job_id} — {e}")

        # Log to agent_activity — success or failure
        try:
            from execution.db_agent_activity import log_activity
            log_activity(
                client_phone=client_phone,
                agent_name="proposal_agent",
                action_taken="proposal_from_new_job",
                input_summary=raw_input_text[:120],
                output_summary=(proposal_summary or proposal_error or "unknown")[:120],
                sms_sent=False,
            )
        except Exception:
            pass

    return jsonify({
        "success": True,
        "job_id": job_id,
        "proposal_id": proposal_id,
        "proposal_summary": proposal_summary,
        "proposal_error": proposal_error,
        "redirect": "/dashboard/",
    })


# ---------------------------------------------------------------------------
# GET /dashboard/job/<id> — Job detail page
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/job/<job_id>")
def job_detail(job_id):
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    ctx = _base_context("control", client_id)
    sb = _get_supabase()

    # Load job — must belong to this client
    try:
        result = sb.table("jobs").select("*").eq("id", job_id).eq("client_id", client_id).execute()
        if not result.data:
            abort(404)
        job = result.data[0]
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: job_detail query — {e}")
        abort(404)

    # Load customer
    customer = {}
    if job.get("customer_id"):
        try:
            result = sb.table("customers").select("*").eq("id", job["customer_id"]).eq("client_id", client_id).execute()
            if result.data:
                customer = result.data[0]
        except Exception:
            pass

    # Load proposals linked to this job
    proposals = []
    try:
        proposals = sb.table("proposals").select(
            "id, status, amount_estimate, created_at"
        ).eq("client_id", client_id).eq("job_id", job_id).order("created_at", desc=True).execute().data or []
    except Exception as e:
        print(f"[{_ts()}] WARN dashboard_routes: job_detail proposals — {e}")

    # Load invoices linked to this job
    invoices = []
    try:
        invoices = sb.table("invoices").select(
            "id, status, amount_due, paid_at, created_at"
        ).eq("client_id", client_id).eq("job_id", job_id).order("created_at", desc=True).execute().data or []
    except Exception as e:
        print(f"[{_ts()}] WARN dashboard_routes: job_detail invoices — {e}")

    # Load agent activity for this job
    client_phone = ctx["_client"].get("phone", "")
    activity = []
    try:
        activity = sb.table("agent_activity").select(
            "agent_name, action_taken, output_summary, created_at"
        ).eq("client_phone", client_phone).ilike("input_summary", f"%{job_id[:8]}%").order("created_at", desc=True).limit(10).execute().data or []
    except Exception:
        pass

    ctx.update({
        "job": job,
        "customer": customer,
        "proposals": proposals,
        "invoices": invoices,
        "activity": activity,
        "fmt_date": fmt_date,
        "fmt_phone": fmt_phone,
        "fmt_short_date": fmt_short_date,
        "fmt_activity_time": fmt_activity_time,
    })
    return render_template("dashboard/job_detail.html", **ctx)


# ---------------------------------------------------------------------------
# GET /dashboard/customers/<id> — Customer detail page
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/customers/<customer_id>")
def customer_detail(customer_id):
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    ctx = _base_context("customers", client_id)
    sb = _get_supabase()

    # Load customer — must belong to this client
    try:
        result = sb.table("customers").select("*").eq("id", customer_id).eq("client_id", client_id).execute()
        if not result.data:
            abort(404)
        customer = result.data[0]
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: customer_detail query — {e}")
        abort(404)

    # Load jobs for this customer
    jobs = []
    try:
        jobs = sb.table("jobs").select(
            "id, job_type, status, scheduled_date, job_description"
        ).eq("client_id", client_id).eq("customer_id", customer_id).order("scheduled_date", desc=True).execute().data or []
    except Exception as e:
        print(f"[{_ts()}] WARN dashboard_routes: customer_detail jobs — {e}")

    # Load proposals
    proposals = []
    try:
        proposals = sb.table("proposals").select(
            "id, status, amount_estimate, created_at"
        ).eq("client_id", client_id).eq("customer_id", customer_id).order("created_at", desc=True).limit(10).execute().data or []
    except Exception as e:
        print(f"[{_ts()}] WARN dashboard_routes: customer_detail proposals — {e}")

    # Load invoices
    invoices = []
    try:
        invoices = sb.table("invoices").select(
            "id, status, amount_due, paid_at, created_at"
        ).eq("client_id", client_id).eq("customer_id", customer_id).order("created_at", desc=True).limit(10).execute().data or []
    except Exception as e:
        print(f"[{_ts()}] WARN dashboard_routes: customer_detail invoices — {e}")

    ctx.update({
        "customer": customer,
        "jobs": jobs,
        "proposals": proposals,
        "invoices": invoices,
        "fmt_date": fmt_date,
        "fmt_phone": fmt_phone,
        "fmt_short_date": fmt_short_date,
    })
    return render_template("dashboard/customer_detail.html", **ctx)


# ---------------------------------------------------------------------------
# GET /dashboard/dispatch — Dispatch Board
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/dispatch")
def dispatch_board():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    ctx = _base_context("dispatch", client_id)
    sb = _get_supabase()

    # Load scheduling data via Option A (unified jobs table)
    from execution.db_scheduling import (
        get_todays_jobs, get_workers, get_carry_forward_jobs, get_held_jobs,
    )

    # Support date selector via ?date=YYYY-MM-DD query param
    selected_date = request.args.get("date", date.today().isoformat())
    # Validate date format
    try:
        date.fromisoformat(selected_date)
    except (ValueError, TypeError):
        selected_date = date.today().isoformat()

    jobs = get_todays_jobs(client_id, selected_date)
    workers = get_workers(client_id)
    carry_forward = get_carry_forward_jobs(client_id)
    held = get_held_jobs(client_id)

    # Enrich jobs with customer names
    all_jobs = jobs + carry_forward + held
    cust_ids = list(set(j.get("customer_id") for j in all_jobs if j.get("customer_id")))
    cust_map = {}
    if cust_ids:
        try:
            custs = sb.table("customers").select("id, customer_name, customer_phone, customer_address").in_("id", cust_ids).execute().data or []
            cust_map = {c["id"]: c for c in custs}
        except Exception as e:
            print(f"[{_ts()}] WARN dashboard_routes: dispatch customer map — {e}")

    for j in all_jobs:
        cid = j.get("customer_id")
        if cid and cid in cust_map:
            j["customer_name"] = cust_map[cid].get("customer_name", "")
            j["customer_phone"] = cust_map[cid].get("customer_phone", "")
            j["address"] = cust_map[cid].get("customer_address", "") or j.get("job_description", "")
        else:
            j["customer_name"] = ""
            j["customer_phone"] = ""
            j["address"] = j.get("job_description", "")

    # Phase 2 AI suggestions (returns [] if < 30 sessions)
    suggestions = []
    try:
        from execution.dispatch_suggestion import get_suggestions
        suggestions = get_suggestions(client_id, jobs, workers)
    except Exception as e:
        print(f"[{_ts()}] WARN dashboard_routes: dispatch suggestions — {e}")

    # Load existing assignments for this date from route_assignments
    # This restores board state after page reload or Send Routes
    assignments = {}  # {job_id: worker_id}
    try:
        asgn_rows = sb.table("route_assignments").select(
            "job_id, worker_id, sort_order"
        ).eq("client_id", client_id).execute()
        for row in (asgn_rows.data or []):
            if row.get("job_id") and row.get("worker_id"):
                assignments[row["job_id"]] = row["worker_id"]
    except Exception as e:
        print(f"[{_ts()}] WARN dashboard_routes: dispatch assignments query — {e}")

    ctx.update({
        "jobs": jobs,
        "workers": workers,
        "carry_forward": carry_forward,
        "held": held,
        "suggestions": suggestions,
        "dispatch_date": selected_date,
        "today_iso": date.today().isoformat(),
        "jobs_json": json.dumps(jobs, default=str),
        "carry_forward_json": json.dumps(carry_forward, default=str),
        "workers_json": json.dumps(workers, default=str),
        "assignments_json": json.dumps(assignments),
    })
    return render_template("dashboard/dispatch.html", **ctx)


# ---------------------------------------------------------------------------
# GET /dashboard/classes — Class/Slot management
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/classes")
def classes_page():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    ctx = _base_context("classes", client_id)
    sb = _get_supabase()
    client = ctx["_client"]
    client_phone = client.get("phone", "")
    today_str = date.today().isoformat()
    future_14 = (date.today() + timedelta(days=14)).isoformat()
    past_7 = (date.today() - timedelta(days=7)).isoformat()

    # Load class board for this client
    board = None
    try:
        result = sb.table("class_boards").select("*").eq("client_phone", client_phone).limit(1).execute()
        if result.data:
            board = result.data[0]
    except Exception as e:
        print(f"[{_ts()}] WARN dashboard_routes: class_boards query — {e}")

    # Load upcoming slots (next 14 days)
    upcoming_slots = []
    try:
        result = sb.table("class_slots").select(
            "id, title, slot_date, start_time, end_time, capacity, enrolled_count, "
            "instructor, description, status"
        ).eq("client_phone", client_phone).gte("slot_date", today_str).lte(
            "slot_date", future_14
        ).neq("status", "cancelled").order("slot_date").order("start_time").execute()
        upcoming_slots = result.data or []
    except Exception as e:
        print(f"[{_ts()}] WARN dashboard_routes: upcoming slots query — {e}")

    # Load past slots (last 7 days)
    past_slots = []
    try:
        result = sb.table("class_slots").select(
            "id, title, slot_date, start_time, end_time, capacity, enrolled_count, "
            "instructor, description, status"
        ).eq("client_phone", client_phone).lt("slot_date", today_str).gte(
            "slot_date", past_7
        ).order("slot_date", desc=True).order("start_time").execute()
        past_slots = result.data or []
    except Exception as e:
        print(f"[{_ts()}] WARN dashboard_routes: past slots query — {e}")

    # Load enrollments for upcoming slots
    slot_ids = [s["id"] for s in upcoming_slots]
    enrollments = {}
    if slot_ids:
        try:
            for sid in slot_ids[:50]:
                enr = sb.table("class_enrollments").select(
                    "customer_name, customer_phone"
                ).eq("slot_id", sid).eq("status", "enrolled").execute()
                enrollments[sid] = enr.data or []
        except Exception as e:
            print(f"[{_ts()}] WARN dashboard_routes: enrollments query — {e}")

    # Group slots by date
    from collections import defaultdict
    upcoming_by_date = defaultdict(list)
    for s in upcoming_slots:
        upcoming_by_date[s.get("slot_date", "")].append(s)

    past_by_date = defaultdict(list)
    for s in past_slots:
        past_by_date[s.get("slot_date", "")].append(s)

    base_url = os.environ.get("BOLTS11_BASE_URL", "https://bolts11.com")
    board_token = board.get("token", "") if board else ""
    booking_url = f"{base_url}/book/{board_token}" if board_token else ""

    ctx.update({
        "board": board,
        "upcoming_slots": upcoming_slots,
        "past_slots": past_slots,
        "upcoming_by_date": dict(upcoming_by_date),
        "past_by_date": dict(past_by_date),
        "enrollments": enrollments,
        "booking_url": booking_url,
        "slots_json": json.dumps(upcoming_slots, default=str),
        "fmt_short_date": fmt_short_date,
    })
    return render_template("dashboard/classes.html", **ctx)


# ---------------------------------------------------------------------------
# POST /api/slots/create — Create a new class slot
# ---------------------------------------------------------------------------

@dashboard_bp.route("/api/slots/create", methods=["POST"])
def api_create_slot():
    client_id = _resolve_client_id()
    if not client_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "Invalid JSON body"}), 400

    title = (data.get("title") or "").strip()
    slot_date = data.get("slot_date")
    start_time = data.get("start_time")

    if not title:
        return jsonify({"success": False, "error": "Title is required"}), 400
    if not slot_date:
        return jsonify({"success": False, "error": "Date is required"}), 400
    if not start_time:
        return jsonify({"success": False, "error": "Start time is required"}), 400

    client = _load_client(client_id)
    client_phone = client.get("phone", "")

    row = {
        "client_phone": client_phone,
        "title": title,
        "slot_date": slot_date,
        "start_time": start_time,
        "end_time": data.get("end_time") or None,
        "capacity": int(data.get("capacity", 10)),
        "enrolled_count": 0,
        "instructor": (data.get("instructor") or "").strip() or None,
        "description": (data.get("description") or "").strip() or None,
        "status": "open",
    }

    try:
        sb = _get_supabase()
        result = sb.table("class_slots").insert(row).execute()
        if not result.data:
            return jsonify({"success": False, "error": "Insert failed"}), 500
        slot_id = result.data[0]["id"]
        print(f"[{_ts()}] INFO dashboard_routes: Created slot {slot_id[:8]} — {title} on {slot_date}")
        return jsonify({"success": True, "slot_id": slot_id})
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: slot create failed — {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# POST /api/slots/cancel — Cancel a class slot
# ---------------------------------------------------------------------------

@dashboard_bp.route("/api/slots/cancel", methods=["POST"])
def api_cancel_slot():
    client_id = _resolve_client_id()
    if not client_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    data = request.get_json(silent=True)
    slot_id = (data or {}).get("slot_id")
    if not slot_id:
        return jsonify({"success": False, "error": "slot_id required"}), 400

    client = _load_client(client_id)
    client_phone = client.get("phone", "")

    try:
        sb = _get_supabase()
        # Verify slot belongs to this client
        check = sb.table("class_slots").select("id").eq("id", slot_id).eq("client_phone", client_phone).execute()
        if not check.data:
            return jsonify({"success": False, "error": "Slot not found"}), 404

        sb.table("class_slots").update({"status": "cancelled"}).eq("id", slot_id).execute()
        print(f"[{_ts()}] INFO dashboard_routes: Cancelled slot {slot_id[:8]}")
        return jsonify({"success": True})
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: slot cancel failed — {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# GET /dashboard/schedule — Appointment schedule (vertical timeline)
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/schedule")
def schedule_page():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    ctx = _base_context("schedule", client_id)
    sb = _get_supabase()
    client = ctx["_client"]
    client_phone = client.get("phone", "")
    today_d = date.today()

    # Load appointment board
    board = None
    try:
        result = sb.table("class_boards").select("*").eq(
            "client_phone", client_phone
        ).eq("board_type", "appointment").limit(1).execute()
        if result.data:
            board = result.data[0]
    except Exception as e:
        print(f"[{_ts()}] WARN dashboard_routes: appointment board query — {e}")

    # Parse settings
    settings = {}
    if board and board.get("settings_json"):
        raw = board["settings_json"]
        if isinstance(raw, str):
            try:
                settings = json.loads(raw)
            except Exception:
                settings = {}
        elif isinstance(raw, dict):
            settings = raw

    slot_duration = settings.get("slot_duration_minutes", 25)

    # Load slots for next 2 weeks
    end_date = (today_d + timedelta(days=14)).isoformat()
    slots = []
    try:
        result = sb.table("class_slots").select(
            "id, title, slot_date, start_time, end_time, capacity, "
            "enrolled_count, status, description"
        ).eq("client_phone", client_phone).eq(
            "board_type", "appointment"
        ).gte(
            "slot_date", today_d.isoformat()
        ).lte("slot_date", end_date).order("slot_date").order("start_time").execute()
        slots = result.data or []
    except Exception as e:
        print(f"[{_ts()}] WARN dashboard_routes: schedule slots query — {e}")

    # Load enrollments for booked slots
    slot_ids = [s["id"] for s in slots if (s.get("enrolled_count") or 0) > 0]
    enrollments = {}
    if slot_ids:
        try:
            for sid in slot_ids[:100]:
                enr = sb.table("class_enrollments").select(
                    "customer_name, customer_phone, customer_id"
                ).eq("slot_id", sid).eq("status", "enrolled").execute()
                enrollments[sid] = enr.data or []
        except Exception as e:
            print(f"[{_ts()}] WARN dashboard_routes: schedule enrollments query — {e}")

    # Group by date
    from collections import defaultdict
    week_slots = defaultdict(list)
    for s in slots:
        week_slots[s.get("slot_date", "")].append(s)

    base_url = os.environ.get("BOLTS11_BASE_URL", "https://bolts11.com")
    board_token = board.get("token", "") if board else ""
    booking_url = f"{base_url}/book/{board_token}" if board_token else ""

    ctx.update({
        "board": board,
        "week_slots": dict(week_slots),
        "enrollments": enrollments,
        "slot_duration": slot_duration,
        "settings": settings,
        "booking_url": booking_url,
        "fmt_short_date": fmt_short_date,
    })
    return render_template("dashboard/schedule.html", **ctx)


# NOTE: /api/slots/generate is now in booking_routes.py (idempotent version)


# ---------------------------------------------------------------------------
# GET /dashboard/workers — Worker roster management
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/workers")
def workers_page():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    ctx = _base_context("workers", client_id)
    sb = _get_supabase()

    workers = []
    try:
        result = sb.table("employees").select(
            "id, name, phone, role, notes, active"
        ).eq("client_id", client_id).order("active", desc=True).order("name").execute()
        workers = result.data or []
    except Exception as e:
        print(f"[{_ts()}] WARN dashboard_routes: workers query failed — {e}")

    ctx.update({"workers": workers})
    return render_template("dashboard/workers.html", **ctx)


# ---------------------------------------------------------------------------
# POST /api/workers/create — Add a new worker
# ---------------------------------------------------------------------------

@dashboard_bp.route("/api/workers/create", methods=["POST"])
def workers_create():
    client_id = _resolve_client_id()
    if not client_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    role = (data.get("role") or "").strip()
    notes = (data.get("notes") or "").strip()

    if not name:
        return jsonify({"success": False, "error": "Name is required"})
    if not phone:
        return jsonify({"success": False, "error": "Phone is required"})

    # Normalize phone to E.164
    import re as _re
    digits = _re.sub(r'\D', '', phone)
    if len(digits) == 10:
        phone = f"+1{digits}"
    elif len(digits) == 11 and digits[0] == '1':
        phone = f"+{digits}"
    elif not phone.startswith("+"):
        phone = f"+{digits}"

    try:
        sb = _get_supabase()
        result = sb.table("employees").insert({
            "client_id": client_id,
            "name": name,
            "phone": phone,
            "role": role or None,
            "notes": notes or None,
            "active": True,
        }).execute()
        worker_id = result.data[0]["id"]
        print(f"[{_ts()}] INFO dashboard_routes: Created worker {name} id={worker_id[:8]}")
        return jsonify({"success": True, "id": worker_id})
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: workers_create failed — {e}")
        return jsonify({"success": False, "error": str(e)})


# ---------------------------------------------------------------------------
# POST /api/workers/update — Edit or deactivate a worker
# ---------------------------------------------------------------------------

@dashboard_bp.route("/api/workers/update", methods=["POST"])
def workers_update():
    client_id = _resolve_client_id()
    if not client_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    data = request.get_json(silent=True) or {}
    worker_id = data.get("id")
    if not worker_id:
        return jsonify({"success": False, "error": "Worker id required"})

    updates = {}
    if "name" in data:
        updates["name"] = data["name"].strip()
    if "phone" in data:
        import re as _re
        digits = _re.sub(r'\D', '', data["phone"].strip())
        if len(digits) == 10:
            updates["phone"] = f"+1{digits}"
        elif len(digits) == 11 and digits[0] == '1':
            updates["phone"] = f"+{digits}"
        else:
            updates["phone"] = data["phone"].strip()
    if "role" in data:
        updates["role"] = data["role"] or None
    if "notes" in data:
        updates["notes"] = data["notes"].strip() or None
    if "active" in data:
        updates["active"] = bool(data["active"])

    if not updates:
        return jsonify({"success": False, "error": "Nothing to update"})

    try:
        sb = _get_supabase()
        # Multi-tenancy: filter by both worker_id AND client_id
        sb.table("employees").update(updates).eq("id", worker_id).eq("client_id", client_id).execute()
        print(f"[{_ts()}] INFO dashboard_routes: Updated worker id={worker_id[:8]} fields={list(updates.keys())}")
        return jsonify({"success": True})
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: workers_update failed — {e}")
        return jsonify({"success": False, "error": str(e)})


# ---------------------------------------------------------------------------
# POST /api/jobs/<id>/approve-scope — Owner approves scope change
# ---------------------------------------------------------------------------

@dashboard_bp.route("/api/jobs/<job_id>/approve-scope", methods=["POST"])
def approve_scope(job_id):
    client_id = _resolve_client_id()
    if not client_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    sb = _get_supabase()

    # Verify job belongs to this client
    try:
        job_check = sb.table("jobs").select("id, customer_id").eq("id", job_id).eq("client_id", client_id).execute()
        if not job_check.data:
            return jsonify({"success": False, "error": "Job not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    # Clear scope_hold
    try:
        sb.table("jobs").update({"scope_hold": False}).eq("id", job_id).execute()
    except Exception as e:
        print(f"[{_ts()}] WARN dashboard_routes: scope_hold clear failed — {e}")

    # Find the most recent draft invoice for this job and set to sent
    invoice_id = None
    amount = 0
    customer_name = "Customer"
    try:
        inv = sb.table("invoices").select("id, amount_due, customer_id").eq(
            "job_id", job_id
        ).eq("status", "draft").order("created_at", desc=True).limit(1).execute()
        if inv.data:
            invoice_id = inv.data[0]["id"]
            amount = float(inv.data[0].get("amount_due") or 0)
            # Get customer name
            cust_id = inv.data[0].get("customer_id")
            if cust_id:
                cust = sb.table("customers").select("customer_name").eq("id", cust_id).execute()
                if cust.data:
                    customer_name = cust.data[0].get("customer_name", "Customer")
            # Update invoice status
            sb.table("invoices").update({"status": "sent"}).eq("id", invoice_id).execute()
            print(f"[{_ts()}] INFO dashboard_routes: Scope approved — invoice {invoice_id[:8]} sent for ${amount:.0f}")
    except Exception as e:
        print(f"[{_ts()}] WARN dashboard_routes: approve-scope invoice update failed — {e}")

    # SMS owner confirmation
    try:
        client = _load_client(client_id)
        owner_mobile = client.get("owner_mobile") or client.get("phone", "")
        client_phone = client.get("phone", "")
        if owner_mobile and invoice_id:
            from execution.sms_send import send_sms
            send_sms(
                to_number=owner_mobile,
                message_body=f"Invoice approved and sent for {customer_name} — ${amount:.0f}",
                from_number=client_phone,
                message_type="invoice",
            )
    except Exception as e:
        print(f"[{_ts()}] WARN dashboard_routes: approve-scope SMS failed — {e}")

    return jsonify({"success": True, "invoice_id": invoice_id})


# ---------------------------------------------------------------------------
# GET /dashboard/admin — Super admin heartbeat view
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/admin")
def admin_heartbeat():
    if not session.get("is_super_admin"):
        abort(403)

    sb = _get_supabase()
    now_iso = datetime.now(timezone.utc)

    # All clients
    clients_list = []
    try:
        result = sb.table("clients").select(
            "id, business_name, owner_name, phone, active"
        ).eq("active", True).order("business_name").execute()
        clients_list = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: admin clients query — {e}")

    # Per-client stats
    for c in clients_list:
        cid = c["id"]
        cphone = c.get("phone", "")

        # Last 5 agent activity
        try:
            act = sb.table("agent_activity").select(
                "agent_name, action_taken, output_summary, created_at"
            ).eq("client_phone", cphone).order("created_at", desc=True).limit(5).execute()
            c["recent_activity"] = act.data or []
            c["last_activity"] = act.data[0].get("created_at", "") if act.data else ""
        except Exception:
            c["recent_activity"] = []
            c["last_activity"] = ""

        # Open jobs count
        try:
            jobs = sb.table("jobs").select("id").eq("client_id", cid).not_.in_(
                "status", ["completed", "cancelled", "invoiced", "paid"]
            ).execute()
            c["open_jobs"] = len(jobs.data or [])
        except Exception:
            c["open_jobs"] = 0

        # Needs attention count
        try:
            na = sb.table("needs_attention").select("id").eq(
                "client_phone", cphone
            ).eq("status", "open").execute()
            c["needs_attention"] = len(na.data or [])
        except Exception:
            c["needs_attention"] = 0

    # Board counts by type
    board_counts = {}
    try:
        boards = sb.table("class_boards").select("board_type").execute()
        for b in (boards.data or []):
            bt = b.get("board_type", "unknown")
            board_counts[bt] = board_counts.get(bt, 0) + 1
    except Exception:
        pass

    # SMS sent today
    sms_today = 0
    try:
        today_start = now_iso.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        sms = sb.table("sms_message_log").select("id").gte("sent_at", today_start).execute()
        sms_today = len(sms.data or [])
    except Exception:
        pass

    ctx = {
        "active_page": "admin",
        "business_name": "Bolts11 Admin",
        "owner_name": session.get("owner_name", ""),
        "current_date": datetime.now().strftime("%a %b %d, %Y"),
        "today": date.today().strftime("%A, %B %-d"),
        "clients": clients_list,
        "board_counts": board_counts,
        "sms_today": sms_today,
        "total_clients": len(clients_list),
        "total_boards": sum(board_counts.values()),
        "fmt_activity_time": fmt_activity_time,
    }
    return render_template("dashboard/admin.html", **ctx)

# ---------------------------------------------------------------------------
# Stub routes — coming soon pages
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/customers/")
def customers():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")

    ctx = _base_context("customers", client_id)
    sb = _get_supabase()

    # Load all customers for this client
    customers_list = []
    try:
        result = sb.table("customers").select(
            "id, customer_name, customer_phone, customer_email, customer_address, sms_consent, created_at"
        ).eq("client_id", client_id).order("customer_name").execute()
        customers_list = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR dashboard_routes: customers query — {e}")

    # Annotate each customer with job count and last job date
    cust_ids = [c["id"] for c in customers_list]
    job_counts = {}
    last_jobs = {}
    if cust_ids:
        try:
            jobs_result = sb.table("jobs").select(
                "customer_id, scheduled_date"
            ).eq("client_id", client_id).in_("customer_id", cust_ids).execute()
            for j in (jobs_result.data or []):
                cid = j["customer_id"]
                job_counts[cid] = job_counts.get(cid, 0) + 1
                sd = j.get("scheduled_date") or ""
                if sd > last_jobs.get(cid, ""):
                    last_jobs[cid] = sd
        except Exception as e:
            print(f"[{_ts()}] ERROR dashboard_routes: customer job counts — {e}")

    for c in customers_list:
        c["job_count"] = job_counts.get(c["id"], 0)
        c["last_job"] = last_jobs.get(c["id"], "")

    ctx.update({
        "customers": customers_list,
        "fmt_phone": fmt_phone,
        "fmt_short_date": fmt_short_date,
    })
    return render_template("dashboard/customers.html", **ctx)


@dashboard_bp.route("/dashboard/purchases/")
def purchases():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")
    ctx = _base_context("purchases", client_id)
    return render_template("dashboard/coming_soon.html",
        page_name="Purchases", **ctx)


@dashboard_bp.route("/dashboard/receipts/")
def receipts():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")
    ctx = _base_context("receipts", client_id)
    return render_template("dashboard/coming_soon.html",
        page_name="Receipts", **ctx)


@dashboard_bp.route("/dashboard/accounting/")
def accounting():
    client_id = _resolve_client_id()
    if not client_id:
        return redirect("/login")
    ctx = _base_context("accounting", client_id)
    return render_template("dashboard/coming_soon.html",
        page_name="Accounting", **ctx)


# ---------------------------------------------------------------------------
# Public routes — no sidebar, served as static files
# ---------------------------------------------------------------------------

@dashboard_bp.route("/dashboard/book.html")
@dashboard_bp.route("/book")
def booking_form():
    dashboard_dir = os.path.join(_project_root, "dashboard")
    return send_from_directory(dashboard_dir, "book.html")
