"""
routes/admin_routes.py — All routes for the Bolts11 Admin Dashboard
"""
import os
import re
import sys
from datetime import datetime
from functools import wraps

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, render_template, request, redirect, session, jsonify, flash
from werkzeug.security import generate_password_hash

admin_bp = Blueprint("admin_bp", __name__)
ADMIN_PIN = os.environ.get("ADMIN_PIN", "")


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sb():
    from supabase import create_client
    return create_client(os.environ.get("SUPABASE_URL",""), os.environ.get("SUPABASE_SERVICE_KEY",""))


def _require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_authed"):
            return redirect("/")
        return f(*args, **kwargs)
    return decorated


def _fmt_dt(iso_str):
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y %I:%M %p")
    except Exception:
        return iso_str[:16]


def _normalize_phone(raw):
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10: return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"): return f"+{digits}"
    return f"+{digits}" if digits else ""


def _map_vertical(business_type):
    bt = (business_type or "").lower()
    if any(x in bt for x in ["yoga","pilates","fitness","training","massage","wellness","acupuncture","dance","nutrition"]):
        return "wellness"
    if any(x in bt for x in ["plumb","hvac","sewer","septic","electric","excavat","heating","drain"]):
        return "sewer_drain"
    return "professional"


def _estimate_costs(activity):
    haiku = sonnet = other = 0
    for row in activity:
        agent = (row.get("agent_name") or "").lower()
        if "haiku" in agent or "classification" in agent or "router" in agent:
            haiku += 1
        elif any(x in agent for x in ["invoice","proposal","sonnet","content"]):
            sonnet += 1
        else:
            other += 1
    return {
        "haiku_calls": haiku, "sonnet_calls": sonnet, "other_calls": other,
        "est_usd": round(haiku*0.0008 + sonnet*0.006 + other*0.001, 4),
    }


@admin_bp.route("/health")
def health():
    return jsonify({"status": "ok", "service": "bolts11-admin"}), 200


@admin_bp.route("/", methods=["GET","POST"])
def login():
    if session.get("admin_authed"):
        return redirect("/requests")
    error = None
    if request.method == "POST":
        pin = request.form.get("pin","").strip()
        if not ADMIN_PIN:
            error = "ADMIN_PIN not configured in Railway env vars."
        elif pin == ADMIN_PIN:
            session["admin_authed"] = True
            session.permanent = True
            return redirect("/requests")
        else:
            error = "Incorrect PIN."
    return render_template("admin_login.html", error=error)


@admin_bp.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@admin_bp.route("/requests")
@_require_admin
def requests_list():
    try:
        sb = _sb()
        data = sb.table("access_requests").select("*").order("created_at", desc=True).execute().data or []
    except Exception as e:
        print(f"[{_ts()}] ERROR admin: {e}")
        data = []
    # Get pending count for sidebar badge
    pending_count = sum(1 for r in data if r.get("status") == "pending")

    approved = [r for r in data if r.get("status") == "approved"]

    # Attach client_id + active state to each approved row so the
    # template can render a clickable "Manage →" link that takes the
    # admin straight to /clients/<id> where the Reset PIN / Send
    # Reminder / Delete forms live. Approve writes to clients.phone
    # in E.164, so we normalize each request's phone to match and
    # do a single batched lookup.
    if approved:
        approved_phones_e164 = list({
            _normalize_phone(r.get("phone", ""))
            for r in approved if r.get("phone")
        })
        client_by_phone = {}
        if approved_phones_e164:
            try:
                clients_result = sb.table("clients").select(
                    "id, phone, business_name, active"
                ).in_("phone", approved_phones_e164).execute()
                for c in (clients_result.data or []):
                    client_by_phone[c.get("phone", "")] = c
            except Exception as e:
                print(f"[{_ts()}] WARN admin: client lookup for approved rows failed — {e}")

        for r in approved:
            c = client_by_phone.get(_normalize_phone(r.get("phone", ""))) or {}
            r["client_id"] = c.get("id")
            r["client_active"] = c.get("active") if c else None
            r["client_business_name"] = c.get("business_name") or r.get("business_type") or "—"

    return render_template("admin_requests.html",
        pending=[r for r in data if r.get("status")=="pending"],
        contacted=[r for r in data if r.get("status")=="contacted"],
        approved=approved,
        rejected=[r for r in data if r.get("status") in ("rejected","declined")],
        fmt_dt=_fmt_dt, total=len(data), pending_count=pending_count,
        active_page="requests",
    )


@admin_bp.route("/requests/<req_id>/approve", methods=["POST"])
@_require_admin
def approve_request(req_id):
    try:
        sb = _sb()
        result = sb.table("access_requests").select("*").eq("id", req_id).execute()
        if not result.data:
            flash("Request not found.", "error")
            return redirect("/requests")
        req = result.data[0]
        phone_e164 = _normalize_phone(req.get("phone",""))
        existing = sb.table("clients").select("id").eq("phone", phone_e164).execute()
        if existing.data:
            flash(f"Client with {phone_e164} already exists.", "warning")
            sb.table("access_requests").update({"status":"approved","approved_at":datetime.utcnow().isoformat()}).eq("id",req_id).execute()
            return redirect("/requests")
        business_name = request.form.get("business_name") or f"{(req.get('name') or '').split()[0]}'s {req.get('business_type','Business')}"
        owner_name  = req.get("name","")
        owner_email = req.get("email","")
        new_client = {
            "business_name": business_name, "owner_name": owner_name,
            "phone": phone_e164, "active": True,
            "trade_vertical": _map_vertical(req.get("business_type","")),
            "created_at": datetime.utcnow().isoformat(),
        }
        sb.table("clients").insert(new_client).execute()
        sb.table("access_requests").update({"status":"approved","approved_at":datetime.utcnow().isoformat()}).eq("id",req_id).execute()
        if owner_email:
            try:
                from execution.resend_agent import send_welcome_email
                send_welcome_email(name=owner_name, email=owner_email, business_name=business_name, phone=phone_e164)
            except Exception as e:
                print(f"[{_ts()}] WARN admin: welcome email failed — {e}")
        flash(f"✓ {business_name} is live. Welcome email sent to {owner_email}.", "success")
    except Exception as e:
        flash(f"Error: {e}", "error")
    return redirect("/requests")


@admin_bp.route("/requests/<req_id>/reject", methods=["POST"])
@_require_admin
def reject_request(req_id):
    try:
        _sb().table("access_requests").update({"status":"rejected"}).eq("id",req_id).execute()
        flash("Request rejected.", "info")
    except Exception as e:
        flash(f"Error: {e}", "error")
    return redirect("/requests")


@admin_bp.route("/requests/<req_id>/contact", methods=["POST"])
@_require_admin
def mark_contacted(req_id):
    try:
        _sb().table("access_requests").update({"status":"contacted","contacted_at":datetime.utcnow().isoformat()}).eq("id",req_id).execute()
        flash("Marked as contacted.", "success")
    except Exception as e:
        flash(f"Error: {e}", "error")
    return redirect("/requests")


@admin_bp.route("/clients")
@_require_admin
def clients_list():
    try:
        sb = _sb()
        clients = sb.table("clients").select("id,business_name,owner_name,phone,active,trade_vertical,created_at").order("created_at",desc=True).execute().data or []
        jobs_result = sb.table("jobs").select("client_phone").execute().data or []
        counts = {}
        for j in jobs_result:
            p = j.get("client_phone","")
            counts[p] = counts.get(p,0) + 1
    except Exception as e:
        print(f"[{_ts()}] ERROR admin: {e}")
        clients, counts = [], {}
    pending_count = 0
    try:
        pending_count = len(_sb().table("access_requests").select("id").eq("status","pending").execute().data or [])
    except Exception:
        pass
    return render_template("admin_clients.html",
        clients=clients, counts=counts, fmt_dt=_fmt_dt,
        total=len(clients), active_count=sum(1 for c in clients if c.get("active")),
        pending_count=pending_count, active_page="clients",
    )


@admin_bp.route("/clients/<client_id>")
@_require_admin
def client_detail(client_id):
    try:
        sb = _sb()
        result = sb.table("clients").select("*").eq("id",client_id).execute()
        if not result.data:
            flash("Client not found.","error"); return redirect("/clients")
        client = result.data[0]
        phone  = client.get("phone","")
        activity = sb.table("agent_activity").select("*").eq("client_phone",phone).order("created_at",desc=True).limit(50).execute().data or []
        jobs = sb.table("jobs").select("id,created_at,agent_used").eq("client_phone",phone).order("created_at",desc=True).limit(20).execute().data or []
        try:
            sms = sb.table("sms_message_log").select("id").eq("client_phone",phone).execute().data or []
        except Exception:
            sms = []
        cost_estimate = _estimate_costs(activity)
        pending_count = len(sb.table("access_requests").select("id").eq("status","pending").execute().data or [])
    except Exception as e:
        flash(f"Error: {e}","error"); return redirect("/clients")
    return render_template("admin_client_detail.html",
        client=client, activity=activity, jobs=jobs,
        sms_count=len(sms), cost_estimate=cost_estimate,
        fmt_dt=_fmt_dt, pending_count=pending_count, active_page="clients",
    )


@admin_bp.route("/clients/<client_id>/resend-welcome", methods=["POST"])
@_require_admin
def resend_welcome(client_id):
    try:
        sb = _sb()
        result = sb.table("clients").select("*").eq("id",client_id).execute()
        if not result.data:
            flash("Client not found.","error"); return redirect("/clients")
        client = result.data[0]
        email = request.form.get("email","").strip()
        if not email:
            flash("Email address required.","error"); return redirect(f"/clients/{client_id}")
        from execution.resend_agent import send_welcome_email
        r = send_welcome_email(name=client.get("owner_name",""), email=email,
            business_name=client.get("business_name",""), phone=client.get("phone",""))
        flash(f"✓ Welcome email sent to {email}" if r.get("success") else f"Email failed: {r.get('error')}", "success" if r.get("success") else "error")
    except Exception as e:
        flash(f"Error: {e}","error")
    return redirect(f"/clients/{client_id}")


@admin_bp.route("/clients/<client_id>/toggle-active", methods=["POST"])
@_require_admin
def toggle_active(client_id):
    try:
        sb = _sb()
        result = sb.table("clients").select("active,phone,business_name").eq("id",client_id).execute()
        if not result.data:
            flash("Client not found.","error"); return redirect("/clients")
        current = result.data[0].get("active",True)
        sb.table("clients").update({"active": not current}).eq("id",client_id).execute()
        _admin_audit(sb, result.data[0].get("phone",""),
                     "pause_client" if current else "resume_client",
                     f"client_id={client_id[:8]} business={result.data[0].get('business_name','')}")
        flash(f"Client {'paused' if current else 'resumed'}.", "success")
    except Exception as e:
        flash(f"Error: {e}","error")
    return redirect(f"/clients/{client_id}")


# ---------------------------------------------------------------------------
# Admin audit logging helper — every destructive/communication action
# lands in agent_activity with agent_name="admin" for an immutable trail.
# ---------------------------------------------------------------------------

def _admin_audit(sb, client_phone: str, action: str, summary: str) -> None:
    """
    Append-only audit record for every admin action. Failures here must
    NEVER block the action itself — log and swallow.
    """
    try:
        sb.table("agent_activity").insert({
            "client_phone": client_phone or "",
            "agent_name":   "admin",
            "action_taken": action,
            "input_summary": summary[:500],
            "output_summary": f"by admin_pin at {datetime.utcnow().isoformat()}",
            "sms_sent":     False,
        }).execute()
    except Exception as e:
        print(f"[{_ts()}] WARN admin: audit log write failed — {e}")


# ---------------------------------------------------------------------------
# Tables to cascade when a client is deleted. Order matters — children
# first, client last. Most are multi-tenant on client_id; the legacy
# ones (agent_activity, needs_attention) use client_phone.
#
# A missing table on this list does NOT block the delete — if Supabase
# returns an error, we log and continue. The final client row delete
# proceeds regardless, but the admin gets a summary of what succeeded
# vs. what errored so they can follow up manually.
# ---------------------------------------------------------------------------

_CASCADE_TABLES_BY_CLIENT_ID = [
    "draft_corrections", "job_photos", "invoice_drafts", "job_extended_data",
    "pwa_chat_messages", "pwa_tokens",
    "route_assignments", "route_tokens", "dispatch_decisions",
    "time_entries", "time_bank",
    "follow_ups", "estimate_edits", "client_prompt_overrides",
    "job_pricing_history", "estimate_sessions",
    "proposals", "invoices", "lost_jobs",
    "jobs",
    "customers", "employees", "pricebook_items",
    "sms_message_log", "webhook_log",
]
_CASCADE_TABLES_BY_CLIENT_PHONE = ["agent_activity", "needs_attention"]


@admin_bp.route("/clients/<client_id>/delete", methods=["POST"])
@_require_admin
def delete_client(client_id):
    """
    Hard delete a client + every child row they own.

    Guard: the admin must type the client's exact business name into
    the confirm_name field. Prevents fat-finger deletes of the wrong
    tenant. Cascades through every table listed above; per-table
    failures are logged but don't abort the cascade.
    """
    sb = _sb()
    try:
        result = sb.table("clients").select(
            "id,business_name,phone,owner_name"
        ).eq("id", client_id).execute()
        if not result.data:
            flash("Client not found.", "error")
            return redirect("/clients")
        client = result.data[0]
        business_name = client.get("business_name") or ""
        client_phone = client.get("phone") or ""
    except Exception as e:
        flash(f"Error loading client: {e}", "error")
        return redirect("/clients")

    # Guard — the admin must retype the business name exactly
    typed = (request.form.get("confirm_name") or "").strip()
    if typed != business_name:
        flash(
            f"Confirmation mismatch — type the business name exactly: "
            f'"{business_name}". Nothing was deleted.',
            "error",
        )
        return redirect(f"/clients/{client_id}")

    # Cascade through child tables first
    errors = []
    total_rows_deleted = 0
    for table in _CASCADE_TABLES_BY_CLIENT_ID:
        try:
            # PostgREST requires a filter on deletes; use eq client_id
            res = sb.table(table).delete().eq("client_id", client_id).execute()
            n = len(res.data or []) if hasattr(res, "data") else 0
            total_rows_deleted += n
        except Exception as e:
            errors.append(f"{table}: {str(e)[:80]}")

    for table in _CASCADE_TABLES_BY_CLIENT_PHONE:
        if not client_phone:
            continue
        try:
            res = sb.table(table).delete().eq("client_phone", client_phone).execute()
            n = len(res.data or []) if hasattr(res, "data") else 0
            total_rows_deleted += n
        except Exception as e:
            errors.append(f"{table}: {str(e)[:80]}")

    # Finally delete the client row itself
    try:
        sb.table("clients").delete().eq("id", client_id).execute()
    except Exception as e:
        flash(f"Child rows cleaned but client row delete failed: {e}", "error")
        return redirect(f"/clients/{client_id}")

    _admin_audit(
        sb, client_phone, "delete_client",
        f"client_id={client_id[:8]} business={business_name!r} "
        f"rows_deleted={total_rows_deleted} errors={len(errors)}"
    )

    msg = f"✓ Deleted {business_name} ({total_rows_deleted} child rows removed)"
    if errors:
        msg += f" — {len(errors)} cascade warnings (check logs)"
        print(f"[{_ts()}] WARN admin: delete_client cascade errors — {errors}")
    flash(msg, "success" if not errors else "warning")
    return redirect("/clients")


@admin_bp.route("/clients/<client_id>/reset-pin", methods=["POST"])
@_require_admin
def reset_pin(client_id):
    """
    Generate a fresh 4-digit PIN, hash it with werkzeug, write to
    clients.pin_hash, and email the PLAINTEXT PIN to the owner's email
    via Resend. The owner is expected to change it on first sign-in
    via the existing /set-pin flow.

    The plaintext PIN NEVER appears in the HTTP response or the flash
    message — it only exists on the wire to Resend, then in the
    owner's inbox. Admin sees confirmation that the email went out.
    """
    import secrets
    try:
        sb = _sb()
        result = sb.table("clients").select(
            "id,business_name,owner_name,phone"
        ).eq("id", client_id).execute()
        if not result.data:
            flash("Client not found.", "error")
            return redirect("/clients")
        client = result.data[0]
    except Exception as e:
        flash(f"Error: {e}", "error")
        return redirect("/clients")

    # The email is provided by the form (admin pastes the owner's email
    # from the client detail page — no column on clients table holds it)
    owner_email = (request.form.get("email") or "").strip()
    if not owner_email:
        flash("Owner email required to send the new PIN.", "error")
        return redirect(f"/clients/{client_id}")

    # Mint a fresh 4-digit PIN using secrets.randbelow (CSPRNG) instead
    # of random.randint (seeded PRNG). 4-digit space is small enough
    # that attackers care about entropy of each issuance.
    new_pin = f"{secrets.randbelow(10000):04d}"
    pin_hash = generate_password_hash(new_pin)

    try:
        sb.table("clients").update({"pin_hash": pin_hash}).eq("id", client_id).execute()
    except Exception as e:
        flash(f"PIN hash write failed: {e}", "error")
        return redirect(f"/clients/{client_id}")

    # Send the plaintext PIN via Resend. Swallow failures — admin can
    # retry the send; the hash is already updated so the owner can't
    # log in with the old PIN regardless.
    try:
        from execution.resend_agent import send_pin_reset_email
        result = send_pin_reset_email(
            to_email=owner_email,
            owner_name=client.get("owner_name", ""),
            business_name=client.get("business_name", ""),
            phone=client.get("phone", ""),
            new_pin=new_pin,
        )
        if result.get("success"):
            _admin_audit(sb, client.get("phone", ""), "reset_pin",
                         f"client_id={client_id[:8]} emailed to={owner_email}")
            flash(f"✓ New PIN sent to {owner_email}. Old PIN is disabled.", "success")
        else:
            flash(f"PIN updated but email failed: {result.get('error')}. "
                  f"Try again or send the PIN manually.", "warning")
    except Exception as e:
        flash(f"PIN updated but email send crashed: {e}", "error")

    return redirect(f"/clients/{client_id}")


@admin_bp.route("/clients/<client_id>/send-reminder", methods=["POST"])
@_require_admin
def send_reminder(client_id):
    """
    Send an ad-hoc reminder email from the admin console. Subject +
    body come straight from the admin's form; no templates, no
    interpolation beyond the greeting. Used for one-off outreach
    ("your trial is ending", "we noticed you haven't logged in").
    """
    try:
        sb = _sb()
        result = sb.table("clients").select(
            "business_name,owner_name,phone"
        ).eq("id", client_id).execute()
        if not result.data:
            flash("Client not found.", "error")
            return redirect("/clients")
        client = result.data[0]
    except Exception as e:
        flash(f"Error: {e}", "error")
        return redirect("/clients")

    owner_email = (request.form.get("email") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    message_body = (request.form.get("message") or "").strip()

    if not owner_email or not subject or not message_body:
        flash("Email, subject, and message are all required.", "error")
        return redirect(f"/clients/{client_id}")

    try:
        from execution.resend_agent import send_admin_reminder_email
        result = send_admin_reminder_email(
            to_email=owner_email,
            owner_name=client.get("owner_name", ""),
            business_name=client.get("business_name", ""),
            subject=subject,
            message_body=message_body,
        )
        if result.get("success"):
            _admin_audit(sb, client.get("phone", ""), "send_reminder",
                         f"client_id={client_id[:8]} to={owner_email} subject={subject[:60]!r}")
            flash(f"✓ Reminder sent to {owner_email}.", "success")
        else:
            flash(f"Send failed: {result.get('error')}", "error")
    except Exception as e:
        flash(f"Send crashed: {e}", "error")

    return redirect(f"/clients/{client_id}")


@admin_bp.route("/costs")
@_require_admin
def costs():
    try:
        sb = _sb()
        activity = sb.table("agent_activity").select("client_phone,agent_name,action_taken,created_at,output_summary").order("created_at",desc=True).limit(1000).execute().data or []
        clients_result = sb.table("clients").select("phone,business_name").execute().data or []
        client_map = {c["phone"]: c["business_name"] for c in clients_result}
        per_client = {}
        for row in activity:
            phone = row.get("client_phone","unknown")
            if phone not in per_client:
                per_client[phone] = {"name":client_map.get(phone,phone),"phone":phone,"haiku_calls":0,"sonnet_calls":0,"total_actions":0,"est_cost_usd":0.0}
            agent = row.get("agent_name","")
            per_client[phone]["total_actions"] += 1
            if "haiku" in agent.lower() or "classification" in agent.lower():
                per_client[phone]["haiku_calls"]  += 1
                per_client[phone]["est_cost_usd"] += 0.0008
            elif any(x in agent.lower() for x in ["invoice","proposal","sonnet"]):
                per_client[phone]["sonnet_calls"] += 1
                per_client[phone]["est_cost_usd"] += 0.006
            else:
                per_client[phone]["est_cost_usd"] += 0.001
        ranked = sorted(per_client.values(), key=lambda x: x["est_cost_usd"], reverse=True)
        total_cost = sum(c["est_cost_usd"] for c in ranked)
        pending_count = len(sb.table("access_requests").select("id").eq("status","pending").execute().data or [])
    except Exception as e:
        print(f"[{_ts()}] ERROR admin: costs — {e}")
        ranked, total_cost, pending_count = [], 0.0, 0
    return render_template("admin_costs.html",
        clients=ranked, total_cost=total_cost, fmt_dt=_fmt_dt,
        pending_count=pending_count, active_page="costs",
    )
