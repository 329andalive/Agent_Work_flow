"""
sms_receive.py — Flask webhook server for inbound SMS from Telnyx

How it works:
    1. Telnyx POSTs to /webhook/inbound whenever a text arrives on your number
    2. We parse the payload, extract key fields, and log the message
    3. We immediately return 200 OK so Telnyx doesn't retry
    4. We call the router to determine which agent should handle the message
    5. (Future) We pass the message to the chosen agent for processing

Run locally:
    python execution/sms_receive.py

Then expose it to Telnyx with ngrok:
    ngrok http 5000
    → Set the webhook URL in Telnyx dashboard to: https://<your-ngrok-id>.ngrok.io/webhook/inbound
"""

import sys
import os
import threading

# Allow running as: python execution/sms_receive.py from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime
from execution.sms_router import route_message
from execution.db_webhook_log import is_duplicate, save_webhook, mark_processed, mark_error

app = Flask(__name__)


def timestamp():
    """Return a formatted timestamp string for log lines."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_telnyx_payload(payload: dict) -> dict | None:
    """
    Extract the fields we care about from a Telnyx inbound SMS webhook payload.

    Telnyx wraps everything under data.payload. Returns None if the payload
    doesn't look like an inbound SMS (wrong event type or missing fields).
    """
    try:
        event_type = payload.get("data", {}).get("event_type", "")

        # Only handle inbound messages — ignore delivery receipts, etc.
        if event_type != "message.received":
            print(f"[{timestamp()}] INFO sms_receive: Ignoring event type: {event_type}")
            return None

        inner = payload["data"]["payload"]

        from_number = inner.get("from", {}).get("phone_number")
        to_number   = inner.get("to", [{}])[0].get("phone_number")
        body        = inner.get("text", "")
        message_id  = inner.get("id")

        if not from_number or not message_id:
            print(f"[{timestamp()}] WARN sms_receive: Missing from_number or message_id in payload")
            return None

        return {
            "from_number": from_number,
            "to_number":   to_number,
            "body":        body,
            "message_id":  message_id,
        }

    except (KeyError, IndexError, TypeError) as e:
        print(f"[{timestamp()}] WARN sms_receive: Could not parse payload structure — {e}")
        return None


def _process_in_background(sms_data: dict, log_id: str | None):
    """
    Run the agent in a background thread so the webhook can return 200
    immediately. Marks the webhook_log row processed or error on completion.
    Deduplication is handled upstream in inbound_webhook() via webhook_log.
    """
    try:
        agent = route_message(sms_data)
        print(f"[{timestamp()}] INFO sms_receive: Message dispatched → {agent}")
        mark_processed(log_id)
    except Exception as e:
        print(f"[{timestamp()}] ERROR sms_receive: route_message raised — {e}")
        mark_error(log_id, str(e))


@app.route("/webhook/inbound", methods=["POST"])
def inbound_webhook():
    """
    Telnyx calls this endpoint whenever a message arrives on your number.

    Returns 200 immediately so Telnyx doesn't retry.
    All agent work runs in a background thread.

    Order of operations (CLAUDE.md Rule #5 — raw payload saved first):
        1. Parse JSON
        2. Early-extract message_id for dedup check
        3. Check webhook_log for duplicate — return 200 early if found
        4. Save raw payload to webhook_log (before any processing)
        5. Parse event type — ignore non-SMS events
        6. Dispatch to background thread
    """
    print(f"[{timestamp()}] INFO sms_receive: Webhook hit — POST /webhook/inbound")

    # Step 1: Parse the JSON body. Return 200 even on bad payloads so Telnyx
    # doesn't retry indefinitely — we log the problem instead.
    try:
        payload = request.get_json(force=True, silent=True)
        if payload is None:
            print(f"[{timestamp()}] WARN sms_receive: Empty or non-JSON body received")
            return jsonify({"status": "ignored", "reason": "non-JSON body"}), 200
    except Exception as e:
        print(f"[{timestamp()}] ERROR sms_receive: Failed to read request body — {e}")
        return jsonify({"status": "error"}), 200  # still 200 to stop retries

    # Step 2: Early-extract message_id before saving payload.
    # Telnyx puts it at data.payload.id for inbound events.
    try:
        early_msg_id = payload.get("data", {}).get("payload", {}).get("id")
    except Exception:
        early_msg_id = None

    # Step 3: Dedup check — if we've seen this message_id before, Telnyx is
    # retrying. Return 200 immediately so it stops retrying.
    if early_msg_id and is_duplicate(early_msg_id):
        print(f"[{timestamp()}] INFO sms_receive: Duplicate message_id={early_msg_id} — returning 200 early")
        return jsonify({"status": "duplicate"}), 200

    # Step 4: Save raw payload FIRST (CLAUDE.md Rule #5).
    # Raw data must survive even if downstream processing fails.
    log_id = save_webhook(payload, early_msg_id)

    # Step 5: Parse the Telnyx event — skip delivery receipts, sent events, etc.
    sms_data = parse_telnyx_payload(payload)
    if sms_data is None:
        mark_processed(log_id)   # non-SMS event — logged and done
        return jsonify({"status": "ignored"}), 200

    # Step 6: Log the inbound SMS details
    print(
        f"[{timestamp()}] INBOUND SMS | "
        f"id={sms_data['message_id']} | "
        f"from={sms_data['from_number']} | "
        f"to={sms_data['to_number']} | "
        f"body='{sms_data['body'][:80]}'"
    )

    # Step 7: Spin up a background thread — return 200 immediately
    thread = threading.Thread(target=_process_in_background, args=(sms_data, log_id))
    thread.daemon = True
    thread.start()

    return jsonify({"status": "ok"}), 200


@app.route("/dashboard/")
@app.route("/dashboard/index.html")
def dashboard_board():
    """Serve the dispatch board dashboard."""
    dashboard_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard")
    return send_from_directory(dashboard_dir, "index.html")


@app.route("/dashboard/office.html")
def dashboard_office():
    """Serve the office dashboard."""
    dashboard_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard")
    return send_from_directory(dashboard_dir, "office.html")


@app.route("/dashboard/book.html")
@app.route("/book")
def booking_form():
    """Serve the customer-facing TCR-compliant booking / opt-in form."""
    dashboard_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard")
    return send_from_directory(dashboard_dir, "book.html")


@app.route("/book/submit", methods=["POST"])
def book_submit():
    """
    Handle the booking form POST.

    Expected JSON body:
        client_phone  — Telnyx number of the business (identifies the client)
        name          — Customer's name
        phone         — Customer's phone (E.164, normalized by the form)
        address       — Service address
        service_type  — Service type selected on the form
        notes         — Optional extra detail
        sms_consent   — Must be true (form requires checkbox)

    Flow:
        1. Validate required fields
        2. Look up client by client_phone
        3. Look up or create customer record
        4. Record consent (source = 'web_form')
        5. Kick off proposal_agent so owner gets a text immediately
    """
    try:
        data = request.get_json(force=True, silent=True) or {}

        client_phone = data.get("client_phone", "").strip()
        name         = data.get("name", "").strip()
        phone        = data.get("phone", "").strip()
        address      = data.get("address", "").strip()
        service_type = data.get("service_type", "").strip()
        notes        = data.get("notes", "").strip()
        consented    = data.get("sms_consent", False)

        # Basic field validation
        if not all([client_phone, name, phone, address, service_type]):
            return jsonify({"status": "error", "message": "Missing required fields."}), 400

        if not consented:
            return jsonify({"status": "error", "message": "SMS consent is required."}), 400

        # Look up client
        from execution.db_client import get_client_by_phone
        client = get_client_by_phone(client_phone)
        if not client:
            print(f"[{timestamp()}] WARN book_submit: No client for phone={client_phone}")
            return jsonify({"status": "error", "message": "Business not found."}), 404

        client_id = client["id"]

        # Look up or create customer
        from execution.db_customer import get_customer_by_phone, create_customer
        customer = get_customer_by_phone(client_id, phone)
        if not customer:
            create_customer(client_id=client_id, name=name, phone=phone, address=address)

        # Record consent
        from execution.db_consent import set_consent
        set_consent(client_id, phone, source="web_form")

        # Build raw_input for proposal_agent from the form fields
        raw = f"{name} at {address} needs {service_type}."
        if notes:
            raw += f" Notes: {notes}"

        # Run proposal_agent in background so the HTTP response is fast
        def _run_proposal():
            try:
                from execution.proposal_agent import run as proposal_run
                proposal_run(client_phone=client_phone, customer_phone=phone, raw_input=raw)
            except Exception as ex:
                print(f"[{timestamp()}] ERROR book_submit: proposal_agent failed — {ex}")

        t = __import__("threading").Thread(target=_run_proposal)
        t.daemon = True
        t.start()

        print(f"[{timestamp()}] INFO book_submit: Booking received — {name} / {phone} / {service_type}")
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"[{timestamp()}] ERROR book_submit: Unexpected error — {e}")
        return jsonify({"status": "error", "message": "Something went wrong."}), 500


@app.route("/health", methods=["GET"])
def health():
    """Simple health check — confirm the server is up."""
    return jsonify({"status": "ok", "timestamp": timestamp()}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"[{timestamp()}] INFO sms_receive: Starting Flask webhook server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
