"""
booking_routes.py — Flask Blueprint for public class booking

Blueprint: booking_bp
Routes:
    GET  /book/<board_token>            — Public booking page (no login)
    POST /api/book/lookup-customer      — Phone → returning customer check
    POST /api/book/create               — Book a slot (capacity check, SMS confirm)
    POST /api/book/waitlist             — Join waitlist for full slot
    POST /api/slots/cancel              — Instructor cancels a class
    POST /api/book/cancel               — Customer cancels their booking
    POST /api/admin/run-scheduled-sms   — Trigger scheduled SMS jobs manually
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


# ---------------------------------------------------------------------------
# POST /api/slots/cancel — instructor/admin cancels a class
# ---------------------------------------------------------------------------

@booking_bp.route("/api/slots/cancel", methods=["POST"])
def cancel_slot():
    """
    Cancel a class slot. Notifies all enrolled customers via SMS,
    cancels their bookings, and logs to agent_activity.
    """
    data = request.get_json(silent=True) or {}
    slot_id = data.get("slot_id")
    cancel_reason = (data.get("reason") or "").strip()
    client_phone = (data.get("client_phone") or "").strip()

    if not slot_id:
        return jsonify({"success": False, "error": "slot_id required"}), 400

    sb = _get_supabase()

    # Load slot
    try:
        result = sb.table("class_slots").select("*").eq("id", slot_id).execute()
        if not result.data:
            return jsonify({"success": False, "error": "Slot not found"}), 404
        slot = result.data[0]
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    slot_client_phone = slot.get("client_phone", client_phone)
    slot_title = slot.get("title", "Class")
    slot_date = slot.get("slot_date", "")
    slot_time = slot.get("start_time", "")

    # 1. Set slot status='cancelled'
    try:
        update = {"status": "cancelled"}
        if cancel_reason:
            update["cancel_reason"] = cancel_reason
        sb.table("class_slots").update(update).eq("id", slot_id).execute()
        print(f"[{timestamp()}] INFO booking: Slot {slot_id[:8]} cancelled — {slot_title} on {slot_date}")
    except Exception as e:
        print(f"[{timestamp()}] ERROR booking: slot cancel update failed — {e}")
        return jsonify({"success": False, "error": str(e)}), 500

    # 2. Get all confirmed enrollments
    enrollments = []
    try:
        result = sb.table("class_enrollments").select(
            "id, customer_phone, customer_name, customer_id"
        ).eq("slot_id", slot_id).eq("status", "enrolled").execute()
        enrollments = result.data or []
    except Exception as e:
        print(f"[{timestamp()}] WARN booking: enrollment query failed — {e}")

    # Load business name for SMS
    business_name = "Your service provider"
    try:
        c = sb.table("clients").select("business_name").eq("phone", slot_client_phone).execute()
        if c.data:
            business_name = c.data[0].get("business_name", business_name)
    except Exception:
        pass

    # Build booking URL for rebook link
    board_token = ""
    try:
        board = sb.table("class_boards").select("token").eq("client_phone", slot_client_phone).limit(1).execute()
        if board.data:
            board_token = board.data[0].get("token", "")
    except Exception:
        pass
    base_url = os.environ.get("BOLTS11_BASE_URL", "https://bolts11.com")
    booking_url = f"{base_url}/book/{board_token}" if board_token else ""

    # 3. Send cancellation SMS to each enrolled customer with consent
    sms_sent = 0
    from execution.sms_send import send_sms
    for enr in enrollments:
        cust_phone = enr.get("customer_phone")
        if not cust_phone:
            continue

        # Check sms_consent
        has_consent = False
        if enr.get("customer_id"):
            try:
                cust = sb.table("customers").select("sms_consent").eq("id", enr["customer_id"]).execute()
                if cust.data and cust.data[0].get("sms_consent"):
                    has_consent = True
            except Exception:
                pass

        if has_consent:
            rebook_line = f" Visit {booking_url} to rebook." if booking_url else ""
            try:
                send_sms(
                    to_number=cust_phone,
                    message_body=(
                        f"{business_name}: {slot_title} on {slot_date} at {slot_time} "
                        f"has been cancelled. Sorry for the inconvenience.{rebook_line}"
                    ),
                    from_number=slot_client_phone,
                    message_type="cancellation",
                )
                sms_sent += 1
            except Exception as e:
                print(f"[{timestamp()}] WARN booking: cancellation SMS failed for {cust_phone} — {e}")

    # 4. Update all enrollment statuses to 'cancelled'
    try:
        sb.table("class_enrollments").update({
            "status": "cancelled",
        }).eq("slot_id", slot_id).eq("status", "enrolled").execute()
    except Exception as e:
        print(f"[{timestamp()}] WARN booking: enrollment cancel update failed — {e}")

    # 5. Log to agent_activity
    try:
        from execution.db_agent_activity import log_activity
        log_activity(
            client_phone=slot_client_phone,
            agent_name="booking_system",
            action_taken="slot_cancelled",
            input_summary=f"{slot_title} on {slot_date}",
            output_summary=f"Cancelled — {len(enrollments)} enrolled, {sms_sent} notified",
            sms_sent=sms_sent > 0,
        )
    except Exception:
        pass

    print(f"[{timestamp()}] INFO booking: Slot cancellation complete — {len(enrollments)} enrolled, {sms_sent} SMS sent")

    return jsonify({
        "success": True,
        "enrollments_cancelled": len(enrollments),
        "sms_sent": sms_sent,
    })


# ---------------------------------------------------------------------------
# POST /api/book/cancel — customer cancels their own booking
# ---------------------------------------------------------------------------

@booking_bp.route("/api/book/cancel", methods=["POST"])
def cancel_booking():
    """
    Customer cancels their booking. Decrements slot count, checks
    waitlist, promotes position 1 if available.
    """
    data = request.get_json(silent=True) or {}
    booking_id = data.get("booking_id")
    booking_token = data.get("booking_token")

    if not booking_id and not booking_token:
        return jsonify({"success": False, "error": "booking_id or booking_token required"}), 400

    sb = _get_supabase()

    # 1. Look up booking
    try:
        if booking_token:
            result = sb.table("class_enrollments").select("*").eq("booking_token", booking_token).execute()
        else:
            result = sb.table("class_enrollments").select("*").eq("id", booking_id).execute()

        if not result.data:
            return jsonify({"success": False, "error": "Booking not found"}), 404
        booking = result.data[0]
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    if booking.get("status") == "cancelled":
        return jsonify({"success": True, "message": "Already cancelled"})

    slot_id = booking.get("slot_id")
    customer_phone = booking.get("customer_phone")
    customer_name = booking.get("customer_name", "Customer")

    # 2. Set booking status='cancelled'
    try:
        sb.table("class_enrollments").update({
            "status": "cancelled",
        }).eq("id", booking["id"]).execute()
    except Exception as e:
        print(f"[{timestamp()}] ERROR booking: booking cancel failed — {e}")
        return jsonify({"success": False, "error": str(e)}), 500

    # 3. Decrement slot.enrolled_count
    try:
        slot_result = sb.table("class_slots").select("enrolled_count, client_phone, title, slot_date, start_time").eq("id", slot_id).execute()
        if slot_result.data:
            slot = slot_result.data[0]
            new_count = max(0, (slot.get("enrolled_count", 1)) - 1)
            sb.table("class_slots").update({"enrolled_count": new_count}).eq("id", slot_id).execute()
            slot_client_phone = slot.get("client_phone", "")
            slot_title = slot.get("title", "Class")
            slot_date = slot.get("slot_date", "")
            slot_time = slot.get("start_time", "")
        else:
            slot_client_phone = ""
            slot_title = "Class"
            slot_date = ""
            slot_time = ""
    except Exception as e:
        print(f"[{timestamp()}] WARN booking: enrolled_count decrement failed — {e}")
        slot_client_phone = ""
        slot_title = "Class"
        slot_date = ""
        slot_time = ""

    # Load business name
    business_name = "Your service provider"
    try:
        c = sb.table("clients").select("business_name").eq("phone", slot_client_phone).execute()
        if c.data:
            business_name = c.data[0].get("business_name", business_name)
    except Exception:
        pass

    # 4. Check waitlist for this slot
    from execution.sms_send import send_sms
    waitlist_promoted = False
    try:
        wl = sb.table("class_waitlist").select(
            "id, customer_name, customer_phone"
        ).eq("slot_id", slot_id).eq("status", "waiting").order("added_at").limit(1).execute()

        if wl.data:
            wl_entry = wl.data[0]
            wl_phone = wl_entry.get("customer_phone")
            wl_name = wl_entry.get("customer_name", "there")

            # Build booking URL
            board_token = ""
            try:
                board = sb.table("class_boards").select("token").eq("client_phone", slot_client_phone).limit(1).execute()
                if board.data:
                    board_token = board.data[0].get("token", "")
            except Exception:
                pass
            base_url = os.environ.get("BOLTS11_BASE_URL", "https://bolts11.com")
            claim_url = f"{base_url}/book/{board_token}" if board_token else ""

            # 5. Notify waitlist position 1
            if wl_phone and claim_url:
                try:
                    send_sms(
                        to_number=wl_phone,
                        message_body=(
                            f"{business_name}: A spot opened in {slot_title} on {slot_date}! "
                            f"Tap to claim it within 2 hours: {claim_url}"
                        ),
                        from_number=slot_client_phone,
                        message_type="waitlist_notify",
                    )
                    waitlist_promoted = True
                    print(f"[{timestamp()}] INFO booking: Waitlist notify sent to {wl_name} ({wl_phone})")
                except Exception as e:
                    print(f"[{timestamp()}] WARN booking: waitlist notify SMS failed — {e}")

            # Update waitlist status
            try:
                sb.table("class_waitlist").update({
                    "status": "notified",
                }).eq("id", wl_entry["id"]).execute()
            except Exception:
                pass
    except Exception as e:
        print(f"[{timestamp()}] WARN booking: waitlist check failed — {e}")

    # 6. Send cancellation confirm to customer who cancelled
    if customer_phone:
        try:
            send_sms(
                to_number=customer_phone,
                message_body=f"Your booking for {slot_title} on {slot_date} has been cancelled.",
                from_number=slot_client_phone,
                message_type="cancellation",
            )
        except Exception as e:
            print(f"[{timestamp()}] WARN booking: cancel confirm SMS failed — {e}")

    print(f"[{timestamp()}] INFO booking: {customer_name} cancelled booking for {slot_title} — waitlist promoted: {waitlist_promoted}")

    return jsonify({
        "success": True,
        "waitlist_promoted": waitlist_promoted,
    })


# ---------------------------------------------------------------------------
# POST /api/admin/run-scheduled-sms — manual trigger for testing
# ---------------------------------------------------------------------------

@booking_bp.route("/api/admin/run-scheduled-sms", methods=["POST"])
def run_scheduled_sms():
    """
    Admin-only endpoint to manually trigger scheduled SMS jobs.
    Accepts JSON: {"job": "nudges|reminders|no_shows", "client_phone": "..."}
    """
    from flask import session as flask_session

    # Basic auth: must have session or be in dev mode
    client_id = flask_session.get("client_id")
    if not client_id and os.environ.get("FLASK_ENV") != "development":
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    data = request.get_json(silent=True) or {}
    job = data.get("job", "all")
    client_phone = data.get("client_phone", "")

    from execution.scheduled_sms import (
        send_class_nudges,
        send_appointment_reminders,
        mark_no_shows,
    )

    results = {}

    if job in ("nudges", "all") and client_phone:
        results["nudges"] = send_class_nudges(client_phone)

    if job in ("reminders", "all"):
        results["reminders"] = send_appointment_reminders()

    if job in ("no_shows", "all"):
        results["no_shows"] = mark_no_shows()

    print(f"[{timestamp()}] INFO admin: run-scheduled-sms job={job} results={results}")
    return jsonify({"success": True, "results": results})
