"""
pwa_routes.py — Flask Blueprint for the Progressive Web App (PWA) shell

The PWA is the tech's primary interface on the road. It installs to the
home screen via the browser install prompt — no app store, no download.

Routes:
    GET /pwa/              — PWA shell (today's route, current job, status)
    GET /pwa/sw.js         — Service worker (served at root scope)
    GET /pwa/manifest.json — Web app manifest (alias to /static/manifest.json)

Future routes (not in this commit — see CLAUDE.md):
    GET /pwa/clock         — Clock in/out screen
    GET /pwa/job           — New job input
    GET /pwa/chat          — AI chat
"""

import os
import sys
from functools import wraps
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import (
    Blueprint, render_template, send_from_directory,
    request, jsonify, redirect, session,
)

pwa_bp = Blueprint("pwa_bp", __name__, url_prefix="/pwa")

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_static_dir = os.path.join(_project_root, "static")


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def require_pwa_auth(view):
    """Redirect to /pwa/login if no PWA session is set."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("pwa_authed") or not session.get("client_id"):
            return redirect("/pwa/login")
        return view(*args, **kwargs)
    return wrapper


@pwa_bp.route("/", strict_slashes=False)
@require_pwa_auth
def pwa_shell():
    return render_template("pwa/shell.html",
        employee_name=session.get("employee_name", "Tech"),
        employee_role=session.get("employee_role", ""),
    )


@pwa_bp.route("/login", strict_slashes=False)
def pwa_login_form():
    return render_template("pwa/login.html")


@pwa_bp.route("/login", methods=["POST"])
def pwa_login_send():
    from execution.pwa_auth import create_magic_link, find_client_by_phone
    from execution.notify import notify

    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()

    if not phone:
        return jsonify({"success": False, "error": "Phone number required"}), 400

    client_id = find_client_by_phone(phone)
    if not client_id:
        return jsonify({"success": True, "message": "If that number is on file, a login link is on the way."})

    base_url = request.host_url.rstrip("/")
    result = create_magic_link(client_id, phone, base_url)

    if not result["success"]:
        return jsonify({"success": True, "message": "If that number is on file, a login link is on the way."})

    employee = result["employee"] or {}
    employee_name = employee.get("name", "")
    first = employee_name.split()[0] if employee_name else ""

    message = (
        f"Hey {first}! Your Bolts11 login link:\n{result['url']}\n\n"
        f"Tap to sign in. Link expires in 15 minutes."
    )
    notify_result = notify(
        client_id=client_id,
        to_phone=phone,
        message=message,
        subject="Your Bolts11 login link",
        message_type="pwa_login",
    )
    print(f"[{_ts()}] INFO pwa_login: Magic link sent to {employee_name} via {notify_result.get('channel')}")
    return jsonify({"success": True, "message": "Login link sent. Check your email or texts.", "channel": notify_result.get("channel")})


@pwa_bp.route("/auth/<token>")
def pwa_auth_verify(token):
    from execution.pwa_auth import consume_magic_link
    result = consume_magic_link(token, request_ip=request.remote_addr, user_agent=request.headers.get("User-Agent", ""))
    if not result["success"]:
        return render_template("pwa/login.html", error=result.get("error", "Invalid link")), 401
    session["client_id"] = result["client_id"]
    session["employee_id"] = result.get("employee_id")
    session["employee_name"] = result.get("employee_name", "Tech")
    session["employee_role"] = result.get("employee_role", "field_tech")
    session["employee_phone"] = result.get("employee_phone", "")
    session["pwa_authed"] = True
    session.permanent = True
    print(f"[{_ts()}] INFO pwa_auth: Session set for {result.get('employee_name')} client={result.get('client_id', '')[:8]}")
    return redirect("/pwa/")


# ---------------------------------------------------------------------------
# GET /pwa/review — list pending documents for review/edit/send
# ---------------------------------------------------------------------------

@pwa_bp.route("/review", strict_slashes=False)
@require_pwa_auth
def pwa_review():
    from execution.db_connection import get_client as get_supabase

    client_id = session.get("client_id")
    sb = get_supabase()

    # Load draft proposals (estimates awaiting review)
    proposals = []
    try:
        result = sb.table("proposals").select(
            "id, customer_id, job_id, amount_estimate, status, created_at, proposal_text"
        ).eq("client_id", client_id).eq("status", "draft").order("created_at", desc=True).limit(50).execute()
        proposals = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR pwa_review: proposals query — {e}")

    # Load draft + work_order invoices
    invoices = []
    try:
        result = sb.table("invoices").select(
            "id, customer_id, job_id, amount_due, status, created_at, invoice_text"
        ).eq("client_id", client_id).in_("status", ["draft", "work_order"]).order("created_at", desc=True).limit(50).execute()
        invoices = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR pwa_review: invoices query — {e}")

    # Build combined list with type tag
    docs = []
    for p in proposals:
        docs.append({
            "id": p["id"],
            "type": "estimate",
            "customer_id": p.get("customer_id"),
            "amount": p.get("amount_estimate") or 0,
            "status": p.get("status", "draft"),
            "created_at": p.get("created_at"),
            "summary": (p.get("proposal_text") or "")[:60],
            "review_url": f"/dashboard/proposal/{p['id']}",
        })
    for inv in invoices:
        doc_type = "work_order" if inv.get("status") == "work_order" else "invoice"
        docs.append({
            "id": inv["id"],
            "type": doc_type,
            "customer_id": inv.get("customer_id"),
            "amount": inv.get("amount_due") or 0,
            "status": inv.get("status", "draft"),
            "created_at": inv.get("created_at"),
            "summary": (inv.get("invoice_text") or "")[:60],
            "review_url": f"/dashboard/invoice/{inv['id']}",
        })

    # Sort by created_at desc
    docs.sort(key=lambda d: d.get("created_at") or "", reverse=True)

    # Resolve customer names
    cust_ids = list({d["customer_id"] for d in docs if d.get("customer_id")})
    cust_map = {}
    if cust_ids:
        try:
            custs = sb.table("customers").select("id, customer_name").in_("id", cust_ids).execute().data or []
            cust_map = {c["id"]: c.get("customer_name") or "—" for c in custs}
        except Exception:
            pass

    return render_template("pwa/review.html",
        employee_name=session.get("employee_name", "Tech"),
        docs=docs,
        cust_map=cust_map,
    )


@pwa_bp.route("/clock", strict_slashes=False)
@require_pwa_auth
def pwa_clock():
    return render_template("pwa/clock.html",
        employee_name=session.get("employee_name", "Tech"),
        employee_role=session.get("employee_role", ""),
    )


@pwa_bp.route("/api/clock/status", methods=["GET"])
@require_pwa_auth
def pwa_clock_status():
    from execution.pwa_clock import get_status
    client_id = session.get("client_id")
    employee_id = session.get("employee_id")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400
    return jsonify({"success": True, **get_status(client_id, employee_id)})


@pwa_bp.route("/api/clock/in", methods=["POST"])
@require_pwa_auth
def pwa_clock_in():
    from execution.pwa_clock import clock_in
    client_id = session.get("client_id")
    employee_id = session.get("employee_id")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400
    result = clock_in(client_id, employee_id)
    return jsonify(result), (200 if result.get("success") else 400)


@pwa_bp.route("/api/clock/out", methods=["POST"])
@require_pwa_auth
def pwa_clock_out():
    from execution.pwa_clock import clock_out
    client_id = session.get("client_id")
    employee_id = session.get("employee_id")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400
    result = clock_out(client_id, employee_id)
    return jsonify(result), (200 if result.get("success") else 400)


@pwa_bp.route("/route", strict_slashes=False)
@require_pwa_auth
def pwa_route():
    return render_template("pwa/route.html",
        employee_name=session.get("employee_name", "Tech"),
        employee_role=session.get("employee_role", ""),
    )


@pwa_bp.route("/api/route", methods=["GET"])
@require_pwa_auth
def pwa_route_data():
    from execution.pwa_jobs import get_route
    client_id = session.get("client_id")
    employee_id = session.get("employee_id")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400
    return jsonify(get_route(client_id, employee_id))


@pwa_bp.route("/api/schedule", methods=["GET"])
@require_pwa_auth
def pwa_schedule_data():
    """
    Return jobs for the next N days (default 5) for this employee.
    Each day includes: date, label (Today/Tomorrow/weekday), and job list.
    Also includes carry_forward jobs (dispatch_status='carry_forward') as
    a priority bucket at the top of today.
    """
    from execution.pwa_jobs import get_schedule
    client_id   = session.get("client_id")
    employee_id = session.get("employee_id")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400
    days = min(int(request.args.get("days", 5)), 7)
    return jsonify(get_schedule(client_id, employee_id, days=days))


@pwa_bp.route("/api/job/<job_id>/pull-to-today", methods=["POST"])
@require_pwa_auth
def pwa_job_pull_to_today(job_id):
    """
    Pull a future scheduled job into today's route.
    Updates jobs.scheduled_date to today, moves route_assignments row.
    """
    from execution.pwa_jobs import pull_job_to_today
    client_id   = session.get("client_id")
    employee_id = session.get("employee_id")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400
    result = pull_job_to_today(client_id, employee_id, job_id)
    return jsonify(result), (200 if result.get("success") else 400)


@pwa_bp.route("/api/job/<job_id>/start", methods=["POST"])
@require_pwa_auth
def pwa_job_start(job_id):
    from execution.pwa_jobs import start_job
    client_id = session.get("client_id")
    employee_id = session.get("employee_id")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400
    result = start_job(client_id, employee_id, job_id)
    return jsonify(result), (200 if result.get("success") else 400)


@pwa_bp.route("/api/job/<job_id>/done", methods=["POST"])
@require_pwa_auth
def pwa_job_done(job_id):
    from execution.pwa_jobs import complete_job
    client_id = session.get("client_id")
    employee_id = session.get("employee_id")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400
    result = complete_job(client_id, employee_id, job_id)
    return jsonify(result), (200 if result.get("success") else 400)


@pwa_bp.route("/api/job/<job_id>/status", methods=["POST"])
@require_pwa_auth
def pwa_job_status(job_id):
    from execution.pwa_jobs import set_status
    client_id = session.get("client_id")
    employee_id = session.get("employee_id")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400
    data = request.get_json(silent=True) or {}
    command = (data.get("command") or "").strip().upper()
    if not command:
        return jsonify({"success": False, "error": "Missing command"}), 400
    result = set_status(client_id, employee_id, job_id, command)
    return jsonify(result), (200 if result.get("success") else 400)


@pwa_bp.route("/job", strict_slashes=False)
@require_pwa_auth
def pwa_new_job():
    return render_template("pwa/new_job.html",
        employee_name=session.get("employee_name", "Tech"),
        employee_role=session.get("employee_role", ""),
    )


@pwa_bp.route("/api/job/new", methods=["POST"])
@require_pwa_auth
def pwa_new_job_create():
    from execution.pwa_new_job import create_proposal_from_pwa
    client_id = session.get("client_id")
    employee_id = session.get("employee_id")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400

    data = request.get_json(silent=True) or {}
    raw_input = (data.get("description") or "").strip()
    customer_name = (data.get("customer_name") or "").strip()
    customer_phone = (data.get("customer_phone") or "").strip()
    customer_address = (data.get("customer_address") or "").strip()
    customer_email = (data.get("customer_email") or "").strip()

    amount_raw = data.get("amount")
    amount = None
    if amount_raw not in (None, ""):
        try:
            v = float(amount_raw)
            if v > 0:
                amount = v
        except (TypeError, ValueError):
            pass

    if not raw_input:
        return jsonify({"success": False, "error": "Job description is required"}), 400

    result = create_proposal_from_pwa(
        client_id=client_id,
        employee_id=employee_id,
        raw_input=raw_input,
        customer_name=customer_name,
        customer_phone=customer_phone,
        customer_address=customer_address,
        customer_email=customer_email,
        amount=amount,
    )
    return jsonify(result), (200 if result.get("success") else 400)


@pwa_bp.route("/api/workorder/new", methods=["POST"])
@require_pwa_auth
def pwa_workorder_create():
    """
    Create a work order directly as a job record — no proposal, no approval.
    Called when the tech taps the 'Create work order' chip from work_order.py.

    Body params:
        customer_id       str   — UUID of an existing customer (required)
        job_type          str   — job type slug
        description       str   — human-readable job label
        amount            float — verbally agreed price (tech-entered, never AI)
        job_status        str   — 'in_progress' or 'scheduled'
        send_confirmation bool  — if true, also create + send a proposal as a courtesy doc
    """
    from execution.db_connection import get_client as get_supabase
    from execution.schema import Jobs as J, Customers as C

    client_id   = session.get("client_id")
    employee_id = session.get("employee_id")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400

    data = request.get_json(silent=True) or {}
    customer_id       = (data.get("customer_id") or "").strip()
    job_type          = (data.get("job_type") or "service").strip()
    description       = (data.get("description") or job_type).strip()
    job_status        = data.get("job_status", "scheduled")
    send_confirmation = bool(data.get("send_confirmation", False))

    # Validate amount
    try:
        amount = float(data.get("amount") or 0)
        if amount <= 0:
            raise ValueError("amount must be > 0")
    except (TypeError, ValueError) as e:
        return jsonify({"success": False, "error": f"Invalid amount: {e}"}), 400

    if not customer_id:
        return jsonify({"success": False, "error": "customer_id is required"}), 400

    # Validate job_status
    if job_status not in ("in_progress", "scheduled"):
        job_status = "scheduled"

    try:
        sb = get_supabase()

        # Write the job record directly — no proposal needed
        job_result = sb.table(J.TABLE).insert({
            J.CLIENT_ID:          client_id,
            J.CUSTOMER_ID:        customer_id,
            J.JOB_TYPE:           job_type,
            J.JOB_DESCRIPTION:    description,
            J.RAW_INPUT:          description,
            J.STATUS:             job_status,
            J.DISPATCH_STATUS:    "unassigned",
            J.ESTIMATED_AMOUNT:   amount,
            J.ASSIGNED_WORKER_ID: employee_id,
            J.SOURCE_PROPOSAL_ID: None,
        }).execute()

        if not job_result.data:
            return jsonify({"success": False, "error": "Failed to create job"}), 500

        job_id = job_result.data[0][J.ID]
        print(f"[{_ts()}] INFO workorder: Created job {str(job_id)[:8]} status={job_status} "
              f"amount={amount} customer={customer_id[:8]} employee={employee_id[:8]}")

        # Optional courtesy confirmation — fire proposal_agent as FYI doc
        # The job is already created regardless of whether this succeeds.
        if send_confirmation:
            try:
                # Look up customer phone for the SMS/email send
                cust_result = sb.table(C.TABLE).select(
                    f"{C.CUSTOMER_PHONE}, {C.CUSTOMER_NAME}, {C.CUSTOMER_ADDRESS}"
                ).eq(C.ID, customer_id).limit(1).execute()

                if cust_result.data:
                    cust = cust_result.data[0]
                    from execution.db_client import get_client_record
                    from execution.proposal_agent import run as proposal_run
                    client_rec   = get_client_record(client_id)
                    client_phone = client_rec.get("phone", "") if client_rec else ""
                    cust_phone   = cust.get(C.CUSTOMER_PHONE, "")

                    if client_phone and cust_phone:
                        proposal_run(
                            client_phone=client_phone,
                            customer_phone=cust_phone,
                            raw_input=f"{description} ${int(amount)}",
                            explicit_amount=amount,
                        )
                        print(f"[{_ts()}] INFO workorder: Confirmation sent for job {str(job_id)[:8]}")
                    else:
                        print(f"[{_ts()}] WARN workorder: send_confirmation skipped — "
                              f"missing phone (client={bool(client_phone)} cust={bool(cust_phone)})")
            except Exception as conf_err:
                # Confirmation failure is non-fatal — job is already created
                print(f"[{_ts()}] WARN workorder: send_confirmation failed — {conf_err}")

        return jsonify({"success": True, "job_id": job_id})

    except Exception as e:
        print(f"[{_ts()}] ERROR workorder: create failed — {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@pwa_bp.route("/chat", strict_slashes=False)
@require_pwa_auth
def pwa_chat_screen():
    return render_template("pwa/chat.html",
        employee_name=session.get("employee_name", "Tech"),
        employee_role=session.get("employee_role", ""),
    )


def _resolve_chat_session_id(employee_id: str) -> str:
    override = session.get("pwa_chat_session_id")
    if override:
        return override
    from execution.pwa_chat_messages import get_active_session_id
    return get_active_session_id(employee_id)


@pwa_bp.route("/api/chat/new-session", methods=["POST"])
@require_pwa_auth
def pwa_chat_new_session():
    import uuid as _uuid
    employee_id = session.get("employee_id")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400
    new_id = str(_uuid.uuid4())
    session["pwa_chat_session_id"] = new_id
    print(f"[{_ts()}] INFO pwa_chat: new chat session for {employee_id[:8]} → {new_id[:8]}")
    return jsonify({"success": True, "session_id": new_id, "messages": []})


@pwa_bp.route("/api/chat/messages", methods=["GET"])
@require_pwa_auth
def pwa_chat_history():
    from execution.pwa_chat_messages import get_history
    employee_id = session.get("employee_id")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400
    session_id = _resolve_chat_session_id(employee_id)
    messages = get_history(session_id, employee_id, limit=20)
    return jsonify({"success": True, "session_id": session_id, "messages": messages})


@pwa_bp.route("/api/chat/send", methods=["POST"])
@require_pwa_auth
def pwa_chat_send():
    from execution.pwa_chat_messages import get_history, save_message
    from execution.pwa_chat import chat as run_chat

    client_id    = session.get("client_id")
    employee_id  = session.get("employee_id")
    employee_name = session.get("employee_name", "Tech")
    employee_role = session.get("employee_role", "")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400

    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()
    if not user_message:
        return jsonify({"success": False, "error": "Empty message"}), 400

    session_id = _resolve_chat_session_id(employee_id)

    # Save user turn first
    save_message(client_id, employee_id, session_id, "user", user_message)

    history = get_history(session_id, employee_id, limit=10)
    if history and history[-1].get("content") == user_message and history[-1].get("role") == "user":
        history = history[:-1]

    business_name = "Bolts11"
    try:
        from execution.db_connection import get_client as get_supabase
        sb = get_supabase()
        cr = sb.table("clients").select("business_name").eq("id", client_id).limit(1).execute()
        if cr.data:
            business_name = cr.data[0].get("business_name") or business_name
    except Exception:
        pass

    # Run the chat agent — session_id enables the guided estimate intercept
    result = run_chat(
        client_id=client_id,
        employee_id=employee_id,
        employee_name=employee_name,
        employee_role=employee_role,
        business_name=business_name,
        user_message=user_message,
        history=history,
        session_id=session_id,   # NEW — required for guided estimate state machine
    )

    if result.get("reply"):
        meta = {"model": result.get("model", "haiku")}
        if result.get("action"):
            meta["action"] = result["action"]
        save_message(client_id, employee_id, session_id, "assistant", result["reply"], metadata=meta)

    return jsonify({
        "success": result.get("success", False),
        "reply":   result.get("reply", ""),
        "action":  result.get("action"),
        "session_id": session_id,
        "error":   result.get("error"),
    })


@pwa_bp.route("/logout")
def pwa_logout():
    for key in ("pwa_authed", "employee_id", "employee_name",
                "employee_role", "employee_phone", "pwa_chat_session_id"):
        session.pop(key, None)
    return redirect("/pwa/login")


# ---------------------------------------------------------------------------
# GET /pwa/api/employees — active employee list for job log crew picker
# ---------------------------------------------------------------------------

@pwa_bp.route("/api/employees", methods=["GET"])
@require_pwa_auth
def pwa_employees_list():
    from execution.db_connection import get_client as get_supabase
    client_id = session.get("client_id")
    try:
        sb = get_supabase()
        result = sb.table("employees").select(
            "id, name, role"
        ).eq("client_id", client_id).eq("active", True).order("name").execute()
        return jsonify({"success": True, "employees": result.data or []})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# GET /pwa/api/joblog/jobs?customer_id=X — open jobs for a customer
# ---------------------------------------------------------------------------

@pwa_bp.route("/api/joblog/jobs", methods=["GET"])
@require_pwa_auth
def pwa_joblog_jobs():
    from execution.db_connection import get_client as get_supabase
    client_id   = session.get("client_id")
    customer_id = request.args.get("customer_id", "").strip()

    try:
        sb = get_supabase()
        q = sb.table("jobs").select(
            "id, job_type, job_description, status, scheduled_date, customer_id"
        ).eq("client_id", client_id).in_(
            "status", ["new", "estimated", "scheduled", "in_progress"]
        ).order("created_at", desc=True).limit(20)

        if customer_id:
            q = q.eq("customer_id", customer_id)

        result = q.execute()
        jobs = result.data or []

        # Enrich with customer name
        if jobs:
            cust_ids = list({j.get("customer_id") for j in jobs if j.get("customer_id")})
            custs = sb.table("customers").select("id, customer_name").in_("id", cust_ids).execute()
            cust_map = {c["id"]: c["customer_name"] for c in (custs.data or [])}
            for j in jobs:
                j["customer_name"] = cust_map.get(j.get("customer_id"), "")

        return jsonify({"success": True, "jobs": jobs})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# POST /pwa/api/joblog/start — Job Start (phase 1 submit)
# ---------------------------------------------------------------------------

@pwa_bp.route("/api/joblog/start", methods=["POST"])
@require_pwa_auth
def pwa_joblog_start():
    """
    Record that a job started. Creates a job_log_sessions row.
    Returns session_id for phase 2.
    """
    import uuid as _uuid
    from execution.db_connection import get_client as get_supabase
    from execution.schema import JobLogSessions as JLS, Jobs
    from datetime import date as _date

    client_id   = session.get("client_id")
    employee_id = session.get("employee_id")
    data        = request.get_json(silent=True) or {}
    job_id      = (data.get("job_id") or "").strip()
    crew_ids    = data.get("crew_ids") or []

    if not job_id:
        return jsonify({"success": False, "error": "job_id required"}), 400
    if not crew_ids:
        return jsonify({"success": False, "error": "Select at least one crew member"}), 400

    try:
        sb = get_supabase()

        # Verify job belongs to this client
        check = sb.table(Jobs.TABLE).select("id").eq("id", job_id).eq(
            Jobs.CLIENT_ID, client_id
        ).execute()
        if not check.data:
            return jsonify({"success": False, "error": "Job not found"}), 404

        # Mark job as in_progress
        sb.table(Jobs.TABLE).update({Jobs.STATUS: "in_progress"}).eq(
            "id", job_id
        ).eq(Jobs.CLIENT_ID, client_id).execute()

        # Create job_log_sessions row
        session_id = str(_uuid.uuid4())
        sb.table(JLS.TABLE).insert({
            JLS.CLIENT_ID:    client_id,
            JLS.EMPLOYEE_ID:  employee_id,
            JLS.SESSION_ID:   session_id,
            JLS.JOB_ID:       job_id,
            JLS.LOG_DATE:     _date.today().isoformat(),
            JLS.STATUS:       "open",
            JLS.CURRENT_STEP: "phase2",
            JLS.CREW_CONFIRMED: True,
        }).execute()

        print(f"[{_ts()}] INFO pwa_joblog_start: job={job_id[:8]} crew={len(crew_ids)} session={session_id[:8]}")
        return jsonify({"success": True, "session_id": session_id})

    except Exception as e:
        print(f"[{_ts()}] ERROR pwa_joblog_start: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# POST /pwa/api/joblog/stop — Job Stop (phase 2 submit)
# ---------------------------------------------------------------------------

@pwa_bp.route("/api/joblog/stop", methods=["POST"])
@require_pwa_auth
def pwa_joblog_stop():
    """
    Save crew, equipment, materials, incidents. Close the session.
    Same DB writes as job_log._save_log() but driven by form data.
    """
    from execution.db_connection import get_client as get_supabase
    from execution.schema import (
        JobLogSessions as JLS, JobCrewLog as JCL,
        JobEquipmentLog as JEL, JobMaterialLog as JML, Jobs
    )
    from datetime import date as _date, datetime as _datetime, timezone as _tz

    client_id   = session.get("client_id")
    employee_id = session.get("employee_id")
    data        = request.get_json(silent=True) or {}

    session_id = (data.get("session_id") or "").strip()
    job_id     = (data.get("job_id") or "").strip()
    crew_ids   = data.get("crew_ids") or [employee_id]
    equipment  = data.get("equipment") or []   # list of strings
    materials  = data.get("materials") or []   # [{name, qty, unit}]
    incidents  = (data.get("incidents") or "").strip()

    if not job_id:
        return jsonify({"success": False, "error": "job_id required"}), 400

    log_date = _date.today().isoformat()

    try:
        sb = get_supabase()

        # Write crew rows
        for eid in crew_ids:
            try:
                sb.table(JCL.TABLE).insert({
                    JCL.CLIENT_ID:   client_id,
                    JCL.JOB_ID:      job_id,
                    JCL.EMPLOYEE_ID: eid,
                    JCL.LOG_DATE:    log_date,
                    JCL.LOGGED_BY:   employee_id,
                    JCL.BILLED:      False,
                }).execute()
            except Exception as e:
                print(f"[{_ts()}] WARN pwa_joblog_stop: crew insert skipped — {e}")

        # Write equipment rows
        for name in equipment:
            if not name: continue
            try:
                sb.table(JEL.TABLE).insert({
                    JEL.CLIENT_ID:      client_id,
                    JEL.JOB_ID:         job_id,
                    JEL.LOGGED_BY:      employee_id,
                    JEL.EQUIPMENT_NAME: name,
                    JEL.LOG_DATE:       log_date,
                    JEL.BILLED:         False,
                }).execute()
            except Exception as e:
                print(f"[{_ts()}] WARN pwa_joblog_stop: equip insert — {e}")

        # Write material rows
        for mat in materials:
            mat_name = (mat.get("name") or "").strip()
            if not mat_name: continue
            try:
                sb.table(JML.TABLE).insert({
                    JML.CLIENT_ID:     client_id,
                    JML.JOB_ID:        job_id,
                    JML.LOGGED_BY:     employee_id,
                    JML.MATERIAL_NAME: mat_name,
                    JML.QUANTITY:      float(mat.get("qty") or 1),
                    JML.UNIT:          mat.get("unit") or "each",
                    JML.LOG_DATE:      log_date,
                    JML.BILLABLE:      True,
                    JML.BILLED:        False,
                }).execute()
            except Exception as e:
                print(f"[{_ts()}] WARN pwa_joblog_stop: material insert — {e}")

        # If incidents reported, append to job_notes
        if incidents:
            try:
                job_r = sb.table(Jobs.TABLE).select(Jobs.JOB_NOTES).eq(
                    "id", job_id
                ).execute()
                existing = (job_r.data[0].get(Jobs.JOB_NOTES) or "") if job_r.data else ""
                note = f"[{log_date} INCIDENT] {incidents}"
                updated = (existing + "\n" + note).strip() if existing else note
                sb.table(Jobs.TABLE).update({Jobs.JOB_NOTES: updated}).eq(
                    "id", job_id
                ).eq(Jobs.CLIENT_ID, client_id).execute()
            except Exception as e:
                print(f"[{_ts()}] WARN pwa_joblog_stop: incident note — {e}")

        # Close the session
        if session_id:
            try:
                sb.table(JLS.TABLE).update({
                    JLS.STATUS:       "closed",
                    JLS.UPDATED_AT:   _datetime.now(_tz.utc).isoformat(),
                }).eq(JLS.SESSION_ID, session_id).eq(JLS.CLIENT_ID, client_id).execute()
            except Exception:
                pass

        parts = []
        if crew_ids:  parts.append(f"{len(crew_ids)} crew")
        if equipment: parts.append(f"{len(equipment)} equipment")
        if materials: parts.append(f"{len(materials)} material {'entry' if len(materials)==1 else 'entries'}")
        if incidents: parts.append("incident noted")
        msg = "Logged: " + ", ".join(parts) + f" for {log_date}." if parts else f"Log saved for {log_date}."

        print(f"[{_ts()}] INFO pwa_joblog_stop: job={job_id[:8]} crew={len(crew_ids)} equip={len(equipment)} mats={len(materials)}")
        return jsonify({"success": True, "message": msg})

    except Exception as e:
        print(f"[{_ts()}] ERROR pwa_joblog_stop: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@pwa_bp.route("/api/customers", methods=["GET"])
@require_pwa_auth
def pwa_customers_list():
    from execution.db_connection import get_client as get_supabase
    client_id = session.get("client_id")
    try:
        sb = get_supabase()
        result = sb.table("customers").select(
            "id, customer_name, customer_phone, customer_address"
        ).eq("client_id", client_id).order("customer_name").execute()
        return jsonify({"success": True, "customers": result.data or []})
    except Exception as e:
        print(f"[{_ts()}] ERROR pwa_customers_list: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# POST /pwa/api/estimate/create — form-based estimate (no chat)
# ---------------------------------------------------------------------------

@pwa_bp.route("/api/estimate/create", methods=["POST"])
@require_pwa_auth
def pwa_estimate_create():
    """
    Create an estimate from the PWA form.
    If customer_id is null, creates the customer first from name+phone.
    Then fires proposal_agent in the background.
    """
    import re as _re
    from execution.db_connection import get_client as get_supabase

    client_id   = session.get("client_id")
    employee_id = session.get("employee_id")
    data = request.get_json(silent=True) or {}

    customer_id      = (data.get("customer_id") or "").strip() or None
    customer_name    = (data.get("customer_name") or "").strip()
    customer_phone   = (data.get("customer_phone") or "").strip()
    customer_address = (data.get("customer_address") or "").strip()
    customer_email   = (data.get("customer_email") or "").strip()
    job_type         = (data.get("job_type") or "").strip()
    notes            = (data.get("notes") or "").strip()

    if not job_type:
        return jsonify({"success": False, "error": "Job type is required"}), 400

    sb = get_supabase()

    # Create customer if needed
    if not customer_id:
        if not customer_phone:
            return jsonify({"success": False, "error": "Customer phone is required for new customers"}), 400
        # Normalize phone
        digits = _re.sub(r"\D", "", customer_phone)
        if len(digits) == 10:   phone_e164 = f"+1{digits}"
        elif len(digits) == 11: phone_e164 = f"+{digits}"
        else:                   phone_e164 = customer_phone
        try:
            # Check for existing
            existing = sb.table("customers").select("id").eq(
                "client_id", client_id
            ).eq("customer_phone", phone_e164).execute()
            if existing.data:
                customer_id = existing.data[0]["id"]
            else:
                new_c = sb.table("customers").insert({
                    "client_id":        client_id,
                    "customer_name":    customer_name or "New Customer",
                    "customer_phone":   phone_e164,
                    "customer_address": customer_address or None,
                    "customer_email":   customer_email or None,
                    "sms_consent":      False,
                }).execute()
                if new_c.data:
                    customer_id = new_c.data[0]["id"]
                    print(f"[{_ts()}] INFO pwa_estimate: Created customer {customer_id[:8]}")
        except Exception as e:
            print(f"[{_ts()}] ERROR pwa_estimate: customer create failed — {e}")
            return jsonify({"success": False, "error": "Could not create customer"}), 500

    if not customer_id:
        return jsonify({"success": False, "error": "Customer required"}), 400

    # Build raw input for proposal_agent
    job_label = job_type.replace("_", " ").title()
    raw_input = job_label + (f" — {notes}" if notes else "")

    # Resolve phones for proposal_agent
    try:
        client_rec = sb.table("clients").select("phone, business_name").eq(
            "id", client_id
        ).limit(1).execute()
        client_phone   = client_rec.data[0].get("phone", "") if client_rec.data else ""
        business_name  = client_rec.data[0].get("business_name", "Bolts11") if client_rec.data else "Bolts11"
    except Exception:
        client_phone  = ""
        business_name = "Bolts11"

    try:
        cust_row = sb.table("customers").select(
            "customer_phone, customer_name"
        ).eq("id", customer_id).limit(1).execute()
        cust_phone = cust_row.data[0].get("customer_phone", "") if cust_row.data else ""
        cust_name  = cust_row.data[0].get("customer_name", "") if cust_row.data else ""
    except Exception:
        cust_phone = ""
        cust_name  = ""

    # Fire proposal_agent
    if client_phone and cust_phone:
        try:
            from execution.proposal_agent import run as proposal_run
            proposal_run(
                client_phone=client_phone,
                customer_phone=cust_phone,
                raw_input=raw_input,
            )
            msg = f"Estimate drafted for {cust_name or 'customer'} — {job_label}."
            print(f"[{_ts()}] INFO pwa_estimate: proposal_agent fired for customer={customer_id[:8]}")
        except Exception as e:
            print(f"[{_ts()}] WARN pwa_estimate: proposal_agent failed — {e}")
            msg = "Estimate queued. Check Office for results."
    else:
        msg = "Estimate queued. Check Office for results."

    return jsonify({"success": True, "message": msg, "customer_id": customer_id})


# ---------------------------------------------------------------------------
# POST /pwa/api/workorder/create — form-based work order (no chat)
# ---------------------------------------------------------------------------

@pwa_bp.route("/api/workorder/create", methods=["POST"])
@require_pwa_auth
def pwa_workorder_create_form():
    """
    Create a work order from the PWA form.
    If customer_id is null, creates the customer first from name+phone.
    """
    import re as _re
    from execution.db_connection import get_client as get_supabase
    from execution.schema import Jobs as J

    client_id   = session.get("client_id")
    employee_id = session.get("employee_id")
    data = request.get_json(silent=True) or {}

    customer_id      = (data.get("customer_id") or "").strip() or None
    customer_name    = (data.get("customer_name") or "").strip()
    customer_phone   = (data.get("customer_phone") or "").strip()
    customer_address = (data.get("customer_address") or "").strip()
    customer_email   = (data.get("customer_email") or "").strip()
    job_type         = (data.get("job_type") or "").strip()
    notes            = (data.get("notes") or "").strip()
    when             = (data.get("when") or "later").strip()
    scheduled_date   = (data.get("scheduled_date") or "").strip() or None

    try:
        amount = float(data.get("amount") or 0)
        if amount <= 0: raise ValueError()
    except Exception:
        return jsonify({"success": False, "error": "Valid price is required"}), 400

    if not job_type:
        return jsonify({"success": False, "error": "Job type is required"}), 400
    if when == "later" and not scheduled_date:
        return jsonify({"success": False, "error": "Scheduled date required"}), 400

    sb = get_supabase()

    # Create customer if needed
    if not customer_id:
        if not customer_phone:
            return jsonify({"success": False, "error": "Customer phone is required for new customers"}), 400
        digits = _re.sub(r"\D", "", customer_phone)
        if len(digits) == 10:   phone_e164 = f"+1{digits}"
        elif len(digits) == 11: phone_e164 = f"+{digits}"
        else:                   phone_e164 = customer_phone
        try:
            existing = sb.table("customers").select("id").eq(
                "client_id", client_id
            ).eq("customer_phone", phone_e164).execute()
            if existing.data:
                customer_id = existing.data[0]["id"]
            else:
                new_c = sb.table("customers").insert({
                    "client_id":        client_id,
                    "customer_name":    customer_name or "New Customer",
                    "customer_phone":   phone_e164,
                    "customer_address": customer_address or None,
                    "customer_email":   customer_email or None,
                    "sms_consent":      False,
                }).execute()
                if new_c.data:
                    customer_id = new_c.data[0]["id"]
                    print(f"[{_ts()}] INFO pwa_wo_form: Created customer {customer_id[:8]}")
        except Exception as e:
            return jsonify({"success": False, "error": "Could not create customer"}), 500

    if not customer_id:
        return jsonify({"success": False, "error": "Customer required"}), 400

    from datetime import date as _date
    import json as _json

    # doc_type: 'work_order' (default) or 'invoice'. Both create a
    # jobs row + an invoices row (matching the dashboard flow).
    doc_type = (data.get("doc_type") or "work_order").strip().lower()
    if doc_type not in ("work_order", "invoice"):
        doc_type = "work_order"

    STATUS_BY_DOC = {"work_order": "work_order", "invoice": "complete"}
    INV_STATUS_BY_DOC = {"work_order": "work_order", "invoice": "draft"}

    job_status = STATUS_BY_DOC[doc_type]
    sched_date = _date.today().isoformat() if when == "now" else (scheduled_date or _date.today().isoformat())
    job_label  = job_type.replace("_", " ").title()
    description = job_label + (f" — {notes}" if notes else "")

    try:
        result = sb.table(J.TABLE).insert({
            J.CLIENT_ID:          client_id,
            J.CUSTOMER_ID:        customer_id,
            J.JOB_TYPE:           job_type,
            J.JOB_DESCRIPTION:    description,
            J.JOB_NOTES:          notes or None,
            J.RAW_INPUT:          f"Created via PWA (doc_type={doc_type})",
            J.STATUS:             job_status,
            J.DISPATCH_STATUS:    "unassigned",
            J.ESTIMATED_AMOUNT:   amount,
            J.SCHEDULED_DATE:     sched_date,
            J.ASSIGNED_WORKER_ID: employee_id,
            J.SOURCE_PROPOSAL_ID: None,
        }).execute()

        if not result.data:
            return jsonify({"success": False, "error": "Failed to create job"}), 500

        job_id = result.data[0][J.ID]

        # Create sidecar invoices row (matching dashboard /api/jobs/create)
        inv_row = {
            "client_id":   client_id,
            "customer_id": customer_id,
            "job_id":      job_id,
            "amount_due":  amount,
            "status":      INV_STATUS_BY_DOC[doc_type],
        }
        line_items = [{"description": job_label, "qty": 1, "unit_price": amount, "total": amount, "amount": amount}]
        inv_row["line_items"] = _json.dumps(line_items)
        if notes:
            inv_row["invoice_text"] = notes
        try:
            sb.table("invoices").insert(inv_row).execute()
        except Exception as e2:
            print(f"[{_ts()}] WARN pwa_wo_form: invoice insert failed — {e2}")

        label = "Work Order" if doc_type == "work_order" else "Invoice"
        print(f"[{_ts()}] INFO pwa_wo_form: {label} created job={job_id[:8]} status={job_status} amount={amount}")
        return jsonify({"success": True, "job_id": job_id, "message": f"{label} created."})
    except Exception as e:
        print(f"[{_ts()}] ERROR pwa_wo_form: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def register_root_sw(app):
    """Register the /sw.js root route on the Flask app (not the blueprint)."""
    @app.route("/sw.js")
    def root_sw():
        response = send_from_directory(_static_dir, "sw.js")
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Content-Type"] = "application/javascript"
        return response
