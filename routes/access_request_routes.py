"""
routes/access_request_routes.py — Early access form + portal login endpoint

Blueprint: access_bp
Routes:
  POST /api/access-request    — handles the bolts11.com 'Get Early Access' form
  POST /api/auth/portal-login — handles the bolts11.com signin.html login form

Both routes allow CORS from bolts11.com so the static Cloudflare site can call
the Railway Flask backend.

Register in sms_receive.py:
  from routes.access_request_routes import access_bp
  app.register_blueprint(access_bp)
"""

import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, request, jsonify, session
from werkzeug.security import check_password_hash

access_bp = Blueprint("access_bp", __name__)

SUPPORT_EMAIL = "support@bolts11.com"
BOLTS11_ORIGIN = "https://bolts11.com"


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _cors(resp, status=200):
    """Add CORS headers for bolts11.com and return response."""
    resp.status_code = status
    resp.headers["Access-Control-Allow-Origin"] = BOLTS11_ORIGIN
    resp.headers["Access-Control-Allow-Credentials"] = "true"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return resp


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return f"+{digits}" if digits else ""


def _get_supabase():
    from execution.db_connection import get_client
    return get_client()


# ── CORS preflight handler (OPTIONS) ────────────────────────────────────────

@access_bp.route("/api/access-request", methods=["OPTIONS"])
@access_bp.route("/api/auth/portal-login", methods=["OPTIONS"])
def handle_preflight():
    resp = jsonify({})
    return _cors(resp, 200)


# ── POST /api/access-request ─────────────────────────────────────────────────

@access_bp.route("/api/access-request", methods=["POST"])
def access_request():
    """
    Receives the 'Get Early Access' form from bolts11.com/index.html.
    1. Saves lead to Supabase (access_requests table — created below if needed)
    2. Emails the requester a confirmation (Resend)
    3. Emails support@bolts11.com a lead alert (Resend)

    Body (JSON):
      { "name": "...", "email": "...", "phone": "...", "business_type": "..." }
    """
    data          = request.get_json(silent=True) or {}
    name          = (data.get("name") or "").strip()
    email         = (data.get("email") or "").strip()
    phone         = (data.get("phone") or "").strip()
    business_type = (data.get("business_type") or "").strip()

    # Basic validation
    if not name or not email or not phone:
        return _cors(jsonify({"error": "Name, email, and phone are required."}), 400)

    phone_e164 = _normalize_phone(phone)

    # ── Save to Supabase ──
    try:
        sb = _get_supabase()
        sb.table("access_requests").insert({
            "name":          name,
            "email":         email,
            "phone":         phone_e164,
            "business_type": business_type,
            "status":        "pending",
            "created_at":    datetime.utcnow().isoformat(),
        }).execute()
        print(f"[{_ts()}] INFO access_request: Saved lead — {name} ({business_type})")
    except Exception as e:
        # Non-fatal — still send emails even if DB write fails
        print(f"[{_ts()}] WARN access_request: DB insert failed — {e}")

    # ── Send emails via Resend ──
    try:
        from execution.resend_agent import (
            send_access_request_confirmation,
            send_access_request_alert,
        )
        # Confirmation to requester
        r1 = send_access_request_confirmation(
            name=name,
            email=email,
            business_type=business_type,
        )
        # Internal alert to support
        r2 = send_access_request_alert(
            name=name,
            email=email,
            phone=phone_e164,
            business_type=business_type,
        )
        print(f"[{_ts()}] INFO access_request: Emails sent — conf={r1.get('success')} alert={r2.get('success')}")
    except Exception as e:
        print(f"[{_ts()}] ERROR access_request: Email send failed — {e}")

    return _cors(jsonify({
        "success": True,
        "message": "Request received. We'll be in touch within one business day."
    }), 200)


# ── POST /api/auth/portal-login ──────────────────────────────────────────────

@access_bp.route("/api/auth/portal-login", methods=["POST"])
def portal_login():
    """
    Called by bolts11.com/signin.html via fetch().
    Validates phone + PIN using the same check_password_hash logic as /login.
    Returns a redirect URL on success so the JS can send the user to the dashboard.

    Body (JSON): { "phone": "+12075550100", "pin": "1234" }
    Returns:     { "redirect_url": "https://.../dashboard/" }
    Or:          { "error": "Incorrect phone or PIN." }  with 401
    """
    data  = request.get_json(silent=True) or {}
    phone = _normalize_phone(data.get("phone", ""))
    pin   = (data.get("pin") or "").strip()

    if not phone or not pin:
        return _cors(jsonify({"error": "Phone and PIN are required."}), 400)

    try:
        sb = _get_supabase()
        result = sb.table("clients").select(
            "id, pin_hash, business_name, owner_name, active"
        ).eq("phone", phone).execute()

        if not result.data:
            print(f"[{_ts()}] WARN portal_login: Phone not found — {phone}")
            return _cors(jsonify({"error": "Incorrect phone or PIN."}), 401)

        client = result.data[0]

        if not client.get("active"):
            return _cors(jsonify({"error": "Account inactive. Contact support@bolts11.com."}), 403)

        pin_hash = client.get("pin_hash") or ""

        # No PIN set yet — redirect to set-pin page
        if not pin_hash:
            set_pin_url = (
                os.environ.get("BOLTS11_BASE_URL", "https://web-production-043dc.up.railway.app")
                + f"/set-pin?phone={phone}"
            )
            return _cors(jsonify({"redirect_url": set_pin_url}), 200)

        if not check_password_hash(pin_hash, pin):
            print(f"[{_ts()}] WARN portal_login: Bad PIN — {phone}")
            return _cors(jsonify({"error": "Incorrect phone or PIN."}), 401)

        # ── Success — establish session ──
        session["client_id"]     = client["id"]
        session["business_name"] = client.get("business_name", "")
        session["owner_name"]    = client.get("owner_name", "")
        session.permanent        = True

        base = os.environ.get("BOLTS11_BASE_URL", "https://web-production-043dc.up.railway.app")
        redirect_url = base.rstrip("/") + "/dashboard/"

        print(f"[{_ts()}] INFO portal_login: Login success — {client.get('business_name')} ({phone})")
        return _cors(jsonify({"redirect_url": redirect_url}), 200)

    except Exception as e:
        print(f"[{_ts()}] ERROR portal_login: {e}")
        return _cors(jsonify({"error": "Service temporarily unavailable."}), 503)
