"""
test_customer_import.py — Tests for the onboarding customer import endpoint.

Tests:
  - POST /api/onboarding/<token>/import-customers imports customers
  - Dedupes by phone number
  - Handles empty input gracefully
  - Invalid token returns 404
"""

import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


FAKE_TOKEN = "test-onboard-token-abc123"


@pytest.fixture
def import_client():
    """Flask test client with mocked Supabase for import tests."""
    os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake-key")
    os.environ.setdefault("SECRET_KEY", "test-secret")

    from execution.sms_receive import app
    app.config["TESTING"] = True
    return app.test_client()


def _mock_supabase(session_data=None):
    """Mock Supabase that returns a fake onboarding session."""
    mock_sb = MagicMock()

    def _make_table(name):
        table = MagicMock()
        for m in ("select", "insert", "update", "delete", "eq", "neq",
                  "order", "limit", "single", "is_", "or_", "filter"):
            getattr(table, m).return_value = table
        result = MagicMock()
        if name == "onboarding_sessions" and session_data:
            result.data = [session_data]
        else:
            result.data = []
        result.count = len(result.data)
        table.execute.return_value = result
        return table

    mock_sb.table.side_effect = _make_table
    return mock_sb


# ---------------------------------------------------------------------------
# Import customers — basic batch
# ---------------------------------------------------------------------------

def test_import_customers_basic(import_client):
    """POST with a list of customers should import them."""
    session = {
        "id": "session-uuid",
        "client_id": "client-uuid",
        "customers_json": [],
    }
    mock_sb = _mock_supabase(session)

    with patch("routes.onboarding_routes._get_supabase", return_value=mock_sb):
        resp = import_client.post(
            f"/api/onboarding/{FAKE_TOKEN}/import-customers",
            data=json.dumps({
                "customers": [
                    {"name": "Alice Smith", "phone": "2075551234", "email": "alice@test.com", "address": "123 Main St"},
                    {"name": "Bob Jones", "phone": "2075555678", "email": "", "address": ""},
                ]
            }),
            content_type="application/json",
        )

    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["success"] is True
    assert data["imported"] == 2
    assert data["total"] == 2


# ---------------------------------------------------------------------------
# Deduplication by phone
# ---------------------------------------------------------------------------

def test_import_dedupes_by_phone(import_client):
    """Duplicate phones should be skipped."""
    session = {
        "id": "session-uuid",
        "client_id": "client-uuid",
        "customers_json": [
            {"name": "Alice Smith", "phone": "+12075551234", "email": "", "address": ""},
        ],
    }
    mock_sb = _mock_supabase(session)

    with patch("routes.onboarding_routes._get_supabase", return_value=mock_sb):
        resp = import_client.post(
            f"/api/onboarding/{FAKE_TOKEN}/import-customers",
            data=json.dumps({
                "customers": [
                    {"name": "Alice Duplicate", "phone": "2075551234", "email": "", "address": ""},
                    {"name": "New Person", "phone": "2075559999", "email": "", "address": ""},
                ]
            }),
            content_type="application/json",
        )

    data = json.loads(resp.data)
    assert data["imported"] == 1
    assert data["skipped"] == 1
    assert data["total"] == 2


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------

def test_import_empty_list(import_client):
    """Empty customer list should return success with 0 imported."""
    session = {"id": "s", "client_id": "c", "customers_json": []}
    mock_sb = _mock_supabase(session)

    with patch("routes.onboarding_routes._get_supabase", return_value=mock_sb):
        resp = import_client.post(
            f"/api/onboarding/{FAKE_TOKEN}/import-customers",
            data=json.dumps({"customers": []}),
            content_type="application/json",
        )

    data = json.loads(resp.data)
    assert data["success"] is True
    assert data["imported"] == 0


# ---------------------------------------------------------------------------
# Invalid token
# ---------------------------------------------------------------------------

def test_import_invalid_token(import_client):
    """Bad token should return 404."""
    mock_sb = _mock_supabase(None)

    with patch("routes.onboarding_routes._get_supabase", return_value=mock_sb):
        resp = import_client.post(
            "/api/onboarding/bad-token/import-customers",
            data=json.dumps({"customers": [{"name": "Test", "phone": "1234567890"}]}),
            content_type="application/json",
        )

    assert resp.status_code == 404
