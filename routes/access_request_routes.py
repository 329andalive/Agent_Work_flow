"""
routes/access_request_routes.py — Early access form + portal login endpoint
FIXED: portal-login now correctly handles new clients with no PIN set
"""

import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, request, jsonify, session
from werkzeug.security import check_password_hash

access_bp = Blueprint("access_bp", __name__)

SUPPORT_EMAIL  = "support@bolts11.com"
BOLTS11_ORIGIN = "https://bolts11.com"
BASE_URL        = os.environ.get("BOLTS11_BASE_URL", "https://web-production-043dc.up.railway.app")


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _cors(resp, status=200):
    resp.status_code = status
    resp.headers["Access-Control-Allow-Origin"]      = BOLTS11_ORIGIN
    resp.headers["Access-Control-Allow-Credentials"] = "true"
    resp.headers["Access-Control-Allow-Headers"]     = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"]     = "POST, OPTIONS"
    return resp


def _normalize_phone(raw):
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:  return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"): return f"+{digits}"
    return f"+{digits}" if digits else ""


def _get_supabase():
    from execution.db_connection import get_client
    return get_client()


# ── CORS preflight ────────────────────────────────────────────────────────────

@access_bp.route("/api/access-request", methods=["OPTIONS"])
@access_bp.route("/api/auth/portal-login", methods=["OPTIONS"])
def handle_preflight():
    return _cors(jsonify({}), 200)


# ── POST /api/access-request ──────────────────────────────────────────────────

@access_bp.route("/api/access-request", methods=["POST"])
def access_request():
    data          = request.get_json(silent=True) or {}
    name          = (data.get("name")          or "").strip()
    email         = (data.get("email")         or "").strip()
    phone         = (data.get("phone")         or "").strip()
    business_type = (data.get("business_type") or "").strip()

    if not name or not email or not phone:
        return _cors(jsonify({"error": "Name, email, and phone are required."}), 400)

    phone_e164 = _normalize_phone(phone)

    # Save to Supabase
    try:
        sb = _get_supabase()
        sb.table("access_requests").insert({
            "name": name, "email": email, "phone": phone_e164,
            "business_type": business_type, "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
    except Exception as e:
        print(f"[{_ts()}] WARN access_request: DB insert failed — {e}")

    # Send emails
    try:
        from execution.resend_agent import send_access_request_confirmation, send_access_request_alert
        r1 = send_access_request_confirmation(name=name, email=email, business_type=business_type)
        r2 = send_access_request_alert(name=name, email=email, phone=phone_e164, business_type=business_type)
        print(f"[{_ts()}] INFO access_request: Emails sent — conf={r1.get('success')} alert={r2.get('success')}")
    except Exception as e:
        print(f"[{_ts()}] ERROR access_request: Email send failed — {e}")

    return _cors(jsonify({"success": True, "message": "Request received."}), 200)


# ── POST /api/auth/portal-login ───────────────────────────────────────────────

@access_bp.route("/api/auth/portal-login", methods=["POST"])
def portal_login():
    """
    Called by bolts11.com/signin.html.

    Three outcomes:
    1. Phone not found            → 401 error message
    2. Phone found, no PIN set    → 200 with redirect to /set-pin on Railway
                                    (client needs to set their PIN first)
    3. Phone found, PIN matches   → 200 with redirect to /dashboard/
    4. Phone found, PIN wrong     → 401 error message

    The signin.html JS checks for redirect_url and follows it.
    The set-pin page already exists on Railway at /set-pin?phone=...
    """
    data  = request.get_json(silent=True) or {}
    phone = _normalize_phone(data.get("phone", ""))
    pin   = (data.get("pin") or "").strip()

    if not phone:
        return _cors(jsonify({"error": "Phone number is required."}), 400)

    try:
        sb = _get_supabase()
        result = sb.table("clients").select(
            "id, pin_hash, business_name, owner_name, active"
        ).eq("phone", phone).execute()

        if not result.data:
            print(f"[{_ts()}] WARN portal_login: Phone not found — {phone}")
            return _cors(jsonify({"error": "Phone number not recognized. Contact support@bolts11.com."}), 401)

        client = result.data[0]

        if not client.get("active"):
            return _cors(jsonify({"error": "Account inactive. Contact support@bolts11.com."}), 403)

        pin_hash = client.get("pin_hash") or ""

        # ── NEW CLIENT: no PIN set yet → send to set-pin page ──
        if not pin_hash:
            set_pin_url = BASE_URL.rstrip("/") + f"/set-pin?phone={phone}"
            print(f"[{_ts()}] INFO portal_login: No PIN set — redirecting to set-pin: {phone}")
            return _cors(jsonify({
                "redirect_url": set_pin_url,
                "message":      "no_pin_set",
            }), 200)

        # ── EXISTING CLIENT: validate PIN ──
        if not pin:
            return _cors(jsonify({"error": "Please enter your PIN."}), 400)

        if not check_password_hash(pin_hash, pin):
            print(f"[{_ts()}] WARN portal_login: Bad PIN — {phone}")
            return _cors(jsonify({"error": "Incorrect PIN. Try again."}), 401)

        # ── SUCCESS: set session and redirect to dashboard ──
        session["client_id"]     = client["id"]
        session["business_name"] = client.get("business_name", "")
        session["owner_name"]    = client.get("owner_name", "")
        session.permanent        = True

        redirect_url = BASE_URL.rstrip("/") + "/dashboard/"
        print(f"[{_ts()}] INFO portal_login: Login success — {client.get('business_name')} ({phone})")
        return _cors(jsonify({"redirect_url": redirect_url}), 200)

    except Exception as e:
        print(f"[{_ts()}] ERROR portal_login: {e}")
        return _cors(jsonify({"error": "Service temporarily unavailable."}), 503)
