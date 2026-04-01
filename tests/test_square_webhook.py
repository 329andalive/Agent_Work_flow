"""
test_square_webhook.py — Tests for the Square payment webhook handler.

Tests:
  - payment.completed via invoice_links lookup (Path A) marks invoice paid
  - payment.completed via payment_note fallback (Path B) marks invoice paid
  - Non-payment events are ignored (200 returned)
  - Missing order_id + no payment_note still returns 200 gracefully
"""

import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import TEST_CLIENT_ID, TEST_CLIENT_PHONE

FAKE_INVOICE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
FAKE_JOB_ID = "job-0001-test-uuid"
FAKE_ORDER_ID = "sq-order-abc123"
FAKE_PAYMENT_ID = "sq-pay-xyz789"


def _square_payload(event_type="payment.completed", order_id=FAKE_ORDER_ID,
                    payment_id=FAKE_PAYMENT_ID, amount_cents=35000,
                    note=""):
    """Build a realistic Square webhook payload."""
    return {
        "type": event_type,
        "data": {
            "object": {
                "payment": {
                    "id": payment_id,
                    "order_id": order_id,
                    "total_money": {"amount": amount_cents, "currency": "USD"},
                    "note": note,
                }
            }
        }
    }


@pytest.fixture
def webhook_client():
    """Flask test client with mocked Supabase for webhook tests."""
    os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake-key")
    os.environ.setdefault("SECRET_KEY", "test-secret")
    # No signature key → verification skipped (sandbox behavior)
    os.environ.pop("SQUARE_WEBHOOK_SIGNATURE_KEY", None)

    from execution.sms_receive import app
    app.config["TESTING"] = True
    return app.test_client()


# ---------------------------------------------------------------------------
# Path A — invoice_links lookup by square_order_id
# ---------------------------------------------------------------------------

def test_payment_completed_marks_invoice_paid_via_link(webhook_client):
    """Square payment.completed with a matching invoice_links row should mark the invoice paid."""
    fake_link = {
        "id": "link-uuid",
        "job_id": FAKE_JOB_ID,
        "client_phone": TEST_CLIENT_PHONE,
        "square_order_id": FAKE_ORDER_ID,
    }
    fake_invoice = {
        "id": FAKE_INVOICE_ID,
        "client_id": TEST_CLIENT_ID,
        "customer_id": "cust-0001",
        "amount_due": 350.00,
    }

    with patch("routes.invoice_routes.get_link_by_square_order", return_value=fake_link) as mock_link, \
         patch("routes.invoice_routes.mark_invoice_paid", return_value=True) as mock_paid, \
         patch("routes.invoice_routes.get_supabase") as mock_sb, \
         patch("routes.invoice_routes._notify_owner_payment_received"):

        # Mock the invoices query (find invoice by job_id)
        mock_table = MagicMock()
        for m in ("select", "eq", "order", "limit"):
            getattr(mock_table, m).return_value = mock_table
        result = MagicMock()
        result.data = [fake_invoice]
        mock_table.execute.return_value = result
        mock_sb.return_value.table.return_value = mock_table

        payload = _square_payload()
        resp = webhook_client.post(
            "/webhooks/square",
            data=json.dumps(payload),
            content_type="application/json",
        )

        assert resp.status_code == 200
        mock_link.assert_called_once_with(FAKE_ORDER_ID)
        mock_paid.assert_called_once_with(FAKE_INVOICE_ID, square_payment_id=FAKE_PAYMENT_ID)


# ---------------------------------------------------------------------------
# Path B — payment_note fallback
# ---------------------------------------------------------------------------

def test_payment_completed_marks_invoice_paid_via_note_fallback(webhook_client):
    """When invoice_links lookup fails, fall back to parsing invoice_id from payment_note."""
    fake_invoice = {
        "id": FAKE_INVOICE_ID,
        "client_id": TEST_CLIENT_ID,
        "customer_id": "cust-0001",
        "amount_due": 350.00,
    }

    with patch("routes.invoice_routes.get_link_by_square_order", return_value=None), \
         patch("routes.invoice_routes.mark_invoice_paid", return_value=True) as mock_paid, \
         patch("routes.invoice_routes.get_supabase") as mock_sb, \
         patch("routes.invoice_routes._notify_owner_payment_received"):

        mock_table = MagicMock()
        for m in ("select", "eq", "order", "limit"):
            getattr(mock_table, m).return_value = mock_table
        result = MagicMock()
        result.data = [fake_invoice]
        mock_table.execute.return_value = result
        mock_sb.return_value.table.return_value = mock_table

        payload = _square_payload(
            note=f"Bolts11 Invoice {FAKE_INVOICE_ID} — Alice Acme",
        )
        resp = webhook_client.post(
            "/webhooks/square",
            data=json.dumps(payload),
            content_type="application/json",
        )

        assert resp.status_code == 200
        mock_paid.assert_called_once_with(FAKE_INVOICE_ID, square_payment_id=FAKE_PAYMENT_ID)


# ---------------------------------------------------------------------------
# Non-payment events should be ignored
# ---------------------------------------------------------------------------

def test_non_payment_event_ignored(webhook_client):
    """Events other than payment.completed should return 200 without processing."""
    payload = _square_payload(event_type="payment.updated")
    resp = webhook_client.post(
        "/webhooks/square",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ignored"


# ---------------------------------------------------------------------------
# Graceful failure — no order_id and no payment_note
# ---------------------------------------------------------------------------

def test_no_order_no_note_returns_200(webhook_client):
    """If we can't resolve an invoice at all, still return 200 (don't cause Square retries)."""
    with patch("routes.invoice_routes.get_link_by_square_order", return_value=None), \
         patch("routes.invoice_routes.get_supabase") as mock_sb:

        mock_table = MagicMock()
        for m in ("select", "eq", "order", "limit"):
            getattr(mock_table, m).return_value = mock_table
        result = MagicMock()
        result.data = []
        mock_table.execute.return_value = result
        mock_sb.return_value.table.return_value = mock_table

        payload = _square_payload(order_id="unknown-order", note="")
        resp = webhook_client.post(
            "/webhooks/square",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 200
