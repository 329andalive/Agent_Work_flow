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

from flask import Flask, request, jsonify, render_template
from datetime import datetime
from execution.sms_router import route_message
from execution.db_webhook_log import is_duplicate, save_webhook, mark_processed, mark_error

# Point Flask templates at the project-level templates/ dir
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__, template_folder=os.path.join(_project_root, "templates"))
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

from datetime import timedelta as _timedelta
app.permanent_session_lifetime = _timedelta(days=30)

# Register blueprints
from routes.document_routes import document_bp
from routes.invoice_routes import invoice_bp
from routes.routes_debug import debug_bp
from routes.command_routes import command_bp
from routes.onboarding_routes import onboarding_bp
from routes.dashboard_routes import dashboard_bp
from routes.auth_routes import auth_bp
app.register_blueprint(document_bp)
app.register_blueprint(invoice_bp)
app.register_blueprint(debug_bp)
app.register_blueprint(command_bp)
app.register_blueprint(onboarding_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(auth_bp)


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


# ---------------------------------------------------------------------------
# Telnyx webhook signature verification (Ed25519)
# ---------------------------------------------------------------------------

def _verify_telnyx_signature(raw_body: bytes, timestamp_hdr: str, signature_hdr: str) -> bool:
    """
    Validate a Telnyx webhook using the Ed25519 public key.

    Telnyx signs payloads as: base64(ed25519_sign(key, f"{timestamp}|{raw_body}"))
    Headers: telnyx-signature-ed25519, telnyx-timestamp

    If TELNYX_PUBLIC_KEY is not set in .env, validation is skipped and True
    is returned — this lets the server come up cleanly before the key is
    added without dropping real traffic. Log the warning so it's visible.

    Returns:
        True if signature is valid (or key not configured), False if invalid.
    """
    public_key_b64 = os.environ.get("TELNYX_PUBLIC_KEY", "").strip()
    if not public_key_b64:
        print(f"[{timestamp()}] WARN sms_receive: TELNYX_PUBLIC_KEY not set — skipping signature check")
        return True

    if not timestamp_hdr or not signature_hdr:
        print(f"[{timestamp()}] WARN sms_receive: Missing Telnyx signature headers")
        return False

    try:
        import base64
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature

        signed_content = f"{timestamp_hdr}|".encode() + raw_body
        public_key_bytes = base64.b64decode(public_key_b64)
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        sig_bytes = base64.b64decode(signature_hdr)
        public_key.verify(sig_bytes, signed_content)
        return True

    except InvalidSignature:
        print(f"[{timestamp()}] WARN sms_receive: Telnyx signature mismatch — possible replay or spoofed request")
        return False
    except Exception as e:
        print(f"[{timestamp()}] ERROR sms_receive: Signature verification error — {e}")
        return False


# ---------------------------------------------------------------------------
# Delivery status handler (message.sent / message.delivered / message.failed)
# ---------------------------------------------------------------------------

_DELIVERY_EVENTS = {"message.sent", "message.delivered", "message.failed", "message.finalized"}


def _handle_delivery_status_bg(payload: dict, log_id: str | None) -> None:
    """
    Background handler for Telnyx delivery status events.
    Matches on telnyx_message_id in the messages table and updates delivery_status.
    """
    try:
        from execution.db_messages import update_message_status
        inner = payload.get("data", {}).get("payload", {})
        telnyx_msg_id = inner.get("id")
        event_type    = payload.get("data", {}).get("event_type", "")

        status_map = {
            "message.sent":      "sent",
            "message.delivered": "delivered",
            "message.failed":    "failed",
            "message.finalized": "finalized",
        }
        new_status = status_map.get(event_type, event_type.replace("message.", ""))

        if telnyx_msg_id:
            update_message_status(telnyx_msg_id, new_status)

        mark_processed(log_id)

    except Exception as e:
        print(f"[{timestamp()}] ERROR sms_receive: _handle_delivery_status_bg — {e}")
        mark_error(log_id, str(e))


# ---------------------------------------------------------------------------
# /webhooks/telnyx — primary Telnyx webhook endpoint (with signature check)
# ---------------------------------------------------------------------------

@app.route("/webhooks/telnyx", methods=["POST"])
def telnyx_webhook():
    """
    Primary Telnyx webhook handler.

    Differences from legacy /webhook/inbound:
      - Validates Ed25519 signature (requires TELNYX_PUBLIC_KEY in .env)
      - Handles both inbound SMS (message.received) and delivery status events
        (message.sent, message.delivered, message.failed, message.finalized)
      - Logs event_type and tenant_id to webhook_log

    Order of operations (CLAUDE.md Rule #5 — raw payload saved first):
      1. Read raw body (before JSON parsing — needed for signature check)
      2. Verify Ed25519 signature
      3. Parse JSON
      4. Early-extract message_id + event_type for dedup and logging
      5. Dedup check
      6. Look up tenant_id by to_number
      7. Save raw payload to webhook_log (with tenant_id + event_type)
      8. Route: inbound SMS → background agent; delivery event → status update
    """
    print(f"[{timestamp()}] INFO sms_receive: POST /webhooks/telnyx")

    # Step 1: Read raw bytes BEFORE get_json() consumes the body
    raw_body = request.get_data()

    # Step 2: Verify signature
    sig   = request.headers.get("telnyx-signature-ed25519", "")
    ts_hdr = request.headers.get("telnyx-timestamp", "")
    if not _verify_telnyx_signature(raw_body, ts_hdr, sig):
        print(f"[{timestamp()}] WARN sms_receive: Rejected — invalid Telnyx signature")
        return jsonify({"status": "unauthorized"}), 200  # still 200 — don't reveal rejection to attacker

    # Step 3: Parse JSON
    try:
        import json as _json
        payload = _json.loads(raw_body) if raw_body else None
        if not payload:
            print(f"[{timestamp()}] WARN sms_receive: Empty body on /webhooks/telnyx")
            return jsonify({"status": "ignored"}), 200
    except Exception as e:
        print(f"[{timestamp()}] ERROR sms_receive: JSON parse failed — {e}")
        return jsonify({"status": "ignored"}), 200

    # Step 4: Extract event_type and message_id
    data_block   = payload.get("data", {})
    event_type   = data_block.get("event_type", "")
    early_msg_id = data_block.get("payload", {}).get("id")
    to_number    = (data_block.get("payload", {}).get("to") or [{}])
    # to is a list for inbound, a string for outbound status events
    if isinstance(to_number, list):
        to_number = to_number[0].get("phone_number", "") if to_number else ""
    elif isinstance(to_number, str):
        pass
    else:
        to_number = ""

    # Step 5: Dedup check (only relevant for inbound — delivery events share same msg id)
    if event_type == "message.received" and early_msg_id and is_duplicate(early_msg_id):
        print(f"[{timestamp()}] INFO sms_receive: Duplicate message_id={early_msg_id} — returning 200 early")
        return jsonify({"status": "duplicate"}), 200

    # Step 6: Resolve tenant_id from the Telnyx number (to_number)
    # Skip DB lookup for delivery receipt events — they are outbound status
    # updates and don't need a client lookup. Saves a DB round-trip per event.
    tenant_id = None
    if event_type not in _DELIVERY_EVENTS and to_number:
        try:
            from execution.db_client import get_client_by_phone
            tenant_client = get_client_by_phone(to_number)
            if tenant_client:
                tenant_id = tenant_client["id"]
        except Exception:
            pass

    # Step 7: Save raw payload to webhook_log (Rule #5)
    log_id = save_webhook(payload, early_msg_id, event_type=event_type, tenant_id=tenant_id)

    # Step 8: Route based on event type
    if event_type == "message.received":
        sms_data = parse_telnyx_payload(payload)
        if sms_data is None:
            mark_processed(log_id)
            return jsonify({"status": "ignored"}), 200

        print(
            f"[{timestamp()}] INBOUND SMS | "
            f"id={sms_data['message_id']} | "
            f"from={sms_data['from_number']} | "
            f"to={sms_data['to_number']} | "
            f"body='{sms_data['body'][:80]}'"
        )
        thread = threading.Thread(target=_process_in_background, args=(sms_data, log_id))
        thread.daemon = True
        thread.start()

    elif event_type in _DELIVERY_EVENTS:
        print(f"[{timestamp()}] INFO sms_receive: Delivery event {event_type} for msg_id={early_msg_id}")
        thread = threading.Thread(target=_handle_delivery_status_bg, args=(payload, log_id))
        thread.daemon = True
        thread.start()

    else:
        print(f"[{timestamp()}] INFO sms_receive: Unhandled event type '{event_type}' — logged only")
        mark_processed(log_id)

    return jsonify({"status": "ok"}), 200


@app.route("/webhooks/telnyx/failover", methods=["POST"])
def telnyx_failover():
    """
    Telnyx failover webhook — called when the primary URL is unreachable.

    Logs the raw payload to Supabase so no data is lost, then returns 200.
    No signature check here — failover fires when primary is down, so we
    accept anything and worry about replay risk less than data loss.
    """
    print(f"[{timestamp()}] WARN sms_receive: POST /webhooks/telnyx/failover — primary may be degraded")

    try:
        raw_body = request.get_data()
        try:
            import json as _json
            payload = _json.loads(raw_body) if raw_body else {}
        except Exception:
            payload = {"raw": raw_body.decode("utf-8", errors="replace")}

        event_type   = payload.get("data", {}).get("event_type", "unknown")
        early_msg_id = payload.get("data", {}).get("payload", {}).get("id")

        log_id = save_webhook(payload, early_msg_id, event_type=f"failover:{event_type}", tenant_id=None)
        mark_processed(log_id)

        print(f"[{timestamp()}] INFO sms_receive: Failover payload logged — log_id={log_id} event={event_type}")
    except Exception as e:
        print(f"[{timestamp()}] ERROR sms_receive: Failover handler failed — {e}")

    return jsonify({"status": "ok"}), 200


## Dashboard routes moved to routes/dashboard_routes.py (dashboard_bp blueprint)
## Public routes (book.html) also handled there.


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


# ---------------------------------------------------------------------------
# Token-based proposal and invoice routes
# ---------------------------------------------------------------------------

def _resolve_token(token: str):
    """
    Shared logic for /p/ and /i/ routes: look up token, check expiry,
    load client record. Returns (link_record, client, error_response).
    If error_response is not None, the caller should return it directly.
    """
    from execution.token_generator import get_link_by_token, is_expired, mark_viewed
    from execution.db_client import get_client_by_phone

    link = get_link_by_token(token)

    if not link:
        return None, None, render_template("error.html",
            title="Invalid Link",
            icon="?",
            heading="Link Not Found",
            message="This link is invalid. It may have been mistyped or already removed.",
            business_name=None,
            contact_phone=None,
        ), 404

    # Load the client so we can show their business name in errors
    client = get_client_by_phone(link["client_phone"]) if link.get("client_phone") else None
    biz_name = client["business_name"] if client else "the business"
    contact = client.get("owner_mobile") or link.get("client_phone") or ""

    if is_expired(link):
        return None, None, render_template("error.html",
            title="Link Expired",
            icon="!",
            heading="This Link Has Expired",
            message=f"This link is no longer active. Please contact {biz_name} for a new one.",
            business_name=biz_name,
            contact_phone=contact,
        ), 410

    # Mark as viewed and log the view event
    mark_viewed(token)

    try:
        from execution.db_agent_activity import log_activity
        log_activity(
            client_phone=link.get("client_phone", ""),
            agent_name="link_viewer",
            action_taken=f"{link['type']}_viewed",
            input_summary=f"token={token}",
            output_summary=f"Document viewed",
            sms_sent=False,
        )
    except Exception:
        pass

    return link, client, None


@app.route("/p/<token>")
def view_proposal(token):
    """
    Serve a proposal via signed token link.
    Looks up the token, loads job + proposal data, renders the proposal template.
    """
    link, client, error = _resolve_token(token)
    if error is not None:
        return error

    if link["type"] != "proposal":
        return render_template("error.html",
            title="Invalid Link",
            icon="?",
            heading="Link Not Found",
            message="This link is invalid.",
            business_name=None,
            contact_phone=None,
        ), 404

    # Load job and proposal data from Supabase
    from execution.db_connection import get_client as get_supabase
    supabase = get_supabase()

    job_id = link.get("job_id")
    job = None
    proposal = None

    if job_id:
        job_result = supabase.table("jobs").select("*").eq("id", job_id).execute()
        if job_result.data:
            job = job_result.data[0]

        prop_result = supabase.table("proposals").select("*").eq("job_id", job_id).order("created_at", desc=True).limit(1).execute()
        if prop_result.data:
            proposal = prop_result.data[0]

    # Build template context from proposal text
    proposal_text = proposal.get("proposal_text", "") if proposal else ""
    proposal_lines = [l for l in proposal_text.split("\n") if l.strip()]

    # Try to load customer info
    customer_name = "Customer"
    customer_address = ""
    if job and job.get("customer_id"):
        cust_result = supabase.table("customers").select("*").eq("id", job["customer_id"]).execute()
        if cust_result.data:
            customer_name = cust_result.data[0].get("customer_name", "Customer")
            customer_address = cust_result.data[0].get("customer_address", "")

    biz_name = client["business_name"] if client else "Business"
    date_str = datetime.now().strftime("%B %d, %Y")

    return render_template("proposal.html",
        business_name=biz_name,
        customer_name=customer_name,
        customer_address=customer_address,
        date=date_str,
        proposal_lines=proposal_lines,
        line_items=[],
        total=None,
        client_phone=link.get("client_phone", ""),
        token=token,
    )


@app.route("/i/<token>")
def view_invoice(token):
    """
    Serve an invoice via signed token link.
    Looks up the token, loads job + invoice data, renders the invoice template.
    """
    link, client, error = _resolve_token(token)
    if error is not None:
        return error

    if link["type"] != "invoice":
        return render_template("error.html",
            title="Invalid Link",
            icon="?",
            heading="Link Not Found",
            message="This link is invalid.",
            business_name=None,
            contact_phone=None,
        ), 404

    # Load job and invoice data from Supabase
    from execution.db_connection import get_client as get_supabase
    supabase = get_supabase()

    job_id = link.get("job_id")
    job = None
    invoice = None

    if job_id:
        job_result = supabase.table("jobs").select("*").eq("id", job_id).execute()
        if job_result.data:
            job = job_result.data[0]

        inv_result = supabase.table("invoices").select("*").eq("job_id", job_id).order("created_at", desc=True).limit(1).execute()
        if inv_result.data:
            invoice = inv_result.data[0]

    # Build template context
    invoice_text = invoice.get("invoice_text", "") if invoice else ""
    invoice_lines = [l for l in invoice_text.split("\n") if l.strip()]
    total_amount = float(invoice.get("amount_due", 0)) if invoice else 0.0

    # Invoice number from job_id
    from datetime import date
    inv_date = date.today().strftime("%Y%m%d")
    inv_suffix = (job_id or "0000")[-4:].upper()
    invoice_number = f"INV-{inv_date}-{inv_suffix}"

    # Customer info
    customer_name = "Customer"
    customer_address = ""
    if job and job.get("customer_id"):
        cust_result = supabase.table("customers").select("*").eq("id", job["customer_id"]).execute()
        if cust_result.data:
            customer_name = cust_result.data[0].get("customer_name", "Customer")
            customer_address = cust_result.data[0].get("customer_address", "")

    # Job description
    job_description = job.get("raw_input", "") if job else ""
    job_description_lines = [l for l in job_description.split("\n") if l.strip()]

    # Payment info from client personality
    payment_terms = ""
    payment_methods = ""
    if client and client.get("personality"):
        import re
        terms_match = re.search(r'[Ss]tandard payment terms?:?\s*(.+?)(?:\n|$)', client["personality"])
        if terms_match:
            payment_terms = terms_match.group(1).strip()
        methods_match = re.search(r'[Pp]ayment methods? accepted?:?\s*(.+?)(?:\n|$)', client["personality"])
        if methods_match:
            payment_methods = methods_match.group(1).strip()

    # Square payment link + paid status
    payment_link_url = link.get("payment_link_url") or ""
    is_paid = bool(invoice.get("paid_at")) if invoice else False

    # Parse structured line_items if present on the invoice
    structured_items = []
    if invoice and invoice.get("line_items"):
        raw_items = invoice["line_items"]
        if isinstance(raw_items, str):
            try:
                import json as _json
                structured_items = _json.loads(raw_items)
            except Exception:
                pass
        elif isinstance(raw_items, list):
            structured_items = raw_items

    biz_name = client["business_name"] if client else "Business"
    date_str = datetime.now().strftime("%B %d, %Y")

    return render_template("invoice.html",
        business_name=biz_name,
        customer_name=customer_name,
        customer_address=customer_address,
        date=date_str,
        invoice_number=invoice_number,
        invoice_lines=invoice_lines,
        line_items=structured_items,
        total_amount=total_amount,
        job_description=job_description,
        job_description_lines=job_description_lines,
        payment_terms=payment_terms or "Due on receipt",
        payment_methods=payment_methods or "Check, cash, or Venmo",
        payment_link_url=payment_link_url,
        is_paid=is_paid,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"[{timestamp()}] INFO sms_receive: Starting Flask webhook server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
