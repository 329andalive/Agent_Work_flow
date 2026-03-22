"""
command_routes.py — Flask Blueprint for the Command Center API

Blueprint: command_bp
Routes:
    POST /api/command     — Direct agent dispatch from dashboard (bypasses SMS router)
    GET  /api/activity    — Fetch recent agent activity for live feed
    GET  /api/stats       — Fetch sidebar stats (open jobs, clocked-in employees)
    GET  /api/client/config — Return active client config for dashboards
"""

import os
import re
import sys
import traceback
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, request, jsonify, session

command_bp = Blueprint("command_bp", __name__)


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_supabase():
    from execution.db_connection import get_client
    return get_client()


# ---------------------------------------------------------------------------
# POST /api/command — direct agent dispatch, no SMS router
# ---------------------------------------------------------------------------

@command_bp.route("/api/command", methods=["POST"])
def handle_command():
    """
    Command Center endpoint — owner/dispatcher at a dashboard.
    Bypasses SMS router entirely. Direct agent dispatch.
    Authentication: session client_id required (dev fallback allowed).
    """
    # Auth check — session or dev fallback
    client_id = session.get("client_id")
    if not client_id:
        if os.environ.get("FLASK_ENV") == "development":
            try:
                sb = _get_supabase()
                r = sb.table("clients").select("id").eq("active", True).order("created_at").limit(1).execute()
                client_id = r.data[0]["id"] if r.data else None
            except Exception:
                pass
        if not client_id:
            return jsonify({"agent": "error", "status": "error", "message": "Not authenticated"}), 401

    data = request.get_json(silent=True) or {}
    text = (data.get("message") or data.get("payload", {}).get("message") or "").strip()

    if not text:
        return jsonify({"agent": "error", "status": "error", "message": "Empty command"}), 400

    # Load client record
    try:
        sb = _get_supabase()
        client_row = sb.table("clients").select("*").eq("id", client_id).execute()
        if not client_row.data:
            return jsonify({"agent": "error", "status": "error", "message": "Client not found"}), 404
        client = client_row.data[0]
    except Exception as e:
        return jsonify({"agent": "error", "status": "error", "message": f"DB error: {e}"}), 500

    client_phone = client.get("phone", "")
    owner_mobile = client.get("owner_mobile") or client_phone

    print(f"[{timestamp()}] INFO command: client={client_id} input={text[:120]}")

    # ─────────────────────────────────────────
    # DIRECT DISPATCH — no clarification loop
    # ─────────────────────────────────────────
    text_upper = text.upper()
    result = None

    try:
        # ── INVOICE / BILL ──────────────────
        if any(kw in text_upper for kw in
               ["INVOICE", "BILL ", "SEND HER A BILL",
                "SEND HIM A BILL", "BILL FOR"]):
            customer_phone = _resolve_customer_phone(text, client_id)
            from execution.invoice_agent import run as invoice_run
            output = invoice_run(client_phone=client_phone, raw_input=text, customer_phone=customer_phone)
            if output:
                display = '\n'.join(output.split('\n')[:2])
                result = {"agent": "invoice_agent", "status": "ok", "message": display or "Invoice generated and sent."}
            else:
                result = {"agent": "invoice_agent", "status": "error", "message": "Invoice failed — check that a dollar amount was included."}

        # ── ESTIMATE / PROPOSAL ─────────────
        elif any(kw in text_upper for kw in
                 ["ESTIMATE", "PROPOSAL", "QUOTE", "BID"]):
            customer_phone = _resolve_customer_phone(text, client_id)
            from execution.proposal_agent import run as proposal_run
            output = proposal_run(client_phone=client_phone, customer_phone=customer_phone, raw_input=text)
            result = {"agent": "proposal_agent", "status": "ok", "message": output or "Proposal generated."}

        # ── SCHEDULE ────────────────────────
        elif any(kw in text_upper for kw in
                 ["SCHEDULE", "BOOK", "APPOINTMENT"]):
            from execution.scheduling_agent import handle_scheduling
            owner_employee = {"name": client.get("owner_name", "Owner"), "role": "owner", "phone": owner_mobile}
            output = handle_scheduling(client=client, employee=owner_employee, raw_input=text, from_number=owner_mobile)
            result = {"agent": "scheduling_agent", "status": "ok", "message": output or "Job scheduled."}

        # ── CLOCK IN / OUT ──────────────────
        elif any(kw in text_upper for kw in
                 ["CLOCK IN", "CLOCK OUT", "CLOCKIN", "CLOCKOUT",
                  "CLOCKED IN", "CLOCKED OUT"]):
            from execution.clock_agent import handle_clock
            owner_employee = {"name": client.get("owner_name", "Owner"), "role": "owner", "phone": owner_mobile, "id": "owner"}
            output = handle_clock(client=client, employee=owner_employee, raw_input=text, from_number=owner_mobile)
            result = {"agent": "clock_agent", "status": "ok", "message": output or "Clock event recorded."}

        # ── DONE / COMPLETE ─────────────────
        elif any(kw in text_upper for kw in
                 ["DONE", "COMPLETE", "FINISHED", "WRAPPED UP",
                  "ALL DONE", "JOB DONE"]):
            customer_phone = _resolve_customer_phone(text, client_id)
            from execution.invoice_agent import run as invoice_run
            output = invoice_run(client_phone=client_phone, raw_input=text, customer_phone=customer_phone)
            if output:
                display = '\n'.join(output.split('\n')[:2])
                result = {"agent": "invoice_agent", "status": "ok", "message": display or "Job completed and invoice sent."}
            else:
                result = {"agent": "invoice_agent", "status": "error", "message": "Invoice failed — check that a dollar amount was included."}

        # ── EVERYTHING ELSE → Claude classifies and routes
        else:
            result = _interpret_and_route(text=text, client=client, client_phone=client_phone, owner_mobile=owner_mobile, client_id=client_id)

    except Exception as e:
        print(f"[{timestamp()}] ERROR command: {e}")
        traceback.print_exc()
        return jsonify({"agent": "error", "status": "error", "message": f"Something went wrong: {str(e)}"}), 500

    # Log to agent_activity
    try:
        from execution.db_agent_activity import log_activity
        log_activity(
            client_phone=client_phone,
            agent_name=result.get("agent", "command_center"),
            action_taken="command_executed",
            input_summary=text[:120],
            output_summary=(result.get("message") or "")[:120],
            sms_sent=False,
        )
    except Exception:
        pass

    return jsonify(result), 200


def _extract_phone(text: str) -> str | None:
    """Extract a phone number from command text if present."""
    match = re.search(r'(\+?1?\d{10,11})', re.sub(r'[\s\-\(\)]', '', text))
    return match.group(1) if match else None


def _extract_customer_name(text: str) -> str | None:
    """
    Extract customer name from command text.
    Handles: "Invoice Mike Johnson for...", "Bill Sarah Nelson...",
             "Estimate for John Murphy...", "Done at Mike's place..."
    """
    patterns = [
        r'(?:invoice|bill|estimate|proposal|quote)\s+(?:for\s+)?([A-Z][a-z]+\s+[A-Z][a-z]+)',
        r'(?:invoice|bill|estimate|proposal|quote)\s+(?:for\s+)?([A-Z][a-z]+)',
        r'(?:done|complete|finished)\s+(?:at\s+|for\s+)?([A-Z][a-z]+\s+[A-Z][a-z]+)',
        r'(?:done|complete|finished)\s+(?:at\s+|for\s+)?([A-Z][a-z]+)',
        r'(?:schedule|book)\s+(?:for\s+)?([A-Z][a-z]+\s+[A-Z][a-z]+)',
        r'(?:schedule|book)\s+(?:for\s+)?([A-Z][a-z]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            # Filter out common non-name words
            if name.lower() not in ("for", "the", "at", "her", "him", "them", "this"):
                return name
    return None


def _resolve_customer_phone(text: str, client_id: str) -> str | None:
    """
    Try to find a customer phone number from the command text.
    1. Check for an explicit phone number in the text
    2. Try to find a customer by name in the database
    3. Return None — never fall back to owner_mobile
    """
    # Try explicit phone first
    phone = _extract_phone(text)
    if phone:
        return phone

    # Try name lookup
    name = _extract_customer_name(text)
    if name:
        try:
            sb = _get_supabase()
            results = sb.table("customers").select(
                "id, customer_phone, customer_name"
            ).eq("client_id", client_id).ilike(
                "customer_name", f"%{name}%"
            ).limit(1).execute()
            if results.data:
                found = results.data[0]
                print(f"[{timestamp()}] INFO command: Found customer by name: {found['customer_name']} → {found['customer_phone']}")
                return found["customer_phone"]
            else:
                print(f"[{timestamp()}] INFO command: Customer '{name}' not found in DB — will pass None to agent")
        except Exception as e:
            print(f"[{timestamp()}] WARN command: Name lookup failed — {e}")

    return None


def _interpret_and_route(text, client, client_phone, owner_mobile, client_id):
    """
    For ambiguous commands: use Claude Haiku to identify intent
    and call the right agent. No clarification questions — decide and execute.
    """
    from execution.call_claude import call_claude

    classify_prompt = (
        f"You are dispatching commands for a trade business back office.\n"
        f"The owner typed this command:\n\"{text}\"\n\n"
        f"Classify the intent as exactly one of:\n"
        f"INVOICE - creating or sending an invoice/bill\n"
        f"PROPOSAL - creating an estimate or proposal\n"
        f"SCHEDULE - booking or scheduling a job\n"
        f"CLOCK - clock in or clock out event\n"
        f"REPORT - asking for a summary or report\n"
        f"UNKNOWN - cannot determine\n\n"
        f"Reply with ONE WORD only from the list above."
    )

    raw_intent = call_claude(
        "You classify trade business commands into categories. Reply with one word only.",
        classify_prompt,
        model="haiku"
    )
    intent = (raw_intent or "UNKNOWN").strip().upper()
    print(f"[{timestamp()}] INFO command: Haiku classified intent={intent}")

    customer_phone = _resolve_customer_phone(text, client_id)

    if intent == "INVOICE":
        from execution.invoice_agent import run as invoice_run
        output = invoice_run(client_phone=client_phone, raw_input=text, customer_phone=customer_phone)
        if output:
            display = '\n'.join(output.split('\n')[:2])
            return {"agent": "invoice_agent", "status": "ok", "message": display or "Invoice generated."}
        else:
            return {"agent": "invoice_agent", "status": "error", "message": "Invoice failed — check that a dollar amount was included."}

    elif intent == "PROPOSAL":
        from execution.proposal_agent import run as proposal_run
        output = proposal_run(client_phone, customer_phone, text)
        return {"agent": "proposal_agent", "status": "ok", "message": output or "Proposal generated."}

    elif intent == "SCHEDULE":
        from execution.scheduling_agent import handle_scheduling
        owner_employee = {"name": client.get("owner_name", "Owner"), "role": "owner", "phone": owner_mobile}
        output = handle_scheduling(client=client, employee=owner_employee, raw_input=text, from_number=owner_mobile)
        return {"agent": "scheduling_agent", "status": "ok", "message": output or "Job scheduled."}

    elif intent == "CLOCK":
        from execution.clock_agent import handle_clock
        owner_employee = {"name": client.get("owner_name", "Owner"), "role": "owner", "phone": owner_mobile, "id": "owner"}
        output = handle_clock(client=client, employee=owner_employee, raw_input=text, from_number=owner_mobile)
        return {"agent": "clock_agent", "status": "ok", "message": output or "Clock event recorded."}

    elif intent == "REPORT":
        try:
            sb = _get_supabase()
            today_str = date.today().isoformat()
            jobs = sb.table("jobs").select("job_type, status, actual_amount").eq("client_id", client_id).eq("scheduled_date", today_str).execute().data or []
            invoices = sb.table("invoices").select("amount_due, status").eq("client_id", client_id).in_("status", ["sent", "overdue"]).execute().data or []

            report_prompt = (
                f"Write a brief daily summary for {client['business_name']}.\n"
                f"Today's jobs: {jobs}\n"
                f"Open invoices: {invoices}\n"
                f"Keep it under 200 words. Plain text. Sound like a helpful assistant, not a robot."
            )
            summary = call_claude("You write brief daily business summaries.", report_prompt, model="sonnet")
            return {"agent": "report", "status": "ok", "message": summary or "No data to report today."}
        except Exception as e:
            return {"agent": "report", "status": "ok", "message": f"Report generation failed: {e}"}

    else:
        return {
            "agent": "unknown", "status": "ok",
            "message": "I received your message but wasn't sure which action to take. Try starting with: INVOICE, ESTIMATE, SCHEDULE, CLOCK IN, or DONE.",
        }


# ---------------------------------------------------------------------------
# GET /api/activity — recent agent activity for live feed
# ---------------------------------------------------------------------------

@command_bp.route("/api/activity", methods=["GET"])
def api_activity():
    """Return last 10 agent_activity rows for the live feed."""
    try:
        sb = _get_supabase()
        result = (
            sb.table("agent_activity")
            .select("agent_name, action_taken, output_summary, sms_sent, created_at")
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )
        return jsonify({"success": True, "activity": result.data or []})
    except Exception as e:
        print(f"[{timestamp()}] ERROR command_routes: /api/activity failed — {e}")
        return jsonify({"success": True, "activity": []})


# ---------------------------------------------------------------------------
# GET /api/stats — sidebar stats
# ---------------------------------------------------------------------------

@command_bp.route("/api/stats", methods=["GET"])
def api_stats():
    """Return open job count and other sidebar stats."""
    try:
        sb = _get_supabase()
        jobs = (
            sb.table("jobs")
            .select("id", count="exact")
            .not_.in_("status", ["complete", "invoiced", "cancelled"])
            .execute()
        )
        open_jobs = jobs.count if hasattr(jobs, 'count') else len(jobs.data or [])

        return jsonify({
            "success": True,
            "open_jobs": open_jobs,
            "sms_active": bool(os.environ.get("SMS_10DLC_ACTIVE", "")),
            "square_env": os.environ.get("SQUARE_ENVIRONMENT", "sandbox"),
        })
    except Exception as e:
        print(f"[{timestamp()}] ERROR command_routes: /api/stats failed — {e}")
        return jsonify({"success": True, "open_jobs": 0, "sms_active": False, "square_env": "sandbox"})


# ---------------------------------------------------------------------------
# GET /api/client/config — return active client config for dashboards
# ---------------------------------------------------------------------------

@command_bp.route("/api/client/config", methods=["GET"])
def api_client_config():
    """Return the active client's id, telnyx_phone, and owner_mobile."""
    try:
        sb = _get_supabase()
        result = (
            sb.table("clients")
            .select("id, phone, owner_mobile, business_name")
            .eq("active", True)
            .order("created_at")
            .limit(1)
            .execute()
        )
        if result.data:
            c = result.data[0]
            return jsonify({
                "success": True,
                "client_id": c["id"],
                "client_phone": c.get("phone", ""),
                "owner_mobile": c.get("owner_mobile", ""),
                "business_name": c.get("business_name", ""),
                "supabase_url": os.environ.get("SUPABASE_URL", ""),
                "supabase_anon_key": os.environ.get("SUPABASE_ANON_KEY", ""),
            })
        return jsonify({"success": False, "error": "No active client found"})
    except Exception as e:
        print(f"[{timestamp()}] ERROR command_routes: /api/client/config failed — {e}")
        return jsonify({"success": False, "error": str(e)}), 500
