"""
test_dashboard_routes.py — Regression tests for dashboard route responses.

Tests:
  - GET /dashboard/ returns 200 (Bug 1: was 500 due to duplicate Jinja2 block)
  - GET /dashboard/jobs, /dashboard/jobs/ return 200 (Bug 2: trailing slash 404s)
  - GET /dashboard/proposals, /dashboard/proposals/ return 200
  - GET /dashboard/dispatch, /dashboard/dispatch/ return 200
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import TEST_CLIENT_ID


@pytest.fixture
def app_client():
    """Create a Flask test client with mocked Supabase."""
    # Ensure env vars are set before import
    os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake-key")
    os.environ.setdefault("SECRET_KEY", "test-secret")

    # Build a mock that chains any method call and returns empty data
    def _make_mock_table():
        table = MagicMock()
        # Every method returns self so .select().eq().is_().execute() chains
        for m in ("select", "insert", "update", "delete", "eq", "neq",
                  "gt", "lt", "gte", "lte", "in_", "ilike", "not_",
                  "order", "limit", "single", "is_", "or_", "filter"):
            getattr(table, m).return_value = table
        result = MagicMock()
        result.data = []
        result.count = 0
        table.execute.return_value = result
        return table

    mock_sb = MagicMock()
    mock_sb.table.side_effect = lambda name: _make_mock_table()

    with patch("routes.dashboard_routes._get_supabase", return_value=mock_sb):
        from execution.sms_receive import app
        app.config["TESTING"] = True
        client = app.test_client()
        # Set a valid session so routes don't redirect to /login
        with client.session_transaction() as sess:
            sess["client_id"] = TEST_CLIENT_ID
        yield client


# ---------------------------------------------------------------------------
# Bug 1 — GET /dashboard/ must return 200 (was 500)
# ---------------------------------------------------------------------------

def test_dashboard_index_returns_200(app_client):
    """GET /dashboard/ should render the control board, not 500."""
    resp = app_client.get("/dashboard/")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"


def test_dashboard_no_trailing_slash_returns_200(app_client):
    """GET /dashboard (no slash) should also work (redirect or 200)."""
    resp = app_client.get("/dashboard")
    assert resp.status_code in (200, 301, 302, 308)


# ---------------------------------------------------------------------------
# Bug 2 — Routes that were 404ing
# ---------------------------------------------------------------------------

def test_dashboard_dispatch_returns_200(app_client):
    resp = app_client.get("/dashboard/dispatch")
    assert resp.status_code == 200, f"/dashboard/dispatch → {resp.status_code}"


def test_dashboard_dispatch_trailing_slash(app_client):
    resp = app_client.get("/dashboard/dispatch/")
    assert resp.status_code in (200, 301, 302, 308), f"/dashboard/dispatch/ → {resp.status_code}"


def test_dashboard_customers_returns_200(app_client):
    resp = app_client.get("/dashboard/customers/")
    assert resp.status_code == 200, f"/dashboard/customers/ → {resp.status_code}"


def test_dashboard_estimates_returns_200(app_client):
    resp = app_client.get("/dashboard/estimates/")
    assert resp.status_code == 200, f"/dashboard/estimates/ → {resp.status_code}"


def test_dashboard_invoices_returns_200(app_client):
    resp = app_client.get("/dashboard/invoices/")
    assert resp.status_code == 200, f"/dashboard/invoices/ → {resp.status_code}"


def test_dashboard_workers_returns_200(app_client):
    resp = app_client.get("/dashboard/workers")
    assert resp.status_code == 200, f"/dashboard/workers → {resp.status_code}"


def test_dashboard_workers_trailing_slash(app_client):
    resp = app_client.get("/dashboard/workers/")
    assert resp.status_code in (200, 301, 302, 308), f"/dashboard/workers/ → {resp.status_code}"


# ---------------------------------------------------------------------------
# Missing routes — /jobs and /proposals (should redirect)
# ---------------------------------------------------------------------------

def test_dashboard_jobs_redirects(app_client):
    """GET /dashboard/jobs should redirect to control board."""
    resp = app_client.get("/dashboard/jobs")
    assert resp.status_code in (301, 302, 308), f"/dashboard/jobs → {resp.status_code}"


def test_dashboard_jobs_trailing_slash_redirects(app_client):
    resp = app_client.get("/dashboard/jobs/")
    assert resp.status_code in (301, 302, 308), f"/dashboard/jobs/ → {resp.status_code}"


def test_dashboard_proposals_redirects(app_client):
    """GET /dashboard/proposals should redirect to estimates."""
    resp = app_client.get("/dashboard/proposals")
    assert resp.status_code in (301, 302, 308), f"/dashboard/proposals → {resp.status_code}"


def test_dashboard_proposals_trailing_slash_redirects(app_client):
    resp = app_client.get("/dashboard/proposals/")
    assert resp.status_code in (301, 302, 308), f"/dashboard/proposals/ → {resp.status_code}"


# ---------------------------------------------------------------------------
# Trailing slash consistency — routes that had slash should work without
# ---------------------------------------------------------------------------

def test_dashboard_estimates_no_slash(app_client):
    resp = app_client.get("/dashboard/estimates")
    assert resp.status_code in (200, 301, 302, 308), f"/dashboard/estimates → {resp.status_code}"


def test_dashboard_invoices_no_slash(app_client):
    resp = app_client.get("/dashboard/invoices")
    assert resp.status_code in (200, 301, 302, 308), f"/dashboard/invoices → {resp.status_code}"


def test_dashboard_customers_no_slash(app_client):
    resp = app_client.get("/dashboard/customers")
    assert resp.status_code in (200, 301, 302, 308), f"/dashboard/customers → {resp.status_code}"
