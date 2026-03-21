"""
command_routes.py — Flask Blueprint for the Command Center API

Blueprint: command_bp
Routes:
    POST /api/command     — Execute any agent action from the dashboard
    GET  /api/activity    — Fetch recent agent activity for live feed
    GET  /api/stats       — Fetch sidebar stats (open jobs, clocked-in employees)

The /api/command endpoint constructs a synthetic SMS body from the dashboard
payload and calls route_message() exactly as sms_receive.py does. This means
zero agent changes — the same routing, agents, and business logic run.
"""

import os
import sys
import json
import threading
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, request, jsonify

command_bp = Blueprint("command_bp", __name__)


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# POST /api/command — execute agent actions from dashboard
# ---------------------------------------------------------------------------

@command_bp.route("/api/command", methods=["POST"])
def api_command():
    """
    Execute any agent action from the Command Center dashboard.

    Body:
    {
        "action": "proposal|invoice|schedule|clock|followup|sms|optin|chat",
        "client_phone": "<from /api/client/config>",
        "operator_phone": "<from /api/client/config>",
        "payload": { ... action-specific fields ... }
    }

    For "chat" action, payload.message is treated as if the owner texted it.
    For structured actions, we build a synthetic SMS body and route it.

    Returns:
    {
        "success": true,
        "agent": "proposal_agent",
        "message": "what the system would have sent via SMS",
        "sms_status": "blocked_10dlc"
    }
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        action = data.get("action", "chat")
        client_phone = data.get("client_phone", os.environ.get("TELNYX_PHONE_NUMBER", ""))
        operator_phone = data.get("operator_phone", "")
        payload = data.get("payload", {})

        print(f"[{timestamp()}] INFO command_routes: /api/command action={action}")

        # Build synthetic SMS body based on action type
        if action == "chat":
            sms_body = payload.get("message", "")
        elif action == "proposal":
            parts = []
            if payload.get("customer_name"):
                parts.append(payload["customer_name"])
            if payload.get("address"):
                parts.append(payload["address"])
            if payload.get("job_type"):
                parts.append(payload["job_type"])
            if payload.get("notes"):
                parts.append(payload["notes"])
            sms_body = "ESTIMATE " + " ".join(parts)
        elif action == "invoice":
            parts = []
            if payload.get("customer_name"):
                parts.append(payload["customer_name"])
            if payload.get("hours"):
                parts.append(f"{payload['hours']} hours")
            if payload.get("notes"):
                parts.append(payload["notes"])
            sms_body = "DONE " + " ".join(parts)
        elif action == "schedule":
            parts = []
            if payload.get("customer_name"):
                parts.append(payload["customer_name"])
            if payload.get("address"):
                parts.append(payload["address"])
            if payload.get("date"):
                parts.append(payload["date"])
            if payload.get("time"):
                parts.append(payload["time"])
            if payload.get("notes"):
                parts.append(payload["notes"])
            sms_body = "SCHEDULE " + " ".join(parts)
        elif action == "clock":
            emp_name = payload.get("employee_name", "")
            clock_action = payload.get("clock_action", "in").upper()
            sms_body = f"CLOCK {clock_action} {emp_name}"
        elif action == "followup":
            customer = payload.get("customer_name", "")
            msg = payload.get("message", "")
            sms_body = f"followup {customer} {msg}"
        elif action == "optin":
            phone = payload.get("phone", "")
            sms_body = f"SET OPTIN {phone}"
        elif action == "sms":
            # Direct SMS — don't route, just attempt to send
            to = payload.get("to_number", "")
            msg = payload.get("message", "")
            if to and msg:
                from execution.sms_send import send_sms
                result = send_sms(to_number=to, message_body=msg, from_number=client_phone)
                return jsonify({
                    "success": result.get("success", False),
                    "agent": "direct_sms",
                    "message": msg,
                    "sms_status": "sent" if result.get("success") else "failed",
                    "error": result.get("error"),
                })
            return jsonify({"success": False, "error": "Missing to_number or message"}), 400
        else:
            return jsonify({"success": False, "error": f"Unknown action: {action}"}), 400

        if not sms_body.strip():
            return jsonify({"success": False, "error": "Empty message"}), 400

        # Route through the existing SMS router synchronously
        # Build sms_data dict matching what sms_receive.py produces
        sms_data = {
            "from_number": operator_phone or client_phone,
            "to_number": client_phone,
            "body": sms_body,
            "message_id": f"dashboard-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        }

        # Run route_message synchronously so we can return the result
        from execution.sms_router import route_message
        agent_name = route_message(sms_data)

        # Check SMS status — is 10DLC active?
        sms_active = bool(os.environ.get("SMS_10DLC_ACTIVE", ""))

        # Fetch the most recent agent_activity for this action to get the output
        output_summary = ""
        try:
            from execution.db_connection import get_client as get_supabase
            sb = get_supabase()
            recent = (
                sb.table("agent_activity")
                .select("output_summary, action_taken")
                .eq("agent_name", agent_name)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if recent.data:
                output_summary = recent.data[0].get("output_summary", "")
        except Exception:
            pass

        return jsonify({
            "success": True,
            "agent": agent_name,
            "message": output_summary or f"Routed to {agent_name}",
            "sms_status": "sent" if sms_active else "blocked_10dlc",
            "raw_input": sms_body,
        })

    except Exception as e:
        print(f"[{timestamp()}] ERROR command_routes: /api/command failed — {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# GET /api/activity — recent agent activity for live feed
# ---------------------------------------------------------------------------

@command_bp.route("/api/activity", methods=["GET"])
def api_activity():
    """Return last 10 agent_activity rows for the live feed."""
    try:
        from execution.db_connection import get_client as get_supabase
        sb = get_supabase()
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
        from execution.db_connection import get_client as get_supabase
        sb = get_supabase()

        # Open jobs (not complete/invoiced)
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
    """
    Return the active client's id, telnyx_phone, and owner_mobile.
    Dashboards call this on page load instead of hardcoding values.
    Returns the first active client found.
    """
    try:
        from execution.db_connection import get_client as get_supabase
        sb = get_supabase()
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
