"""
routes/admin_routes.py — All routes for the Bolts11 Admin Dashboard

Blueprint: admin_bp

Routes:
  GET/POST  /                        — login
  GET       /logout                  — logout
  GET       /requests                — access requests list
  POST      /requests/<id>/approve   — approve + provision client
  POST      /requests/<id>/reject    — reject request
  GET       /clients                 — all active clients
  GET       /clients/<id>            — client detail + API usage
  POST      /clients/<id>/resend-welcome — resend welcome email
  GET       /costs                   — API cost tracking across all clients
  GET       /health                  — simple health check

Auth: single shared ADMIN_PIN (env var) — not per-user.
All routes except /login and /health require admin session.
"""

import os
import re
import sys
import json
from datetime import datetime, timezone
from functools import wraps

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import (
    Blueprint, render_template, request, redirect,
    session, jsonify, url_for, flash
)
from werkzeug.security import generate_password_hash

admin_bp = Blueprint("admin_bp", __name__)

ADMIN_PIN = os.environ.get("ADMIN_PIN", "")


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sb():
    """Get Supabase client — shared DB with main app."""
    from supabase import create_client
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    return create_client(url, key)


def _require_admin(f):
    """Decorator: redirect to login if not authenticated."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_authed"):
            return redirect("/")
        return f(*args, **kwargs)
    return decorated


def _fmt_dt(iso_str):
    """Format ISO timestamp for display."""
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y %I:%M %p")
    except Exception:
        return iso_str[:16]


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return f"+{digits}" if digits else ""


# ── Health check ─────────────────────────────────────────────────────────────

@admin_bp.route("/health")
def health():
    return jsonify({"status": "ok", "service": "bolts11-admin"}), 200


# ── Login ─────────────────────────────────────────────────────────────────────

@admin_bp.route("/", methods=["GET", "POST"])
def login():
    if session.get("admin_authed"):
        return redirect("/requests")

    error = None
    if request.method == "POST":
        pin = request.form.get("pin", "").strip()
        if not ADMIN_PIN:
            error = "ADMIN_PIN not configured in Railway env vars."
        elif pin == ADMIN_PIN:
            session["admin_authed"] = True
            session.permanent = True
            print(f"[{_ts()}] INFO admin: Login success")
            return redirect("/requests")
        else:
            error = "Incorrect PIN."
            print(f"[{_ts()}] WARN admin: Bad PIN attempt")

    return render_template("admin_login.html", error=error)


@admin_bp.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ── Access Requests ───────────────────────────────────────────────────────────

@admin_bp.route("/requests")
@_require_admin
def requests_list():
    try:
        sb = _sb()
        result = sb.table("access_requests") \
            .select("*") \
            .order("created_at", desc=True) \
            .execute()
        requests_data = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR admin: fetch access_requests — {e}")
        requests_data = []

    # Split by status
    pending  = [r for r in requests_data if r.get("status") == "pending"]
    contacted = [r for r in requests_data if r.get("status") == "contacted"]
    approved = [r for r in requests_data if r.get("status") == "approved"]
    rejected = [r for r in requests_data if r.get("status") in ("rejected", "declined")]

    return render_template("admin_requests.html",
        pending=pending,
        contacted=contacted,
        approved=approved,
        rejected=rejected,
        fmt_dt=_fmt_dt,
        total=len(requests_data),
    )


@admin_bp.route("/requests/<req_id>/approve", methods=["POST"])
@_require_admin
def approve_request(req_id):
    try:
        sb = _sb()
        # Get the request
        result = sb.table("access_requests").select("*").eq("id", req_id).execute()
        if not result.data:
            flash("Request not found.", "error")
            return redirect("/requests")

        req = result.data[0]
        phone_e164 = _normalize_phone(req.get("phone", ""))

        # Check if client already exists
        existing = sb.table("clients").select("id").eq("phone", phone_e164).execute()
        if existing.data:
            flash(f"Client with phone {phone_e164} already exists.", "warning")
            sb.table("access_requests").update({
                "status": "approved",
                "approved_at": datetime.utcnow().isoformat(),
            }).eq("id", req_id).execute()
            return redirect("/requests")

        # Provision new client in Supabase
        business_type = req.get("business_type", "")
        business_name = request.form.get("business_name") or f"{req.get('name', '').split()[0]}'s {business_type}"
        owner_name    = req.get("name", "")
        owner_email   = req.get("email", "")

        new_client = {
            "business_name": business_name,
            "owner_name":    owner_name,
            "phone":         phone_e164,
            "active":        True,
            "trade_vertical": _map_vertical(business_type),
            "created_at":    datetime.utcnow().isoformat(),
        }
        client_result = sb.table("clients").insert(new_client).execute()
        client_id = client_result.data[0]["id"] if client_result.data else None

        # Mark request approved
        sb.table("access_requests").update({
            "status":      "approved",
            "approved_at": datetime.utcnow().isoformat(),
        }).eq("id", req_id).execute()

        # Send welcome email
        if owner_email:
            try:
                from execution.resend_agent import send_welcome_email
                send_welcome_email(
                    name=owner_name,
                    email=owner_email,
                    business_name=business_name,
                    phone=phone_e164,
                )
                print(f"[{_ts()}] INFO admin: Welcome email sent → {owner_email}")
            except Exception as e:
                print(f"[{_ts()}] WARN admin: Welcome email failed — {e}")

        # Log to agent_activity
        try:
            sb.table("agent_activity").insert({
                "client_phone":   phone_e164,
                "agent_name":     "admin_onboarding",
                "action_taken":   "client_provisioned",
                "input_summary":  f"Access request approved for {owner_name}",
                "output_summary": f"Client created: {business_name} | ID: {client_id}",
                "sms_sent":       False,
                "created_at":     datetime.utcnow().isoformat(),
            }).execute()
        except Exception:
            pass

        print(f"[{_ts()}] INFO admin: Client provisioned — {business_name} ({phone_e164})")
        flash(f"✓ {business_name} is live. Welcome email sent to {owner_email}.", "success")

    except Exception as e:
        print(f"[{_ts()}] ERROR admin: approve_request — {e}")
        flash(f"Error approving request: {e}", "error")

    return redirect("/requests")


@admin_bp.route("/requests/<req_id>/reject", methods=["POST"])
@_require_admin
def reject_request(req_id):
    try:
        sb = _sb()
        sb.table("access_requests").update({
            "status": "rejected",
        }).eq("id", req_id).execute()
        flash("Request rejected.", "info")
    except Exception as e:
        flash(f"Error: {e}", "error")
    return redirect("/requests")


@admin_bp.route("/requests/<req_id>/contact", methods=["POST"])
@_require_admin
def mark_contacted(req_id):
    try:
        sb = _sb()
        sb.table("access_requests").update({
            "status":       "contacted",
            "contacted_at": datetime.utcnow().isoformat(),
        }).eq("id", req_id).execute()
        flash("Marked as contacted.", "success")
    except Exception as e:
        flash(f"Error: {e}", "error")
    return redirect("/requests")


# ── Clients ───────────────────────────────────────────────────────────────────

@admin_bp.route("/clients")
@_require_admin
def clients_list():
    try:
        sb = _sb()
        result = sb.table("clients") \
            .select("id, business_name, owner_name, phone, active, trade_vertical, created_at") \
            .order("created_at", desc=True) \
            .execute()
        clients = result.data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR admin: fetch clients — {e}")
        clients = []

    # Get job counts per client
    counts = {}
    try:
        sb = _sb()
        jobs_result = sb.table("jobs").select("client_phone").execute()
        for j in (jobs_result.data or []):
            p = j.get("client_phone", "")
            counts[p] = counts.get(p, 0) + 1
    except Exception:
        pass

    return render_template("admin_clients.html",
        clients=clients,
        counts=counts,
        fmt_dt=_fmt_dt,
        total=len(clients),
        active_count=sum(1 for c in clients if c.get("active")),
    )


@admin_bp.route("/clients/<client_id>")
@_require_admin
def client_detail(client_id):
    try:
        sb = _sb()
        result = sb.table("clients").select("*").eq("id", client_id).execute()
        if not result.data:
            flash("Client not found.", "error")
            return redirect("/clients")
        client = result.data[0]
        phone  = client.get("phone", "")

        # Recent activity
        activity = sb.table("agent_activity") \
            .select("*") \
            .eq("client_phone", phone) \
            .order("created_at", desc=True) \
            .limit(50) \
            .execute().data or []

        # Job count
        jobs = sb.table("jobs") \
            .select("id, created_at, agent_used") \
            .eq("client_phone", phone) \
            .order("created_at", desc=True) \
            .limit(20) \
            .execute().data or []

        # SMS count
        try:
            sms = sb.table("sms_message_log") \
                .select("id, direction, created_at") \
                .eq("client_phone", phone) \
                .execute().data or []
        except Exception:
            sms = []

        # API cost estimate (from agent_activity)
        cost_estimate = _estimate_costs(activity)

    except Exception as e:
        print(f"[{_ts()}] ERROR admin: client_detail — {e}")
        flash(f"Error loading client: {e}", "error")
        return redirect("/clients")

    return render_template("admin_client_detail.html",
        client=client,
        activity=activity,
        jobs=jobs,
        sms_count=len(sms),
        cost_estimate=cost_estimate,
        fmt_dt=_fmt_dt,
    )


@admin_bp.route("/clients/<client_id>/resend-welcome", methods=["POST"])
@_require_admin
def resend_welcome(client_id):
    try:
        sb = _sb()
        result = sb.table("clients").select("*").eq("id", client_id).execute()
        if not result.data:
            flash("Client not found.", "error")
            return redirect("/clients")
        client = result.data[0]

        email = request.form.get("email", "").strip()
        if not email:
            flash("Email address required.", "error")
            return redirect(f"/clients/{client_id}")

        from execution.resend_agent import send_welcome_email
        result = send_welcome_email(
            name=client.get("owner_name", ""),
            email=email,
            business_name=client.get("business_name", ""),
            phone=client.get("phone", ""),
        )
        if result.get("success"):
            flash(f"✓ Welcome email sent to {email}", "success")
        else:
            flash(f"Email failed: {result.get('error')}", "error")

    except Exception as e:
        flash(f"Error: {e}", "error")

    return redirect(f"/clients/{client_id}")


@admin_bp.route("/clients/<client_id>/toggle-active", methods=["POST"])
@_require_admin
def toggle_active(client_id):
    try:
        sb = _sb()
        result = sb.table("clients").select("active").eq("id", client_id).execute()
        if not result.data:
            flash("Client not found.", "error")
            return redirect("/clients")
        current = result.data[0].get("active", True)
        sb.table("clients").update({"active": not current}).eq("id", client_id).execute()
        flash(f"Client {'deactivated' if current else 'activated'}.", "success")
    except Exception as e:
        flash(f"Error: {e}", "error")
    return redirect(f"/clients/{client_id}")


# ── API Cost Tracking ─────────────────────────────────────────────────────────

@admin_bp.route("/costs")
@_require_admin
def costs():
    try:
        sb = _sb()
        # Get all activity logs
        activity = sb.table("agent_activity") \
            .select("client_phone, agent_name, action_taken, created_at, output_summary") \
            .order("created_at", desc=True) \
            .limit(1000) \
            .execute().data or []

        # Get all clients for name lookup
        clients_result = sb.table("clients") \
            .select("phone, business_name") \
            .execute().data or []
        client_map = {c["phone"]: c["business_name"] for c in clients_result}

        # Aggregate costs per client
        per_client = {}
        for row in activity:
            phone = row.get("client_phone", "unknown")
            if phone not in per_client:
                per_client[phone] = {
                    "name":         client_map.get(phone, phone),
                    "phone":        phone,
                    "haiku_calls":  0,
                    "sonnet_calls": 0,
                    "sms_sent":     0,
                    "total_actions": 0,
                    "est_cost_usd": 0.0,
                }
            agent = row.get("agent_name", "")
            per_client[phone]["total_actions"] += 1
            if "haiku" in agent.lower() or "classification" in agent.lower():
                per_client[phone]["haiku_calls"]  += 1
                per_client[phone]["est_cost_usd"] += 0.0008   # ~$0.0008 per Haiku call
            elif any(x in agent.lower() for x in ["invoice", "proposal", "sonnet"]):
                per_client[phone]["sonnet_calls"] += 1
                per_client[phone]["est_cost_usd"] += 0.006    # ~$0.006 per Sonnet call
            else:
                per_client[phone]["est_cost_usd"] += 0.001    # generic estimate

        # Sort by cost desc
        ranked = sorted(per_client.values(), key=lambda x: x["est_cost_usd"], reverse=True)
        total_cost = sum(c["est_cost_usd"] for c in ranked)

    except Exception as e:
        print(f"[{_ts()}] ERROR admin: costs — {e}")
        ranked = []
        total_cost = 0.0

    return render_template("admin_costs.html",
        clients=ranked,
        total_cost=total_cost,
        fmt_dt=_fmt_dt,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _estimate_costs(activity: list) -> dict:
    """Estimate API costs from agent_activity rows."""
    haiku = sonnet = other = 0
    for row in activity:
        agent = (row.get("agent_name") or "").lower()
        if "haiku" in agent or "classification" in agent or "router" in agent:
            haiku += 1
        elif any(x in agent for x in ["invoice", "proposal", "sonnet", "content"]):
            sonnet += 1
        else:
            other += 1
    return {
        "haiku_calls":  haiku,
        "sonnet_calls": sonnet,
        "other_calls":  other,
        "est_usd":      round(haiku * 0.0008 + sonnet * 0.006 + other * 0.001, 4),
    }


def _map_vertical(business_type: str) -> str:
    """Map form business_type to vertical key."""
    bt = (business_type or "").lower()
    if any(x in bt for x in ["yoga", "pilates", "fitness", "training", "massage",
                               "wellness", "acupuncture", "dance", "nutrition"]):
        return "wellness"
    if any(x in bt for x in ["plumb", "hvac", "sewer", "septic", "electric",
                               "excavat", "heating", "drain"]):
        return "sewer_drain"
    if any(x in bt for x in ["auto", "barber", "salon", "vet", "groomin",
                               "photo", "tutor", "consult"]):
        return "professional"
    return "general"
