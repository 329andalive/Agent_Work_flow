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


# ---------------------------------------------------------------------------
# Auth decorator — protects PWA routes
# ---------------------------------------------------------------------------

def require_pwa_auth(view):
    """Redirect to /pwa/login if no PWA session is set."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("pwa_authed") or not session.get("client_id"):
            return redirect("/pwa/login")
        return view(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# GET /pwa/ — PWA shell (auth required)
# ---------------------------------------------------------------------------

@pwa_bp.route("/", strict_slashes=False)
@require_pwa_auth
def pwa_shell():
    """The PWA shell that loads when the tech taps their home-screen icon."""
    return render_template("pwa/shell.html",
        employee_name=session.get("employee_name", "Tech"),
        employee_role=session.get("employee_role", ""),
    )


# ---------------------------------------------------------------------------
# GET /pwa/login — Magic link request screen
# ---------------------------------------------------------------------------

@pwa_bp.route("/login", strict_slashes=False)
def pwa_login_form():
    """Render the phone-number entry form."""
    return render_template("pwa/login.html")


# ---------------------------------------------------------------------------
# POST /pwa/login — Send magic link
# ---------------------------------------------------------------------------

@pwa_bp.route("/login", methods=["POST"])
def pwa_login_send():
    """
    Generate and send a magic-link login URL.

    Body: { phone: "+12075551234" }
    """
    from execution.pwa_auth import create_magic_link, find_client_by_phone
    from execution.notify import notify

    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()

    if not phone:
        return jsonify({"success": False, "error": "Phone number required"}), 400

    # Find which client this tech belongs to
    client_id = find_client_by_phone(phone)
    if not client_id:
        # Don't reveal whether the phone exists — generic message
        return jsonify({
            "success": True,
            "message": "If that number is on file, a login link is on the way.",
        })

    base_url = request.host_url.rstrip("/")
    result = create_magic_link(client_id, phone, base_url)

    if not result["success"]:
        # Same generic response — never confirm/deny existence
        return jsonify({
            "success": True,
            "message": "If that number is on file, a login link is on the way.",
        })

    # Send the link via notify router (auto-routes email vs SMS)
    employee = result["employee"] or {}
    employee_name = employee.get("name", "")
    first = employee_name.split()[0] if employee_name else ""

    message = (
        f"Hey {first}! Your Bolts11 login link:\n{result['url']}\n\n"
        f"Tap to sign in. Link expires in 15 minutes."
    )
    subject = "Your Bolts11 login link"

    notify_result = notify(
        client_id=client_id,
        to_phone=phone,
        message=message,
        subject=subject,
        message_type="pwa_login",
    )

    print(f"[{_ts()}] INFO pwa_login: Magic link sent to {employee_name} via {notify_result.get('channel')}")

    return jsonify({
        "success": True,
        "message": "Login link sent. Check your email or texts.",
        "channel": notify_result.get("channel"),
    })


# ---------------------------------------------------------------------------
# GET /pwa/auth/<token> — Verify magic link and set session
# ---------------------------------------------------------------------------

@pwa_bp.route("/auth/<token>")
def pwa_auth_verify(token):
    """Consume a magic link, set the PWA session, redirect to /pwa/."""
    from execution.pwa_auth import consume_magic_link

    result = consume_magic_link(
        token,
        request_ip=request.remote_addr,
        user_agent=request.headers.get("User-Agent", ""),
    )

    if not result["success"]:
        return render_template("pwa/login.html", error=result.get("error", "Invalid link")), 401

    # Set the PWA session
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
# GET /pwa/clock — Clock in/out screen
# ---------------------------------------------------------------------------

@pwa_bp.route("/clock", strict_slashes=False)
@require_pwa_auth
def pwa_clock():
    """The clock in/out + today's route screen."""
    return render_template("pwa/clock.html",
        employee_name=session.get("employee_name", "Tech"),
        employee_role=session.get("employee_role", ""),
    )


# ---------------------------------------------------------------------------
# GET /pwa/api/clock/status — current clock state + today's route
# ---------------------------------------------------------------------------

@pwa_bp.route("/api/clock/status", methods=["GET"])
@require_pwa_auth
def pwa_clock_status():
    from execution.pwa_clock import get_status
    client_id = session.get("client_id")
    employee_id = session.get("employee_id")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400
    status = get_status(client_id, employee_id)
    return jsonify({"success": True, **status})


# ---------------------------------------------------------------------------
# POST /pwa/api/clock/in — clock in
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# POST /pwa/api/clock/out — clock out
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# GET /pwa/route — Today's route detail screen
# ---------------------------------------------------------------------------

@pwa_bp.route("/route", strict_slashes=False)
@require_pwa_auth
def pwa_route():
    """The route detail screen — list of jobs with action buttons."""
    return render_template("pwa/route.html",
        employee_name=session.get("employee_name", "Tech"),
        employee_role=session.get("employee_role", ""),
    )


# ---------------------------------------------------------------------------
# GET /pwa/api/route — today's route + current job state
# ---------------------------------------------------------------------------

@pwa_bp.route("/api/route", methods=["GET"])
@require_pwa_auth
def pwa_route_data():
    from execution.pwa_jobs import get_route
    client_id = session.get("client_id")
    employee_id = session.get("employee_id")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400
    return jsonify(get_route(client_id, employee_id))


# ---------------------------------------------------------------------------
# POST /pwa/api/job/<job_id>/start — Start a specific job
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# POST /pwa/api/job/<job_id>/done — Complete a job + auto-invoice + advance
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# POST /pwa/api/job/<job_id>/status — BACK / PARTS / NOSHOW / SCOPE
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# GET /pwa/job — New job input screen
# ---------------------------------------------------------------------------

@pwa_bp.route("/job", strict_slashes=False)
@require_pwa_auth
def pwa_new_job():
    """The new-job input screen — tech types a job description + customer details."""
    return render_template("pwa/new_job.html",
        employee_name=session.get("employee_name", "Tech"),
        employee_role=session.get("employee_role", ""),
    )


# ---------------------------------------------------------------------------
# POST /pwa/api/job/new — Create a proposal from PWA input
# ---------------------------------------------------------------------------

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
    )
    return jsonify(result), (200 if result.get("success") else 400)


# ---------------------------------------------------------------------------
# GET /pwa/chat — AI chat screen
# ---------------------------------------------------------------------------

@pwa_bp.route("/chat", strict_slashes=False)
@require_pwa_auth
def pwa_chat_screen():
    """The AI chat screen."""
    return render_template("pwa/chat.html",
        employee_name=session.get("employee_name", "Tech"),
        employee_role=session.get("employee_role", ""),
    )


# ---------------------------------------------------------------------------
# Chat session resolution helper
# ---------------------------------------------------------------------------

def _resolve_chat_session_id(employee_id: str) -> str:
    """
    Return the active chat session id for this employee.

    The Flask session can hold a `pwa_chat_session_id` override that
    wins over the DB lookup — that's how the New Chat button starts a
    fresh conversation without dropping any rows from pwa_chat_messages.
    Once the override session has its first message saved, the DB
    lookup also returns it on subsequent calls, so the override stays
    consistent across page reloads.
    """
    override = session.get("pwa_chat_session_id")
    if override:
        return override
    from execution.pwa_chat_messages import get_active_session_id
    return get_active_session_id(employee_id)


# ---------------------------------------------------------------------------
# POST /pwa/api/chat/new-session — Start a fresh chat session
# ---------------------------------------------------------------------------

@pwa_bp.route("/api/chat/new-session", methods=["POST"])
@require_pwa_auth
def pwa_chat_new_session():
    """
    Mint a fresh chat session id and store it in the Flask session as
    an override. Does NOT delete any rows from pwa_chat_messages — the
    old session stays around for history/audit, it just stops being
    the "active" one for this employee.
    """
    import uuid as _uuid
    employee_id = session.get("employee_id")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400

    new_id = str(_uuid.uuid4())
    session["pwa_chat_session_id"] = new_id
    print(f"[{_ts()}] INFO pwa_chat: new chat session for {employee_id[:8]} → {new_id[:8]}")
    return jsonify({"success": True, "session_id": new_id, "messages": []})


# ---------------------------------------------------------------------------
# GET /pwa/api/chat/messages — Last 20 messages of current session
# ---------------------------------------------------------------------------

@pwa_bp.route("/api/chat/messages", methods=["GET"])
@require_pwa_auth
def pwa_chat_history():
    from execution.pwa_chat_messages import get_history
    employee_id = session.get("employee_id")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400

    session_id = _resolve_chat_session_id(employee_id)
    messages = get_history(session_id, employee_id, limit=20)

    return jsonify({
        "success": True,
        "session_id": session_id,
        "messages": messages,
    })


# ---------------------------------------------------------------------------
# POST /pwa/api/chat/send — Send a user message, get assistant reply
# ---------------------------------------------------------------------------

@pwa_bp.route("/api/chat/send", methods=["POST"])
@require_pwa_auth
def pwa_chat_send():
    from execution.pwa_chat_messages import get_history, save_message
    from execution.pwa_chat import chat as run_chat

    client_id = session.get("client_id")
    employee_id = session.get("employee_id")
    employee_name = session.get("employee_name", "Tech")
    employee_role = session.get("employee_role", "")
    if not employee_id:
        return jsonify({"success": False, "error": "No employee in session"}), 400

    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()
    if not user_message:
        return jsonify({"success": False, "error": "Empty message"}), 400

    # Resolve session id — Flask override (from /new-session) wins over DB
    session_id = _resolve_chat_session_id(employee_id)

    # Save the user turn first so it shows up even if Claude fails
    save_message(client_id, employee_id, session_id, "user", user_message)

    # Load recent history (excluding the message we just saved).
    # The agent only needs the last 10 turns for context — the chat
    # screen UI loads more on /pwa/api/chat/messages, but we don't pay
    # the model to re-read ancient history every turn.
    history = get_history(session_id, employee_id, limit=10)

    # Drop the message we just inserted from the history pass to the agent
    # (it's already in the user_message arg)
    if history and history[-1].get("content") == user_message and history[-1].get("role") == "user":
        history = history[:-1]

    # Resolve business name (cheap lookup, cached client-side could come later)
    business_name = "Bolts11"
    try:
        from execution.db_connection import get_client as get_supabase
        sb = get_supabase()
        cr = sb.table("clients").select("business_name").eq("id", client_id).limit(1).execute()
        if cr.data:
            business_name = cr.data[0].get("business_name") or business_name
    except Exception:
        pass

    # Run the chat agent
    result = run_chat(
        client_id=client_id,
        employee_id=employee_id,
        employee_name=employee_name,
        employee_role=employee_role,
        business_name=business_name,
        user_message=user_message,
        history=history,
    )

    # Save the assistant reply (whether success or fallback message).
    # Persist the action chip in metadata so re-loading history shows it.
    if result.get("reply"):
        meta = {"model": result.get("model", "haiku")}
        if result.get("action"):
            meta["action"] = result["action"]
        save_message(
            client_id, employee_id, session_id,
            "assistant", result["reply"],
            metadata=meta,
        )

    return jsonify({
        "success": result.get("success", False),
        "reply": result.get("reply", ""),
        "action": result.get("action"),
        "session_id": session_id,
        "error": result.get("error"),
    })


# ---------------------------------------------------------------------------
# GET /pwa/logout — Clear PWA session
# ---------------------------------------------------------------------------

@pwa_bp.route("/logout")
def pwa_logout():
    """Clear PWA-specific session keys and return to login."""
    for key in ("pwa_authed", "employee_id", "employee_name",
                "employee_role", "employee_phone", "pwa_chat_session_id"):
        session.pop(key, None)
    # Don't clear client_id if dashboard auth is also active
    return redirect("/pwa/login")


# ---------------------------------------------------------------------------
# GET /sw.js — Service worker at root scope
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# GET /sw.js — Service worker at root scope
# ---------------------------------------------------------------------------
# Service workers can only control pages within their scope. A SW served
# from /static/sw.js is scoped to /static/. To control /pwa/* (and any
# other root path), we need to serve sw.js from a path that includes the
# scope we want. We expose it at /sw.js (root) so it can control the
# whole app, including /pwa/, /doc/, and /static/ assets.

def register_root_sw(app):
    """Register the /sw.js root route on the Flask app (not the blueprint)."""
    @app.route("/sw.js")
    def root_sw():
        response = send_from_directory(_static_dir, "sw.js")
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Content-Type"] = "application/javascript"
        return response
