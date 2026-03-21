"""
onboarding_routes.py — Flask Blueprint for client onboarding system

Blueprint: onboarding_bp
Routes:
    POST /api/onboarding/create               — Create new client + session
    GET  /onboard/<token>                      — Serve client wizard
    POST /api/onboarding/<token>/save          — Save step progress
    POST /api/onboarding/<token>/complete      — Mark wizard complete
    GET  /api/onboarding/list                  — List all sessions (admin)
    POST /api/onboarding/<token>/approve       — Approve + activate client
    GET  /api/onboarding/pricing-template/<v>  — Pricing template for vertical
    GET  /api/onboarding/specialties/<v>       — Specialties for vertical
"""

import os
import sys
import secrets
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, request, jsonify, send_from_directory

onboarding_bp = Blueprint("onboarding_bp", __name__)

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_supabase():
    from execution.db_connection import get_client
    return get_client()


# ---------------------------------------------------------------------------
# POST /api/onboarding/create — create new client + onboarding session
# ---------------------------------------------------------------------------

@onboarding_bp.route("/api/onboarding/create", methods=["POST"])
def create_onboarding():
    """
    Create a new client row (status=pending_setup) and an onboarding session.
    Returns the setup token and URL.
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        client_name = data.get("client_name", "").strip()
        telnyx_phone = data.get("telnyx_phone", "").strip()
        owner_name = data.get("owner_name", "").strip()
        owner_mobile = data.get("owner_mobile", "").strip()
        trade_vertical = data.get("trade_vertical", "").strip()
        admin_note = data.get("admin_note", "").strip()

        if not client_name:
            return jsonify({"success": False, "error": "Business name is required"}), 400

        supabase = _get_supabase()

        # Create client row with pending_setup status
        client_record = {
            "business_name": client_name,
            "owner_name": owner_name or None,
            "phone": telnyx_phone or None,
            "owner_mobile": owner_mobile or None,
            "trade_vertical": trade_vertical or None,
            "active": False,
        }
        client_result = supabase.table("clients").insert(client_record).execute()
        if not client_result.data:
            return jsonify({"success": False, "error": "Failed to create client"}), 500

        client_id = client_result.data[0]["id"]

        # Generate unique token for the onboarding session
        token = secrets.token_urlsafe(24)

        session_record = {
            "client_id": client_id,
            "token": token,
            "status": "pending",
            "company_name": client_name,
            "owner_name": owner_name or None,
            "owner_mobile": owner_mobile or None,
            "trade_vertical": trade_vertical or None,
        }
        session_result = supabase.table("onboarding_sessions").insert(session_record).execute()
        if not session_result.data:
            return jsonify({"success": False, "error": "Failed to create onboarding session"}), 500

        base_url = request.host_url.rstrip('/')
        onboarding_url = f"{base_url}/onboard/{token}"

        print(f"[{timestamp()}] INFO onboarding: Created session for {client_name} → {onboarding_url}")

        return jsonify({
            "success": True,
            "client_id": client_id,
            "token": token,
            "onboarding_url": onboarding_url,
        })

    except Exception as e:
        print(f"[{timestamp()}] ERROR onboarding: create failed — {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# GET /onboard/<token> — serve the client wizard HTML
# ---------------------------------------------------------------------------

@onboarding_bp.route("/onboard/<token>")
def serve_wizard(token):
    """Serve the client-facing onboarding wizard."""
    try:
        supabase = _get_supabase()
        result = supabase.table("onboarding_sessions").select("id, status, expires_at").eq("token", token).execute()

        if not result.data:
            return "<h2>This setup link is invalid.</h2><p>Please contact your Bolts11 representative.</p>", 404

        session = result.data[0]
        expires_at_str = session.get("expires_at", "")
        try:
            expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > expires_at:
                return "<h2>This setup link has expired.</h2><p>Please contact your Bolts11 representative for a new link.</p>", 410
        except (ValueError, TypeError):
            pass

        dashboard_dir = os.path.join(_project_root, "dashboard")
        return send_from_directory(dashboard_dir, "onboard_wizard.html")

    except Exception as e:
        print(f"[{timestamp()}] ERROR onboarding: serve_wizard failed — {e}")
        return "<h2>Something went wrong.</h2><p>Please contact your Bolts11 representative.</p>", 500


# ---------------------------------------------------------------------------
# GET /api/onboarding/<token>/data — get session data for resume
# ---------------------------------------------------------------------------

@onboarding_bp.route("/api/onboarding/<token>/data", methods=["GET"])
def get_session_data(token):
    """Return all saved data for a session so the wizard can resume."""
    try:
        supabase = _get_supabase()
        result = supabase.table("onboarding_sessions").select("*").eq("token", token).execute()
        if not result.data:
            return jsonify({"success": False, "error": "Session not found"}), 404
        return jsonify({"success": True, "session": result.data[0]})
    except Exception as e:
        print(f"[{timestamp()}] ERROR onboarding: get_session_data failed — {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# POST /api/onboarding/<token>/save — save step progress
# ---------------------------------------------------------------------------

@onboarding_bp.route("/api/onboarding/<token>/save", methods=["POST"])
def save_step(token):
    """
    Save progress from any step. Only updates fields present in the data —
    never nulls out fields from other steps.
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        step = data.get("step", 1)
        step_data = data.get("data", {})

        supabase = _get_supabase()

        # Verify session exists
        session = supabase.table("onboarding_sessions").select("id, step_reached").eq("token", token).execute()
        if not session.data:
            return jsonify({"success": False, "error": "Session not found"}), 404

        session_id = session.data[0]["id"]
        current_step = session.data[0].get("step_reached", 1)

        # Build update from provided fields only
        update = {
            "last_activity_at": datetime.now(timezone.utc).isoformat(),
            "status": "in_progress",
        }

        # Advance step_reached to the NEXT step (step just completed + 1)
        next_step = step + 1
        if next_step > current_step:
            update["step_reached"] = next_step

        # Map step data to columns
        field_map = {
            1: ["company_name", "owner_name", "owner_email", "owner_mobile",
                "company_address", "company_city", "company_state", "company_zip",
                "company_phone", "years_in_business"],
            2: ["trade_vertical", "trade_specialties", "service_radius_miles", "service_area_desc"],
            3: ["tone_preference", "customer_type", "pricing_style", "tagline", "how_they_found_us"],
            4: ["employees_json"],
            5: ["pricing_json"],
            6: ["logo_url"],
        }

        allowed = field_map.get(step, [])
        for key in allowed:
            if key in step_data:
                update[key] = step_data[key]

        supabase.table("onboarding_sessions").update(update).eq("id", session_id).execute()

        print(f"[{timestamp()}] INFO onboarding: Saved step {step} for token={token[:12]}...")
        return jsonify({"success": True, "next_step": step + 1})

    except Exception as e:
        print(f"[{timestamp()}] ERROR onboarding: save_step failed — {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# POST /api/onboarding/<token>/complete — finish wizard
# ---------------------------------------------------------------------------

@onboarding_bp.route("/api/onboarding/<token>/complete", methods=["POST"])
def complete_onboarding(token):
    """Mark wizard as complete, generate personality MD, notify admin."""
    try:
        supabase = _get_supabase()
        session_result = supabase.table("onboarding_sessions").select("*").eq("token", token).execute()
        if not session_result.data:
            return jsonify({"success": False, "error": "Session not found"}), 404

        session = session_result.data[0]
        now = datetime.now(timezone.utc).isoformat()

        # Generate personality MD via Claude
        personality_md = _generate_personality_md(session)

        # Update session
        supabase.table("onboarding_sessions").update({
            "status": "completed",
            "completed_at": now,
            "last_activity_at": now,
            "step_reached": 6,
            "personality_md": personality_md,
        }).eq("token", token).execute()

        # Notify admin via SMS (if SMS active)
        company = session.get("company_name", "A new client")
        try:
            from execution.sms_send import send_sms
            owner_phone = os.environ.get("ADMIN_PHONE", "")
            base_url = request.host_url.rstrip('/')
            send_sms(
                to_number=owner_phone,
                message_body=f"{company} completed their onboarding setup. Review at: {base_url}/dashboard/onboarding.html",
                from_number=os.environ.get("TELNYX_PHONE_NUMBER", ""),
            )
        except Exception as e:
            print(f"[{timestamp()}] WARN onboarding: SMS notification failed — {e}")

        print(f"[{timestamp()}] INFO onboarding: {company} completed onboarding | personality_md={len(personality_md or '')} chars")
        return jsonify({"success": True})

    except Exception as e:
        print(f"[{timestamp()}] ERROR onboarding: complete failed — {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# GET /api/onboarding/list — list all sessions (admin)
# ---------------------------------------------------------------------------

@onboarding_bp.route("/api/onboarding/list", methods=["GET"])
def list_sessions():
    """Return all onboarding sessions ordered by created_at DESC."""
    try:
        supabase = _get_supabase()
        result = (
            supabase.table("onboarding_sessions")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )

        # Also get telnyx phone from clients table
        sessions = result.data or []
        for s in sessions:
            if s.get("client_id"):
                try:
                    c = supabase.table("clients").select("phone").eq("id", s["client_id"]).execute()
                    s["telnyx_phone"] = c.data[0]["phone"] if c.data else None
                except Exception:
                    s["telnyx_phone"] = None

        return jsonify({"success": True, "sessions": sessions})
    except Exception as e:
        print(f"[{timestamp()}] ERROR onboarding: list failed — {e}")
        return jsonify({"success": True, "sessions": []})


# ---------------------------------------------------------------------------
# POST /api/onboarding/<token>/approve — activate the client
# ---------------------------------------------------------------------------

@onboarding_bp.route("/api/onboarding/<token>/approve", methods=["POST"])
def approve_onboarding(token):
    """Approve the onboarding, activate the client, save personality."""
    try:
        supabase = _get_supabase()
        session_result = supabase.table("onboarding_sessions").select("*").eq("token", token).execute()
        if not session_result.data:
            return jsonify({"success": False, "error": "Session not found"}), 404

        session = session_result.data[0]
        client_id = session.get("client_id")
        now = datetime.now(timezone.utc).isoformat()

        # Check if personality_md was edited in the request
        data = request.get_json(force=True, silent=True) or {}
        personality_md = data.get("personality_md") or session.get("personality_md", "")

        # Update onboarding session
        supabase.table("onboarding_sessions").update({
            "status": "approved",
            "personality_md": personality_md,
            "personality_md_approved": True,
            "last_activity_at": now,
        }).eq("token", token).execute()

        # Activate client + copy personality
        if client_id:
            client_update = {
                "active": True,
                "personality": personality_md,
                "business_name": session.get("company_name"),
                "owner_name": session.get("owner_name"),
                "owner_mobile": session.get("owner_mobile"),
                "trade_vertical": session.get("trade_vertical"),
                "service_area": session.get("service_area_desc"),
            }
            supabase.table("clients").update(client_update).eq("id", client_id).execute()

            # Create employees from session data
            employees = session.get("employees_json") or []
            if isinstance(employees, str):
                try:
                    employees = json.loads(employees)
                except Exception:
                    employees = []
            for emp in employees:
                try:
                    supabase.table("employees").insert({
                        "client_id": client_id,
                        "name": emp.get("name", ""),
                        "phone": emp.get("phone", ""),
                        "role": emp.get("role", "field_tech"),
                        "active": True,
                    }).execute()
                except Exception as e:
                    print(f"[{timestamp()}] WARN onboarding: Employee insert failed — {e}")

        print(f"[{timestamp()}] INFO onboarding: Approved {session.get('company_name')} | client_id={client_id}")
        return jsonify({"success": True})

    except Exception as e:
        print(f"[{timestamp()}] ERROR onboarding: approve failed — {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# GET /api/onboarding/pricing-template/<vertical>
# ---------------------------------------------------------------------------

@onboarding_bp.route("/api/onboarding/pricing-template/<vertical>", methods=["GET"])
def pricing_template(vertical):
    """Return pricing benchmarks for a trade vertical from the database."""
    from execution.db_pricing import get_benchmarks
    services = get_benchmarks(vertical)
    if services:
        return jsonify({"success": True, "services": services})
    # Fallback to hardcoded templates if DB is empty
    from execution.onboarding_templates import get_template
    fallback = get_template(vertical)
    return jsonify({"success": True, "services": [
        {"service_name": t["service"], "price_low": t["low"],
         "price_typical": (t["low"] + t["high"]) / 2,
         "price_high": t["high"], "price_unit": "per job", "notes": None}
        for t in fallback
    ]})


# ---------------------------------------------------------------------------
# GET /api/onboarding/specialties/<vertical>
# ---------------------------------------------------------------------------

@onboarding_bp.route("/api/onboarding/specialties/<vertical>", methods=["GET"])
def specialties(vertical):
    """Return specialty options for a trade vertical from the database."""
    from execution.db_pricing import get_specialties_from_db
    specs = get_specialties_from_db(vertical)
    if specs:
        return jsonify({"success": True, "specialties": specs})
    # Fallback to hardcoded templates if DB is empty
    from execution.onboarding_templates import get_specialties
    return jsonify({"success": True, "specialties": get_specialties(vertical)})


# ---------------------------------------------------------------------------
# GET /api/verticals — all active trade verticals
# ---------------------------------------------------------------------------

@onboarding_bp.route("/api/verticals", methods=["GET"])
def list_verticals():
    """Return all active trade verticals for the wizard trade selection."""
    from execution.db_pricing import get_verticals
    verticals = get_verticals()
    if verticals:
        return jsonify({"success": True, "verticals": verticals})
    # Fallback to hardcoded list
    return jsonify({"success": True, "verticals": [
        {"vertical_key": "septic", "vertical_label": "Septic & Sewer", "icon": "🚽", "sort_order": 1},
        {"vertical_key": "plumbing", "vertical_label": "Plumbing", "icon": "🔧", "sort_order": 2},
        {"vertical_key": "hvac", "vertical_label": "HVAC", "icon": "❄️", "sort_order": 3},
        {"vertical_key": "electrical", "vertical_label": "Electrical", "icon": "⚡", "sort_order": 4},
        {"vertical_key": "excavation", "vertical_label": "Excavation", "icon": "🚜", "sort_order": 5},
        {"vertical_key": "drain", "vertical_label": "Drain Cleaning", "icon": "🌊", "sort_order": 6},
        {"vertical_key": "general", "vertical_label": "General Contracting", "icon": "🔨", "sort_order": 7},
        {"vertical_key": "landscaping", "vertical_label": "Landscaping", "icon": "🌿", "sort_order": 8},
        {"vertical_key": "property_mgmt", "vertical_label": "Property Maintenance", "icon": "🏠", "sort_order": 9},
    ]})


# ---------------------------------------------------------------------------
# Personality MD generation
# ---------------------------------------------------------------------------

def _generate_personality_md(session: dict) -> str:
    """Call Claude to generate the personality MD from wizard answers."""
    try:
        from execution.call_claude import call_claude

        company = session.get("company_name", "Business")
        owner = session.get("owner_name", "Owner")
        vertical = session.get("trade_vertical", "general trades")
        specialties = session.get("trade_specialties") or []
        if isinstance(specialties, str):
            try:
                specialties = json.loads(specialties)
            except Exception:
                specialties = [specialties]
        service_area = session.get("service_area_desc", "")
        city = session.get("company_city", "")
        state = session.get("company_state", "")
        tone = session.get("tone_preference", "professional")
        customer_type = session.get("customer_type", "homeowners")
        pricing_style = session.get("pricing_style", "ballpark estimates")
        tagline = session.get("tagline", "")
        years = session.get("years_in_business", "")

        system_prompt = (
            "You are generating a master context document for an "
            "AI business assistant. This document will be used as "
            "the system prompt for every customer interaction. "
            "Write it in second person ('You are the AI assistant "
            "for...'). Be specific and practical. Match the tone "
            "preference exactly."
        )

        user_prompt = (
            f"Generate a complete personality and context document "
            f"for this trade business AI assistant:\n\n"
            f"Business: {company}\n"
            f"Owner: {owner}\n"
            f"Trade: {vertical}\n"
            f"Specialties: {', '.join(specialties) if specialties else 'General'}\n"
            f"Service Area: {service_area or f'{city}, {state}'}\n"
            f"Location: {city}, {state}\n"
            f"Tone: {tone}\n"
            f"Customer Type: {customer_type}\n"
            f"Pricing Style: {pricing_style}\n"
            f"Tagline: {tagline or 'none'}\n"
            f"Years in Business: {years or 'not specified'}\n\n"
            f"The document should include:\n"
            f"1. Who you are (business identity)\n"
            f"2. Your service area and what you do\n"
            f"3. How you communicate (tone and style)\n"
            f"4. How you handle pricing (based on pricing_style)\n"
            f"5. What you never do (never quote exact prices without "
            f"owner approval, never commit to same-day service, "
            f"never discuss competitors)\n"
            f"6. Emergency escalation (always contact owner for "
            f"any job over $500 or any angry customer)\n\n"
            f"Format as clean markdown. Max 400 words."
        )

        result = call_claude(system_prompt, user_prompt, model="sonnet")
        if result:
            print(f"[{timestamp()}] INFO onboarding: Personality MD generated for {company} ({len(result)} chars)")
            return result
        else:
            print(f"[{timestamp()}] WARN onboarding: Claude returned no personality MD")
            return f"# {company}\n\nPersonality document pending manual creation."

    except Exception as e:
        print(f"[{timestamp()}] ERROR onboarding: _generate_personality_md failed — {e}")
        return f"# {session.get('company_name', 'Business')}\n\nGeneration failed — create manually."
