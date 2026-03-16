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

# Allow running as: python execution/sms_receive.py from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify
from datetime import datetime
from execution.sms_router import route_message

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


def handle_inbound(sms_data: dict):
    """
    STUB — called after parsing a valid inbound SMS.

    Currently just logs and routes. Future work:
    - Call the agent returned by route_message()
    - Pass sms_data + client context to that agent
    - Send a reply via sms_send.py
    """
    agent = route_message(sms_data)
    print(f"[{timestamp()}] INFO sms_receive: Message dispatched → {agent} (not yet implemented)")
    # TODO: call agent here once agents are built
    # e.g. proposal_agent.handle(sms_data, client)


@app.route("/webhook/inbound", methods=["POST"])
def inbound_webhook():
    """
    Telnyx calls this endpoint whenever a message arrives on your number.

    We must return 200 OK quickly — Telnyx will retry if we don't.
    All heavy processing happens after the response is returned (or async in prod).
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

    # Step 2: Extract the fields we need
    sms_data = parse_telnyx_payload(payload)
    if sms_data is None:
        # parse_telnyx_payload already logged the reason
        return jsonify({"status": "ignored"}), 200

    # Step 3: Log the inbound message
    print(
        f"[{timestamp()}] INBOUND SMS | "
        f"id={sms_data['message_id']} | "
        f"from={sms_data['from_number']} | "
        f"to={sms_data['to_number']} | "
        f"body='{sms_data['body'][:80]}'"
    )

    # Step 4: Route and handle (non-blocking stub for now)
    try:
        handle_inbound(sms_data)
    except Exception as e:
        # Never let agent errors bubble up and affect the 200 response to Telnyx
        print(f"[{timestamp()}] ERROR sms_receive: handle_inbound raised — {e}")

    # Step 5: Acknowledge immediately
    return jsonify({"status": "ok"}), 200


@app.route("/health", methods=["GET"])
def health():
    """Simple health check — confirm the server is up."""
    return jsonify({"status": "ok", "timestamp": timestamp()}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"[{timestamp()}] INFO sms_receive: Starting Flask webhook server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
