"""
square_agent.py — Generates Square Payment Links for invoices

Uses Square Payment Links API (quick_pay) to create a checkout URL
that can be embedded in the PAY NOW button on invoice pages or
sent directly via SMS.

Environment variables required:
    SQUARE_ACCESS_TOKEN   — Square API access token
    SQUARE_ENVIRONMENT    — 'sandbox' or 'production'
    SQUARE_LOCATION_ID    — Square location ID for the business

Usage:
    from execution.square_agent import create_payment_link
    result = create_payment_link(
        invoice_id="abc-123",
        amount_cents=15000,
        description="Septic pump out",
        customer_name="Mike Anderson",
    )
    # → {"success": True, "payment_link_url": "https://square.link/u/xxx", ...}
"""

import os
import sys
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

# squareup v44 SDK — try all known import paths
SQUARE_AVAILABLE = False
Client = None
try:
    from squareup.client import Client
    SQUARE_AVAILABLE = True
except ImportError:
    try:
        from square.client import Client
        SQUARE_AVAILABLE = True
    except ImportError:
        try:
            from square import Client
            SQUARE_AVAILABLE = True
        except ImportError:
            print(f"[square_agent] WARN: squareup SDK not found — payment links disabled")

load_dotenv()


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_square_client():
    """Return an initialised Square SDK client."""
    if Client is None:
        raise ImportError("Square SDK (squareup) is not installed — run: pip install squareup")
    environment = os.environ.get("SQUARE_ENVIRONMENT", "sandbox")
    access_token = os.environ.get("SQUARE_ACCESS_TOKEN")
    if not access_token:
        raise EnvironmentError("SQUARE_ACCESS_TOKEN is not set in .env")
    return Client(access_token=access_token, environment=environment)


def create_payment_link(
    invoice_id: str,
    amount_cents: int,
    description: str,
    customer_name: str,
) -> dict:
    """
    Create a Square Payment Link for an invoice.

    Args:
        invoice_id:     The Supabase invoice UUID (used as idempotency key suffix)
        amount_cents:   Amount in cents (e.g. $150.00 → 15000)
        description:    Line item description shown on the Square checkout page
        customer_name:  Customer name shown on the checkout page

    Returns:
        dict with keys: success, payment_link_url, square_order_id,
        square_payment_link_id — or success=False with error string.
    """
    try:
        client = get_square_client()
        location_id = os.environ.get("SQUARE_LOCATION_ID")
        if not location_id:
            print(f"[{timestamp()}] ERROR square_agent: SQUARE_LOCATION_ID is not set in .env")
            return {"success": False, "error": "SQUARE_LOCATION_ID not configured"}

        idempotency_key = f"bolts11-{invoice_id}-{uuid.uuid4().hex[:8]}"

        body = {
            "idempotency_key": idempotency_key,
            "quick_pay": {
                "name": description or "Invoice Payment",
                "price_money": {
                    "amount": amount_cents,
                    "currency": "USD",
                },
                "location_id": location_id,
            },
            "checkout_options": {
                "allow_tipping": False,
                "ask_for_shipping_address": False,
            },
            "payment_note": f"Bolts11 Invoice {invoice_id} — {customer_name}",
        }

        print(f"[{timestamp()}] INFO square_agent: Creating payment link for invoice {invoice_id} amount={amount_cents}¢")
        result = client.payment_links.create_payment_link(body=body)

        if result.is_success():
            link = result.body.get("payment_link", {})
            url = link.get("url")
            print(f"[{timestamp()}] INFO square_agent: Payment link created → {url}")
            return {
                "success": True,
                "payment_link_url": url,
                "square_order_id": link.get("order_id"),
                "square_payment_link_id": link.get("id"),
            }
        else:
            errors = result.errors
            error_msg = "; ".join(
                f"{e.get('code')}: {e.get('detail')}" for e in errors
            )
            print(f"[{timestamp()}] ERROR square_agent: Square API error — {error_msg}")
            return {"success": False, "error": error_msg}

    except EnvironmentError as e:
        print(f"[{timestamp()}] ERROR square_agent: {e}")
        return {"success": False, "error": str(e)}

    except Exception as e:
        print(f"[{timestamp()}] ERROR square_agent: Unexpected error — {e}")
        return {"success": False, "error": str(e)}
