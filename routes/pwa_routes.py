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


def register_root_sw(app):
    """Register the /sw.js root route on the Flask app (not the blueprint)."""
    @app.route("/sw.js")
    def root_sw():
        response = send_from_directory(_static_dir, "sw.js")
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Content-Type"] = "application/javascript"
        return response
