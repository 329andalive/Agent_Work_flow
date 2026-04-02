"""
routes/access_request_routes.py
FIXED: accepts temp PIN 5555 for new clients with no pin_hash set,
       then redirects them to /set-pin to create their real PIN.
       Also formats phone display correctly.
"""

import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, request, jsonify, session
from werkzeug.security import check_password_hash

access_bp      = Blueprint("access_bp", __name__)
SUPPORT_EMAIL  = "support@bolts11.com"
ALLOWED_ORIGINS = ["https://bolts11.com", "https://www.bolts11.com", "https://api.bolts11.com"]
BASE_URL       = os.environ.get("BOLTS11_BASE_URL", "https://agentworkflow-production.up.railway.app")
TEMP_PIN       = "5555"


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _cors(resp, status=200):
    origin = request.headers.get("Origin", "")
    resp.status_code = status
    resp.headers["Access-Control-Allow-Origin"]      = origin if origin in ALLOWED_ORIGINS else ALLOWED_ORIGINS[0]
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


# ── POST /api/access-request ──────────────────────────────────────────────────

@access_bp.route("/api/access-request", methods=["POST", "OPTIONS"])
def access_request():
    if request.method == "OPTIONS":
        return _cors(jsonify({}), 200)
    data          = request.get_json(silent=True) or {}
    name          = (data.get("name")          or "").strip()
    email         = (data.get("email")         or "").strip()
    phone         = (data.get("phone")         or "").strip()
    business_type = (data.get("business_type") or "").strip()

    if not name or not email or not phone:
        return _cors(jsonify({"error": "Name, email, and phone are required."}), 400)

    phone_e164 = _normalize_phone(phone)

    try:
        sb = _get_supabase()
        sb.table("access_requests").insert({
            "name": name, "email": email, "phone": phone_e164,
            "business_type": business_type, "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
    except Exception as e:
        print(f"[{_ts()}] WARN access_request: DB insert failed — {e}")

    try:
        from execution.resend_agent import send_access_request_confirmation, send_access_request_alert
        r1 = send_access_request_confirmation(name=name, email=email, business_type=business_type)
        r2 = send_access_request_alert(name=name, email=email, phone=phone_e164, business_type=business_type)
        print(f"[{_ts()}] INFO access_request: Emails sent — conf={r1.get('success')} alert={r2.get('success')}")
    except Exception as e:
        print(f"[{_ts()}] ERROR access_request: Email send failed — {e}")

    return _cors(jsonify({"success": True}), 200)


# ── POST /api/auth/portal-login ───────────────────────────────────────────────

@access_bp.route("/api/auth/portal-login", methods=["POST", "OPTIONS"])
def portal_login():
    """
    Login flow for bolts11.com/signin.html

    NEW CLIENT (no pin_hash):
      - Temp PIN 5555 accepted → redirect to /set-pin to create real PIN
      - Wrong PIN → friendly error explaining to use 5555

    EXISTING CLIENT (has pin_hash):
      - Correct PIN → redirect to /dashboard/
      - Wrong PIN → error
    """
    if request.method == "OPTIONS":
        return _cors(jsonify({}), 200)

    data  = request.get_json(silent=True) or {}
    phone = _normalize_phone(data.get("phone", ""))
    pin   = (data.get("pin") or "").strip()

    if not phone:
        return _cors(jsonify({"error": "Phone number is required."}), 400)

    try:
        sb     = _get_supabase()
        result = sb.table("clients").select(
            "id, pin_hash, business_name, owner_name, active"
        ).eq("phone", phone).execute()

        if not result.data:
            print(f"[{_ts()}] WARN portal_login: Phone not found — {phone}")
            return _cors(jsonify({
                "error": "Phone number not recognized. Contact support@bolts11.com."
            }), 401)

        client   = result.data[0]
        pin_hash = client.get("pin_hash") or ""

        if not client.get("active"):
            return _cors(jsonify({"error": "Account inactive. Contact support@bolts11.com."}), 403)

        # ── NEW CLIENT: no real PIN set yet ───────────────────────────────────
        if not pin_hash:
            if pin != TEMP_PIN:
                return _cors(jsonify({
                    "error": f"Welcome! For your first login, enter {TEMP_PIN} as your temporary PIN."
                }), 401)
            # Temp PIN accepted — send to set-pin page to create real PIN
            set_pin_url = BASE_URL.rstrip("/") + f"/set-pin?phone={phone}"
            print(f"[{_ts()}] INFO portal_login: Temp PIN accepted, redirecting to set-pin — {phone}")
            return _cors(jsonify({
                "redirect_url": set_pin_url,
                "message":      "no_pin_set",
            }), 200)

        # ── EXISTING CLIENT: validate real PIN ────────────────────────────────
        if not pin:
            return _cors(jsonify({"error": "Please enter your PIN."}), 400)

        if not check_password_hash(pin_hash, pin):
            print(f"[{_ts()}] WARN portal_login: Bad PIN — {phone}")
            return _cors(jsonify({"error": "Incorrect PIN. Try again."}), 401)

        # Success
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
