"""Tests for the email delivery bridge (T6)."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from execution.email_send import (
    send_invoice_email,
    send_proposal_email,
    _build_invoice_html,
)

_COMMON_KWARGS = dict(
    to_name="Test",
    from_name="B&B Septic",
    customer_name="Test Customer",
    business_name="B&B Septic",
    line_items=[{"description": "Pump-out", "amount": 325.00}],
    subtotal=325.00,
    tax_amount=0.0,
    total=325.00,
)


def test_send_invoice_email_no_api_key(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    result = send_invoice_email(
        to_email="test@example.com", invoice_id="abc123", **_COMMON_KWARGS
    )
    assert result["success"] is False
    assert result.get("error") is not None


def test_send_invoice_email_no_customer_email(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    result = send_invoice_email(
        to_email="", invoice_id="abc123", **_COMMON_KWARGS
    )
    assert result["success"] is False
    assert result.get("error") is not None


def test_send_proposal_email_no_api_key(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    result = send_proposal_email(
        to_email="test@example.com", proposal_id="abc123", **_COMMON_KWARGS
    )
    assert result["success"] is False


def test_invoice_html_contains_pay_button():
    html = _build_invoice_html(
        customer_name="Test",
        business_name="B&B Septic",
        invoice_id="abc12345",
        line_items=[{"description": "Pump-out", "amount": 325.00}],
        subtotal=325.00,
        tax_amount=0.0,
        total=325.00,
        payment_link_url="https://sandbox.square.link/u/test123",
    )
    assert "PAY NOW" in html
    assert "sandbox.square.link/u/test123" in html


def test_invoice_html_no_pay_button_when_no_link():
    html = _build_invoice_html(
        customer_name="Test",
        business_name="B&B Septic",
        invoice_id="abc12345",
        line_items=[{"description": "Pump-out", "amount": 325.00}],
        subtotal=325.00,
        tax_amount=0.0,
        total=325.00,
        payment_link_url=None,
    )
    assert "PAY NOW" not in html
