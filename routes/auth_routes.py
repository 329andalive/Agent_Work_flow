"""
auth_routes.py — Login, logout, and PIN setup for dashboard access

Blueprint: auth_bp
Routes:
    GET  /login    — render login form
    POST /login    — validate phone + PIN, set session, redirect
    GET  /logout   — clear session, redirect to /login
    GET  /set-pin  — render set-pin form (first-time setup)
    POST /set-pin  — hash and save PIN to clients table

PIN hashing uses werkzeug (bundled with Flask).
Sessions are permanent (30-day lifetime, configured in app.py).
"""

import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, render_template, request, redirect, session, flash, url_for
from werkzeug.security import generate_password_hash, check_password_hash

auth_bp = Blueprint("auth_bp", __name__)


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_supabase():
    from execution.db_connection import get_client
    return get_client()


def normalize_phone(raw: str) -> str:
    """Normalize a phone input to E.164 format (+1XXXXXXXXXX)."""
    digits = re.sub(r'\D', '', raw)
    if len(digits) == 10:
        return f"+1{digits}"
    elif len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return f"+{digits}" if digits else ""


# ---------------------------------------------------------------------------
# GET /login
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET"])
def login_form():
    """Render the login page."""
    # Already logged in? Go to dashboard.
    if session.get("client_id"):
        return redirect("/dashboard/")
    return render_template("login.html")


# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["POST"])
def login_submit():
    """Validate phone + PIN, set session, redirect to dashboard."""
    phone_raw = request.form.get("phone", "").strip()
    pin = request.form.get("pin", "").strip()

    if not phone_raw or not pin:
        flash("Please enter your phone number and PIN.", "error")
        return render_template("login.html"), 400

    phone = normalize_phone(phone_raw)

    if not phone or len(phone) < 11:
        flash("Please enter a valid phone number.", "error")
        return render_template("login.html"), 400

    try:
        sb = _get_supabase()
        result = sb.table("clients").select(
            "id, pin_hash, business_name, active, owner_name, phone, is_super_admin"
        ).eq("phone", phone).execute()

        if not result.data:
            flash("Phone number not recognized.", "error")
            print(f"[{_ts()}] WARN auth: Login attempt — phone not found: {phone}")
            return render_template("login.html"), 401

        client = result.data[0]

        if not client.get("active"):
            flash("Account inactive. Contact your Bolts11 representative.", "error")
            print(f"[{_ts()}] WARN auth: Login attempt — inactive client: {phone}")
            return render_template("login.html"), 403

        # No PIN set yet — redirect to set-pin
        if not client.get("pin_hash"):
            return redirect(f"/set-pin?phone={phone}")

        # Verify PIN
        check_result = check_password_hash(client["pin_hash"], pin)

        if not check_result:
            flash("Incorrect PIN.", "error")
            print(f"[{_ts()}] WARN auth: Login attempt — bad PIN: {phone}")
            return render_template("login.html"), 401

        # Success — set session
        session["client_id"] = client["id"]
        session["business_name"] = client.get("business_name", "")
        session["owner_name"] = client.get("owner_name", "")
        if client.get("is_super_admin"):
            session["is_super_admin"] = True
        session.permanent = True

        print(f"[{_ts()}] INFO auth: Login success — {client.get('business_name')} ({phone}){' [SUPER ADMIN]' if client.get('is_super_admin') else ''}")
        print(f"[{_ts()}] INFO auth: Session set — client_id={client['id'][:8]}... business={client.get('business_name')}")
        return redirect("/dashboard/")

    except Exception as e:
        print(f"[{_ts()}] ERROR auth: login_submit failed — {e}")
        flash("Something went wrong. Please try again.", "error")
        return render_template("login.html"), 500


# ---------------------------------------------------------------------------
# GET /logout
# ---------------------------------------------------------------------------

@auth_bp.route("/logout")
def logout():
    """Clear session and redirect to login."""
    biz = session.get("business_name", "")
    session.clear()
    print(f"[{_ts()}] INFO auth: Logout — {biz}")
    return redirect("/login")


# ---------------------------------------------------------------------------
# GET /set-pin
# ---------------------------------------------------------------------------

@auth_bp.route("/set-pin", methods=["GET"])
def set_pin_form():
    """Render the first-time PIN setup form."""
    phone = request.args.get("phone", "")
    if not phone:
        return redirect("/login")
    return render_template("set_pin.html", phone=phone)


# ---------------------------------------------------------------------------
# POST /set-pin
# ---------------------------------------------------------------------------

@auth_bp.route("/set-pin", methods=["POST"])
def set_pin_submit():
    """Hash and save a new PIN for the client."""
    phone = request.form.get("phone", "").strip()
    pin = request.form.get("pin", "").strip()
    pin_confirm = request.form.get("pin_confirm", "").strip()

    if not phone:
        return redirect("/login")

    phone = normalize_phone(phone)

    # Validate PIN format
    if not re.fullmatch(r'\d{4}', pin):
        flash("PIN must be exactly 4 digits.", "error")
        return render_template("set_pin.html", phone=phone), 400

    if pin != pin_confirm:
        flash("PINs don't match. Try again.", "error")
        return render_template("set_pin.html", phone=phone), 400

    try:
        sb = _get_supabase()
        result = sb.table("clients").select(
            "id, business_name, active, owner_name"
        ).eq("phone", phone).execute()

        if not result.data:
            flash("Phone number not recognized.", "error")
            return redirect("/login")

        client = result.data[0]

        if not client.get("active"):
            flash("Account inactive.", "error")
            return redirect("/login")

        # Hash and save PIN
        pin_hash = generate_password_hash(pin)
        sb.table("clients").update({"pin_hash": pin_hash}).eq("id", client["id"]).execute()

        # Set session
        session["client_id"] = client["id"]
        session["business_name"] = client.get("business_name", "")
        session["owner_name"] = client.get("owner_name", "")
        session.permanent = True

        print(f"[{_ts()}] INFO auth: PIN set + login — {client.get('business_name')} ({phone})")
        return redirect("/dashboard/")

    except Exception as e:
        print(f"[{_ts()}] ERROR auth: set_pin_submit failed — {e}")
        flash("Something went wrong. Please try again.", "error")
        return render_template("set_pin.html", phone=phone), 500
