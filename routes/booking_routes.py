"""
booking_routes.py — Flask Blueprint for public class booking

Blueprint: booking_bp
Routes:
    GET  /book/<board_token>       — Public booking page (no login)
    POST /api/book/lookup-customer — Phone → returning customer check
    POST /api/book/create          — Book a slot (capacity check, SMS confirm)
    POST /api/book/waitlist        — Join waitlist for full slot
"""

import os
import sys
import re
from datetime import datetime, timezone, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, request, jsonify, render_template

booking_bp = Blueprint("booking_bp", __name__)


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_supabase():
    from execution.db_connection import get_client
    return get_client()


def _normalize_phone(raw: str) -> str:
    """Strip non-digits and normalize to E.164 (+1XXXXXXXXXX)."""
    digits = re.sub(r'\D', '', raw)
    if len(digits) == 10:
        digits = '1' + digits
    if len(digits) == 11 and digits[0] == '1':
        return '+' + digits
    if digits and not digits.startswith('+'):
        return '+' + digits
    return raw.strip()


# ---------------------------------------------------------------------------
# GET /book/<board_token> — Public booking page
# ---------------------------------------------------------------------------

@booking_bp.route("/book/<board_token>")
def booking_page(board_token):
    """Public class booking page. No login, no sidebar. Mobile-first."""
    sb = _get_supabase()

    # Look up board
    try:
        result = sb.table("class_boards").select("*").eq("token", board_token).execute()
        if not result.data:
            return render_template("error.html",
                title="Not Found",
                message="This booking page doesn't exist.",
                sub="Check with the business for the correct link.",
            ), 404
        board = result.data[0]
    except Exception as e:
        print(f"[{timestamp()}] ERROR booking: board lookup failed — {e}")
        return render_template("error.html",
            title="Error",
            message="Something went wrong.",
            sub="Please try again.",
        ), 500

    client_phone = board.get("client_phone", "")

    # Load business name from clients table
    business_name = "Bolts11"
    try:
        c = sb.table("clients").select("business_name").eq("phone", client_phone).execute()
        if c.data:
            business_name = c.data[0].get("business_name", "Bolts11")
    except Exception:
        pass

    # Load upcoming open slots (next 14 days)
    today_str = date.today().isoformat()
    future_14 = (date.today() + timedelta(days=14)).isoformat()
    slots = []
    try:
        result = sb.table("class_slots").select(
            "id, title, slot_date, start_time, end_time, capacity, "
            "enrolled_count, instructor, description, status"
        ).eq("client_phone", client_phone).gte(
            "slot_date", today_str
        ).lte("slot_date", future_14).eq("status", "open").order(
            "slot_date"
        ).order("start_time").execute()
        slots = result.data or []
    except Exception as e:
        print(f"[{timestamp()}] WARN booking: slots query failed — {e}")

    return render_template("book.html",
        board=board,
        board_token=board_token,
        business_name=business_name,
        client_phone=client_phone,
        slots=slots,
    )


# ---------------------------------------------------------------------------
# POST /api/book/lookup-customer — returning customer check
# ---------------------------------------------------------------------------

@booking_bp.route("/api/book/lookup-customer", methods=["POST"])
def lookup_customer():
    """Check if a phone number belongs to a returning customer."""
    data = request.get_json(silent=True) or {}
    phone_raw = (data.get("phone") or "").strip()
    client_phone = (data.get("client_phone") or "").strip()

    if not phone_raw:
        return jsonify({"found": False, "error": "Phone required"}), 400

    phone = _normalize_phone(phone_raw)
    sb = _get_supabase()

    # Look up client_id from client_phone
    client_id = None
    try:
        cr = sb.table("clients").select("id").eq("phone", client_phone).execute()
        if cr.data:
            client_id = cr.data[0]["id"]
    except Exception:
        pass

    if not client_id:
        return jsonify({"found": False})

    try:
        result = sb.table("customers").select(
            "id, customer_name, customer_phone"
        ).eq("client_id", client_id).eq("customer_phone", phone).limit(1).execute()
        if result.data:
            cust = result.data[0]
            return jsonify({
                "found": True,
                "customer_id": cust["id"],
                "customer_name": cust.get("customer_name", ""),
            })
    except Exception as e:
        print(f"[{timestamp()}] WARN booking: customer lookup failed — {e}")

    return jsonify({"found": False})


# ---------------------------------------------------------------------------
# POST /api/book/create — book a slot
# ---------------------------------------------------------------------------

@booking_bp.route("/api/book/create", methods=["POST"])
def create_booking():
    """
    Book a class slot. Checks capacity, creates enrollment,
    sends confirmation SMS to customer + notification to instructor.
    """
    data = request.get_json(silent=True) or {}
    slot_id = data.get("slot_id")
    phone_raw = (data.get("phone") or "").strip()
    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()
    customer_id = data.get("customer_id")
    sms_consent = data.get("sms_consent", False)
    client_phone = (data.get("client_phone") or "").strip()

    if not slot_id:
        return jsonify({"success": False, "error": "Slot is required"}), 400
    if not phone_raw:
        return jsonify({"success": False, "error": "Phone number is required"}), 400

    phone = _normalize_phone(phone_raw)
    full_name = f"{first_name} {last_name}".strip() or "Customer"
    sb = _get_supabase()

    # Load slot and check capacity
    try:
        slot = sb.table("class_slots").select("*").eq("id", slot_id).execute()
        if not slot.data:
            return jsonify({"success": False, "error": "Slot not found"}), 404
        slot = slot.data[0]
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    capacity = slot.get("capacity", 10)
    enrolled = slot.get("enrolled_count", 0)
    if enrolled >= capacity:
        return jsonify({"success": False, "error": "full", "message": "This class is full."}), 409

    # Look up or create customer
    client_id = None
    try:
        cr = sb.table("clients").select("id").eq("phone", client_phone).execute()
        if cr.data:
            client_id = cr.data[0]["id"]
    except Exception:
        pass

    if not customer_id and client_id:
        # Check if customer exists
        try:
            existing = sb.table("customers").select("id").eq(
                "client_id", client_id
            ).eq("customer_phone", phone).limit(1).execute()
            if existing.data:
                customer_id = existing.data[0]["id"]
        except Exception:
            pass

        if not customer_id:
            # Create new customer
            try:
                now = datetime.now(timezone.utc).isoformat()
                new_cust = sb.table("customers").insert({
                    "client_id": client_id,
                    "customer_name": full_name,
                    "customer_phone": phone,
                    "sms_consent": bool(sms_consent),
                    "sms_consent_at": now if sms_consent else None,
                    "sms_consent_src": "web_form" if sms_consent else None,
                }).execute()
                if new_cust.data:
                    customer_id = new_cust.data[0]["id"]
            except Exception as e:
                print(f"[{timestamp()}] WARN booking: customer create failed — {e}")

    # Update sms_consent if returning customer opted in
    if customer_id and sms_consent:
        try:
            sb.table("customers").update({
                "sms_consent": True,
                "sms_consent_at": datetime.now(timezone.utc).isoformat(),
                "sms_consent_src": "web_form",
            }).eq("id", customer_id).execute()
        except Exception:
            pass

    # Insert enrollment
    try:
        sb.table("class_enrollments").insert({
            "slot_id": slot_id,
            "customer_id": customer_id,
            "customer_name": full_name,
            "customer_phone": phone,
            "status": "enrolled",
            "booked_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        print(f"[{timestamp()}] ERROR booking: enrollment insert failed — {e}")
        return jsonify({"success": False, "error": "Booking failed"}), 500

    # Increment enrolled_count
    try:
        sb.table("class_slots").update({
            "enrolled_count": enrolled + 1,
        }).eq("id", slot_id).execute()
    except Exception as e:
        print(f"[{timestamp()}] WARN booking: enrolled_count update failed — {e}")

    # Send booking confirmation SMS to customer
    if sms_consent and phone:
        try:
            from execution.sms_send import send_sms
            slot_title = slot.get("title", "Class")
            slot_date = slot.get("slot_date", "")
            slot_time = slot.get("start_time", "")
            send_sms(
                to_number=phone,
                message_body=(
                    f"You're booked! {slot_title} on {slot_date} at {slot_time}.\n"
                    f"Reply STOP to unsubscribe."
                ),
                from_number=client_phone,
                message_type="booking_confirm",
            )
        except Exception as e:
            print(f"[{timestamp()}] WARN booking: confirmation SMS failed — {e}")

    # Notify instructor if set
    instructor_phone = slot.get("instructor_phone")
    if instructor_phone:
        try:
            from execution.sms_send import send_sms
            send_sms(
                to_number=instructor_phone,
                message_body=f"New booking: {full_name} for {slot.get('title', 'class')} on {slot.get('slot_date', '')}.",
                from_number=client_phone,
                message_type="booking_confirm",
            )
        except Exception:
            pass

    # Build calendar link
    cal_date = slot.get("slot_date", "").replace("-", "")
    cal_start = (slot.get("start_time") or "0800").replace(":", "")
    cal_end = (slot.get("end_time") or "0900").replace(":", "")
    cal_url = (
        f"https://www.google.com/calendar/render?action=TEMPLATE"
        f"&text={slot.get('title', 'Class')}"
        f"&dates={cal_date}T{cal_start}00/{cal_date}T{cal_end}00"
    )

    print(f"[{timestamp()}] INFO booking: {full_name} booked {slot.get('title', 'slot')} on {slot.get('slot_date', '')}")

    return jsonify({
        "success": True,
        "slot_title": slot.get("title", ""),
        "slot_date": slot.get("slot_date", ""),
        "slot_time": slot.get("start_time", ""),
        "calendar_url": cal_url,
    })


# ---------------------------------------------------------------------------
# POST /api/book/waitlist — join waitlist for full slot
# ---------------------------------------------------------------------------

@booking_bp.route("/api/book/waitlist", methods=["POST"])
def join_waitlist():
    """Add customer to waitlist for a full slot."""
    data = request.get_json(silent=True) or {}
    slot_id = data.get("slot_id")
    phone_raw = (data.get("phone") or "").strip()
    name = (data.get("name") or "").strip()

    if not slot_id or not phone_raw:
        return jsonify({"success": False, "error": "Slot and phone required"}), 400

    phone = _normalize_phone(phone_raw)
    sb = _get_supabase()

    try:
        sb.table("class_waitlist").insert({
            "slot_id": slot_id,
            "customer_name": name or "Customer",
            "customer_phone": phone,
            "status": "waiting",
            "added_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        print(f"[{timestamp()}] INFO booking: {name or phone} added to waitlist for slot {slot_id[:8]}")
        return jsonify({"success": True})
    except Exception as e:
        print(f"[{timestamp()}] ERROR booking: waitlist insert failed — {e}")
        return jsonify({"success": False, "error": str(e)}), 500
