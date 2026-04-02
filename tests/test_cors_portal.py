"""
test_cors_portal.py — Tests for CORS preflight and portal endpoints.

Tests:
  - OPTIONS /api/access-request returns 200 with CORS headers (was 404)
  - OPTIONS /api/auth/portal-login returns 200 with CORS headers (was 404)
  - POST /api/access-request with valid data succeeds
  - POST /api/auth/portal-login with unknown phone returns 401
  - CORS origin reflects allowed origins correctly
"""

import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def portal_client():
    """Flask test client for portal/CORS tests."""
    os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake-key")
    os.environ.setdefault("SECRET_KEY", "test-secret")

    from execution.sms_receive import app
    app.config["TESTING"] = True
    return app.test_client()


# ---------------------------------------------------------------------------
# OPTIONS preflight — must return 200, not 404
# ---------------------------------------------------------------------------

def test_options_access_request_returns_200(portal_client):
    """OPTIONS /api/access-request must return 200 with CORS headers."""
    resp = portal_client.options(
        "/api/access-request",
        headers={"Origin": "https://bolts11.com"},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert "Access-Control-Allow-Origin" in resp.headers
    assert "Access-Control-Allow-Methods" in resp.headers


def test_options_portal_login_returns_200(portal_client):
    """OPTIONS /api/auth/portal-login must return 200 with CORS headers."""
    resp = portal_client.options(
        "/api/auth/portal-login",
        headers={"Origin": "https://bolts11.com"},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert "Access-Control-Allow-Origin" in resp.headers


# ---------------------------------------------------------------------------
# CORS origin handling
# ---------------------------------------------------------------------------

def test_cors_reflects_allowed_origin(portal_client):
    """CORS should reflect the request origin when it's in the allowlist."""
    resp = portal_client.options(
        "/api/access-request",
        headers={"Origin": "https://www.bolts11.com"},
    )
    assert resp.headers["Access-Control-Allow-Origin"] == "https://www.bolts11.com"


def test_cors_defaults_for_unknown_origin(portal_client):
    """CORS should default to bolts11.com for unknown origins."""
    resp = portal_client.options(
        "/api/access-request",
        headers={"Origin": "https://evil.example.com"},
    )
    assert resp.headers["Access-Control-Allow-Origin"] == "https://bolts11.com"


# ---------------------------------------------------------------------------
# POST /api/access-request
# ---------------------------------------------------------------------------

def test_access_request_post_succeeds(portal_client):
    """POST with valid data should insert and return success."""
    def _make_mock_table():
        table = MagicMock()
        for m in ("select", "insert", "update", "delete", "eq", "neq",
                  "order", "limit", "execute"):
            getattr(table, m).return_value = table
        result = MagicMock()
        result.data = [{"id": "new-row"}]
        table.execute.return_value = result
        return table

    mock_sb = MagicMock()
    mock_sb.table.side_effect = lambda name: _make_mock_table()

    with patch("routes.access_request_routes._get_supabase", return_value=mock_sb), \
         patch("execution.resend_agent.send_access_request_confirmation", return_value={"success": True}), \
         patch("execution.resend_agent.send_access_request_alert", return_value={"success": True}):

        resp = portal_client.post(
            "/api/access-request",
            data=json.dumps({
                "name": "Joe Plumber",
                "phone": "2075551234",
                "email": "joe@example.com",
                "business_type": "plumbing",
            }),
            content_type="application/json",
            headers={"Origin": "https://bolts11.com"},
        )

    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["success"] is True


def test_access_request_missing_fields_returns_400(portal_client):
    """POST with missing required fields should return 400."""
    resp = portal_client.post(
        "/api/access-request",
        data=json.dumps({"name": "Joe"}),
        content_type="application/json",
        headers={"Origin": "https://bolts11.com"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/auth/portal-login
# ---------------------------------------------------------------------------

def test_portal_login_unknown_phone_returns_401(portal_client):
    """Login with unrecognized phone should return 401."""
    def _make_mock_table():
        table = MagicMock()
        for m in ("select", "eq", "execute"):
            getattr(table, m).return_value = table
        result = MagicMock()
        result.data = []
        table.execute.return_value = result
        return table

    mock_sb = MagicMock()
    mock_sb.table.side_effect = lambda name: _make_mock_table()

    with patch("routes.access_request_routes._get_supabase", return_value=mock_sb):
        resp = portal_client.post(
            "/api/auth/portal-login",
            data=json.dumps({"phone": "2079999999", "pin": "1234"}),
            content_type="application/json",
            headers={"Origin": "https://bolts11.com"},
        )

    assert resp.status_code == 401
