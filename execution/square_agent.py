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

# squareup v44 SDK — the pip package is "squareup" but the module is "square"
# v44 exports Square (not Client) from square.client
SQUARE_AVAILABLE = False
Square = None
SquareEnvironment = None

try:
    from square.client import Square, SquareEnvironment
    SQUARE_AVAILABLE = True
except ImportError:
    try:
        from square import Square
        SQUARE_AVAILABLE = True
    except ImportError:
        print("[square_agent] WARN: squareup SDK not found — payment links disabled")

load_dotenv()


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_square_client():
    """Return an initialised Square SDK client."""
    if Square is None:
        raise ImportError("Square SDK (squareup) is not installed — run: pip install squareup")
    access_token = os.environ.get("SQUARE_ACCESS_TOKEN")
    if not access_token:
        raise EnvironmentError("SQUARE_ACCESS_TOKEN is not set in .env")

    # v44 uses SquareEnvironment enum, not a string
    env_str = os.environ.get("SQUARE_ENVIRONMENT", "sandbox").lower()
    if SquareEnvironment:
        environment = SquareEnvironment.SANDBOX if env_str == "sandbox" else SquareEnvironment.PRODUCTION
        return Square(token=access_token, environment=environment)
    else:
        # Fallback for unknown SDK version
        return Square(token=access_token)


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

        print(f"[{timestamp()}] INFO square_agent: Creating payment link for invoice {invoice_id} amount={amount_cents}¢")

        # v44 API: client.checkout.create_payment_link()
        result = client.checkout.create_payment_link(
            idempotency_key=idempotency_key,
            quick_pay={
                "name": description or "Invoice Payment",
                "price_money": {
                    "amount": amount_cents,
                    "currency": "USD",
                },
                "location_id": location_id,
            },
            checkout_options={
                "allow_tipping": False,
                "ask_for_shipping_address": False,
            },
            payment_note=f"Bolts11 Invoice {invoice_id} — {customer_name}",
        )

        # v44 returns the response object directly (not wrapped in is_success)
        if hasattr(result, 'payment_link'):
            link = result.payment_link
            url = getattr(link, 'url', None) or getattr(link, 'long_url', None)
            order_id = getattr(link, 'order_id', None)
            link_id = getattr(link, 'id', None)
            print(f"[{timestamp()}] INFO square_agent: Payment link created → {url}")
            return {
                "success": True,
                "payment_link_url": url,
                "square_order_id": order_id,
                "square_payment_link_id": link_id,
            }
        elif hasattr(result, 'body'):
            # Older SDK style
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
            print(f"[{timestamp()}] ERROR square_agent: Unexpected response type: {type(result)}")
            return {"success": False, "error": f"Unexpected response: {result}"}

    except EnvironmentError as e:
        print(f"[{timestamp()}] ERROR square_agent: {e}")
        return {"success": False, "error": str(e)}

    except Exception as e:
        print(f"[{timestamp()}] ERROR square_agent: Unexpected error — {e}")
        return {"success": False, "error": str(e)}
