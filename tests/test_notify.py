"""
test_notify.py — Tests for the unified notification router.

Tests the 3-layer permission check:
  Layer 1: Client sms_outbound_enabled / email_outbound_enabled
  Layer 2: Employee (internal) vs Customer (external)
  Layer 3: Customer sms_consent / Employee sms_opted_out
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAKE_CLIENT_ID = "00000000-0000-0000-0000-000000000001"


def _mock_client(sms_enabled=False, email_enabled=True):
    return {
        "id": FAKE_CLIENT_ID,
        "phone": "+15555550200",
        "owner_mobile": "+15555550100",
        "business_name": "Test Trades Co",
        "sms_outbound_enabled": sms_enabled,
        "email_outbound_enabled": email_enabled,
    }


def _mock_employee(opted_out=False):
    return {
        "type": "employee",
        "name": "Jesse",
        "email": "jesse@test.com",
        "sms_consent": True,
        "sms_opted_out": opted_out,
    }


def _mock_customer(consent=False, email=None):
    return {
        "type": "customer",
        "name": "Alice",
        "email": email,
        "sms_consent": consent,
        "sms_opted_out": False,
    }


# ---------------------------------------------------------------------------
# Layer 1: Client switches
# ---------------------------------------------------------------------------

def test_sms_blocked_when_client_disabled():
    """SMS should be blocked when sms_outbound_enabled is false."""
    from execution.notify import _can_sms
    client = _mock_client(sms_enabled=False)
    recipient = _mock_employee()
    assert _can_sms(client, recipient) is False


def test_sms_allowed_when_client_enabled():
    """SMS should be allowed when sms_outbound_enabled is true + employee."""
    from execution.notify import _can_sms
    client = _mock_client(sms_enabled=True)
    recipient = _mock_employee()
    assert _can_sms(client, recipient) is True


# ---------------------------------------------------------------------------
# Layer 2 + 3: Employee vs Customer
# ---------------------------------------------------------------------------

def test_employee_sms_allowed_without_consent():
    """Internal employees don't need CTIA consent for SMS."""
    from execution.notify import _can_sms
    client = _mock_client(sms_enabled=True)
    recipient = _mock_employee()
    assert _can_sms(client, recipient) is True


def test_employee_sms_blocked_when_opted_out():
    """Employee who texted STOP should not receive SMS."""
    from execution.notify import _can_sms
    client = _mock_client(sms_enabled=True)
    recipient = _mock_employee(opted_out=True)
    assert _can_sms(client, recipient) is False


def test_customer_sms_blocked_without_consent():
    """Customer without sms_consent should not receive SMS."""
    from execution.notify import _can_sms
    client = _mock_client(sms_enabled=True)
    recipient = _mock_customer(consent=False)
    assert _can_sms(client, recipient) is False


def test_customer_sms_allowed_with_consent():
    """Customer with sms_consent=true should receive SMS."""
    from execution.notify import _can_sms
    client = _mock_client(sms_enabled=True)
    recipient = _mock_customer(consent=True)
    assert _can_sms(client, recipient) is True


# ---------------------------------------------------------------------------
# Email fallback
# ---------------------------------------------------------------------------

def test_email_allowed_with_address():
    """Email should work when enabled and address exists."""
    from execution.notify import _can_email
    client = _mock_client(email_enabled=True)
    recipient = _mock_customer(email="alice@test.com")
    assert _can_email(client, recipient) is True


def test_email_blocked_without_address():
    """Email should be blocked when no address on file."""
    from execution.notify import _can_email
    client = _mock_client(email_enabled=True)
    recipient = _mock_customer(email=None)
    assert _can_email(client, recipient) is False


def test_email_blocked_when_disabled():
    """Email should be blocked when email_outbound_enabled is false."""
    from execution.notify import _can_email
    client = _mock_client(email_enabled=False)
    recipient = _mock_customer(email="alice@test.com")
    assert _can_email(client, recipient) is False


# ---------------------------------------------------------------------------
# Full notify() routing
# ---------------------------------------------------------------------------

def test_notify_falls_back_to_email_when_sms_disabled():
    """With SMS disabled and email available, should deliver via email."""
    with patch("execution.notify._get_client_settings", return_value=_mock_client(sms_enabled=False)), \
         patch("execution.notify._lookup_recipient", return_value=_mock_customer(email="alice@test.com")), \
         patch("execution.notify._log_blocked"), \
         patch("execution.resend_agent._send", return_value={"success": True, "id": "test"}) as mock_send:

        from execution.notify import notify
        result = notify(FAKE_CLIENT_ID, "+15551234567", "Test message")

    assert result["success"] is True
    assert result["channel"] == "email"
    mock_send.assert_called_once()


def test_notify_blocked_when_no_channel_available():
    """With SMS disabled and no email, should return blocked."""
    with patch("execution.notify._get_client_settings", return_value=_mock_client(sms_enabled=False)), \
         patch("execution.notify._lookup_recipient", return_value=_mock_customer(consent=False, email=None)), \
         patch("execution.notify._log_blocked") as mock_log:

        from execution.notify import notify
        result = notify(FAKE_CLIENT_ID, "+15551234567", "Test message")

    assert result["success"] is False
    assert result["channel"] == "blocked"
    mock_log.assert_called_once()


def test_notify_uses_sms_when_enabled_and_consented():
    """With SMS enabled and customer consent, should use SMS."""
    with patch("execution.notify._get_client_settings", return_value=_mock_client(sms_enabled=True)), \
         patch("execution.notify._lookup_recipient", return_value=_mock_customer(consent=True, email="a@b.com")), \
         patch("execution.sms_send.send_sms", return_value={"success": True, "message_id": "test123"}) as mock_sms:

        from execution.notify import notify
        result = notify(FAKE_CLIENT_ID, "+15551234567", "Test message")

    assert result["success"] is True
    assert result["channel"] == "sms"
    mock_sms.assert_called_once()
