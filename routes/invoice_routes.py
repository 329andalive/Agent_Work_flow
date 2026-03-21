"""
invoice_routes.py — Flask Blueprint for Square payment webhook

Blueprint: invoice_bp, prefix: none (webhook lives at /webhooks/square)

Routes:
    POST /webhooks/square — Square webhook: payment.completed → mark paid + SMS owner

The /i/<token> invoice view route stays in sms_receive.py (it uses _resolve_token
shared with /p/<token>). This Blueprint only handles the Square webhook.

Register in sms_receive.py:
    from routes.invoice_routes import invoice_bp
    app.register_blueprint(invoice_bp)
"""

import os
import sys
import hmac
import hashlib
import base64
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, request, jsonify

invoice_bp = Blueprint("invoice_bp", __name__)


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Square webhook signature verification
# ---------------------------------------------------------------------------

def _verify_square_signature(request_body: bytes, signature_header: str) -> bool:
    """
    Verify the Square webhook signature using HMAC-SHA256.
    Square signs with: HMAC-SHA256(webhook_signature_key, notification_url + body)

    If SQUARE_WEBHOOK_SIGNATURE_KEY is not set, verification is skipped
    (allows dev/sandbox testing). Lock this down in production.
    """
    webhook_sig_key = os.environ.get("SQUARE_WEBHOOK_SIGNATURE_KEY", "")
    notification_url = os.environ.get("SQUARE_WEBHOOK_URL", "")

    if not webhook_sig_key or not notification_url:
        print(f"[{timestamp()}] WARN invoice_routes: Square signature key or URL not set — skipping verification")
        return True

    payload = notification_url.encode("utf-8") + request_body
    computed = hmac.new(
        webhook_sig_key.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).digest()
    computed_b64 = base64.b64encode(computed).decode("utf-8")

    return hmac.compare_digest(computed_b64, signature_header)


# ---------------------------------------------------------------------------
# POST /webhooks/square — payment.completed handler
# ---------------------------------------------------------------------------

@invoice_bp.route("/webhooks/square", methods=["POST"])
def square_webhook():
    """
    Square webhook handler.
    Listens for payment.completed events.
    On success: marks invoice paid, sends SMS to owner.

    Always returns 200 — never let our errors cause Square retries.
    """
    raw_body = request.get_data()
    signature = request.headers.get("x-square-hmacsha256-signature", "")

    # Verify signature
    if not _verify_square_signature(raw_body, signature):
        print(f"[{timestamp()}] WARN invoice_routes: Invalid Square signature — rejecting")
        return jsonify({"status": "unauthorized"}), 200  # 200 to stop retries

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, TypeError):
        print(f"[{timestamp()}] ERROR invoice_routes: Failed to parse Square webhook body")
        return jsonify({"status": "bad_request"}), 200

    event_type = payload.get("type", "")
    print(f"[{timestamp()}] INFO invoice_routes: Square webhook event → {event_type}")

    # Only process payment.completed
    if event_type != "payment.completed":
        return jsonify({"status": "ignored"}), 200

    try:
        payment = payload.get("data", {}).get("object", {}).get("payment", {})
        square_payment_id = payment.get("id")
        order_id = payment.get("order_id")
        amount_cents = payment.get("total_money", {}).get("amount", 0)
        amount_dollars = amount_cents / 100.0

        if not order_id:
            print(f"[{timestamp()}] ERROR invoice_routes: No order_id in payment.completed event")
            return jsonify({"status": "ok"}), 200

        # Look up which invoice this payment belongs to
        from execution.token_generator import get_link_by_square_order, mark_invoice_paid

        link = get_link_by_square_order(order_id)
        if not link:
            print(f"[{timestamp()}] ERROR invoice_routes: No invoice_link found for Square order {order_id}")
            return jsonify({"status": "ok"}), 200

        # Get the job_id from the link, then find the invoice
        job_id = link.get("job_id")
        invoice_id = None
        client_id = None
        customer_name = "Customer"

        if job_id:
            from execution.db_connection import get_client as get_supabase
            supabase = get_supabase()

            # Find the invoice for this job
            inv_result = (
                supabase.table("invoices")
                .select("id, client_id, customer_id, amount_due")
                .eq("job_id", job_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if inv_result.data:
                invoice = inv_result.data[0]
                invoice_id = invoice["id"]
                client_id = invoice.get("client_id")

                # Get customer name
                cust_id = invoice.get("customer_id")
                if cust_id:
                    cust_result = supabase.table("customers").select("customer_name").eq("id", cust_id).execute()
                    if cust_result.data:
                        customer_name = cust_result.data[0].get("customer_name", "Customer")

        if not invoice_id:
            print(f"[{timestamp()}] ERROR invoice_routes: No invoice found for job {job_id}")
            return jsonify({"status": "ok"}), 200

        # Mark invoice paid
        success = mark_invoice_paid(invoice_id, square_payment_id=square_payment_id)
        if not success:
            print(f"[{timestamp()}] ERROR invoice_routes: Failed to mark invoice {invoice_id} paid")
            return jsonify({"status": "ok"}), 200

        print(f"[{timestamp()}] INFO invoice_routes: Invoice {invoice_id} marked paid — ${amount_dollars:.2f}")

        # SMS the owner
        _notify_owner_payment_received(client_id, customer_name, amount_dollars, invoice_id)

        # Log activity
        try:
            from execution.db_agent_activity import log_activity
            log_activity(
                client_phone=link.get("client_phone", ""),
                agent_name="square_webhook",
                action_taken="payment_received",
                input_summary=f"order={order_id} payment={square_payment_id}",
                output_summary=f"invoice={invoice_id} amount=${amount_dollars:.2f} customer={customer_name}",
                sms_sent=True,
            )
        except Exception:
            pass

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"[{timestamp()}] ERROR invoice_routes: Unhandled error in square_webhook — {e}")
        return jsonify({"status": "ok"}), 200  # Always 200


def _notify_owner_payment_received(
    client_id: str,
    customer_name: str,
    amount_dollars: float,
    invoice_id: str,
) -> None:
    """Fetch client record and SMS the owner that a payment came in."""
    try:
        from execution.db_connection import get_client as get_supabase
        from execution.sms_send import send_sms

        supabase = get_supabase()
        client_result = (
            supabase.table("clients")
            .select("owner_mobile, phone, business_name")
            .eq("id", client_id)
            .execute()
        )

        if not client_result.data:
            print(f"[{timestamp()}] ERROR invoice_routes: Could not fetch client {client_id} for payment SMS")
            return

        client = client_result.data[0]
        owner_mobile = client.get("owner_mobile") or client.get("phone")
        client_phone = client.get("phone", "")

        if not owner_mobile:
            print(f"[{timestamp()}] WARN invoice_routes: No owner_mobile for client {client_id} — skipping SMS")
            return

        inv_short = str(invoice_id)[:8].upper()
        message = (
            f"Payment received!\n"
            f"{customer_name} paid ${amount_dollars:.2f}.\n"
            f"Invoice {inv_short} is marked paid."
        )

        send_sms(to_number=owner_mobile, message_body=message, from_number=client_phone)
        print(f"[{timestamp()}] INFO invoice_routes: Payment notification sent to {owner_mobile}")

    except Exception as e:
        print(f"[{timestamp()}] ERROR invoice_routes: SMS notification failed — {e}")
