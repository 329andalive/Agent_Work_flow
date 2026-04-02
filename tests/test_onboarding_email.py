"""
test_onboarding_email.py — Tests for onboarding create + auto-email.

Tests:
  - POST /api/onboarding/create with email sends onboarding invite
  - POST /api/onboarding/create without email skips email, still succeeds
  - send_onboarding_invite builds correct email content
"""

import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def onboard_client():
    """Flask test client with mocked Supabase for onboarding tests."""
    os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake-key")
    os.environ.setdefault("SECRET_KEY", "test-secret")

    from execution.sms_receive import app
    app.config["TESTING"] = True
    return app.test_client()


def _mock_supabase():
    """Mock Supabase that returns fake insert results."""
    mock_sb = MagicMock()

    def _make_table(name):
        table = MagicMock()
        for m in ("select", "insert", "update", "delete", "eq", "neq",
                  "order", "limit", "single", "is_", "or_", "filter"):
            getattr(table, m).return_value = table
        result = MagicMock()
        if name == "clients":
            result.data = [{"id": "client-new-uuid"}]
        elif name == "onboarding_sessions":
            result.data = [{"id": "session-new-uuid", "token": "fake-token"}]
        else:
            result.data = []
        result.count = len(result.data)
        table.execute.return_value = result
        return table

    mock_sb.table.side_effect = _make_table
    return mock_sb


# ---------------------------------------------------------------------------
# Create with email — sends onboarding invite
# ---------------------------------------------------------------------------

def test_create_with_email_sends_invite(onboard_client):
    """POST /api/onboarding/create with owner_email should send the setup link."""
    mock_sb = _mock_supabase()

    with patch("routes.onboarding_routes._get_supabase", return_value=mock_sb), \
         patch("execution.resend_agent.send_onboarding_invite", return_value={"success": True}) as mock_send:

        resp = onboard_client.post(
            "/api/onboarding/create",
            data=json.dumps({
                "client_name": "Joe's Plumbing",
                "owner_name": "Joe",
                "owner_mobile": "+12075551234",
                "owner_email": "joe@example.com",
                "trade_vertical": "Plumbing",
            }),
            content_type="application/json",
        )

    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["success"] is True
    assert data["email_sent"] is True
    assert data["email_to"] == "joe@example.com"
    assert "onboarding_url" in data

    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args[1]
    assert call_kwargs["email"] == "joe@example.com"
    assert call_kwargs["business_name"] == "Joe's Plumbing"
    assert "/onboard/" in call_kwargs["onboarding_url"]


# ---------------------------------------------------------------------------
# Create without email — skips email, still succeeds
# ---------------------------------------------------------------------------

def test_create_without_email_skips_invite(onboard_client):
    """POST /api/onboarding/create without owner_email should skip the email."""
    mock_sb = _mock_supabase()

    with patch("routes.onboarding_routes._get_supabase", return_value=mock_sb):
        resp = onboard_client.post(
            "/api/onboarding/create",
            data=json.dumps({
                "client_name": "Joe's Plumbing",
                "owner_name": "Joe",
                "owner_mobile": "+12075551234",
            }),
            content_type="application/json",
        )

    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["success"] is True
    assert data["email_sent"] is False
    assert data["email_to"] is None


# ---------------------------------------------------------------------------
# send_onboarding_invite builds correct email
# ---------------------------------------------------------------------------

def test_onboarding_invite_email_content():
    """send_onboarding_invite should include the setup link and business name."""
    with patch("execution.resend_agent._send", return_value={"success": True, "id": "test-123"}) as mock_send:
        from execution.resend_agent import send_onboarding_invite
        result = send_onboarding_invite(
            name="Joe Smith",
            email="joe@example.com",
            business_name="Joe's Plumbing",
            onboarding_url="https://app.bolts11.com/onboard/abc123",
        )

    assert result["success"] is True
    mock_send.assert_called_once()
    call_args = mock_send.call_args
    assert call_args[1]["to"] == ["joe@example.com"]
    assert "Joe" in call_args[1]["subject"]
    assert "https://app.bolts11.com/onboard/abc123" in call_args[1]["html"]
    assert "Joe's Plumbing" in call_args[1]["html"]
