"""
conftest.py — Shared test fixtures for the Bolts11 test suite.

Provides mock Supabase client, mock client/customer records,
and common test data so individual test files stay focused.
"""

import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Test constants — generic placeholders, no real client data
# ---------------------------------------------------------------------------
TEST_CLIENT_ID = "00000000-0000-0000-0000-000000000001"
TEST_CLIENT_PHONE = "+15555550200"
TEST_OWNER_MOBILE = "+15555550100"
TEST_BUSINESS_NAME = "Test Trades Co"


# ---------------------------------------------------------------------------
# Mock Supabase client
# ---------------------------------------------------------------------------

class MockSupabaseTable:
    """Mock that chains .select().eq().execute() and returns empty by default."""

    def __init__(self, data=None):
        self._data = data or []

    def select(self, *args, **kwargs):
        return self

    def insert(self, *args, **kwargs):
        return self

    def update(self, *args, **kwargs):
        return self

    def delete(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def neq(self, *args, **kwargs):
        return self

    def gt(self, *args, **kwargs):
        return self

    def lt(self, *args, **kwargs):
        return self

    def gte(self, *args, **kwargs):
        return self

    def lte(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def ilike(self, *args, **kwargs):
        return self

    def not_(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        result = MagicMock()
        result.data = self._data
        result.count = len(self._data)
        return result


class MockSupabase:
    """Mock Supabase client. Returns empty results by default."""

    def __init__(self):
        self._tables = {}

    def table(self, name):
        if name not in self._tables:
            self._tables[name] = MockSupabaseTable()
        return self._tables[name]

    def set_table_data(self, name, data):
        """Pre-load data for a specific table."""
        self._tables[name] = MockSupabaseTable(data)


@pytest.fixture
def mock_supabase():
    """Provides a mock Supabase client that returns empty lists by default."""
    return MockSupabase()


# ---------------------------------------------------------------------------
# Mock client record — Test Trades Co
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_client():
    """Generic test client record — no real client data."""
    return {
        "id": TEST_CLIENT_ID,
        "business_name": TEST_BUSINESS_NAME,
        "owner_name": "Test Owner",
        "phone": TEST_CLIENT_PHONE,
        "owner_mobile": TEST_OWNER_MOBILE,
        "personality": (
            "I am the owner of a trades business.\n"
            "Hourly rate: $125/hr\n"
            "Overtime (after 8hrs or weekends): $175/hr\n"
            "Minimum charge: $150\n"
        ),
        "active": True,
        "pin_hash": None,
        "is_super_admin": True,
    }


# ---------------------------------------------------------------------------
# Mock customer record
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_customer():
    """A test customer with all required fields populated."""
    return {
        "id": "cust-0001-test-uuid",
        "client_id": TEST_CLIENT_ID,
        "customer_name": "Alice Acme",
        "customer_phone": "+12075551234",
        "customer_email": "alice@example.com",
        "customer_address": "42 Oak Street, Belfast, ME 04915",
        "notes": "1000 gal tank, last pumped 2024",
        "sms_consent": True,
        "sms_consent_at": "2026-01-15T12:00:00+00:00",
        "sms_consent_src": "owner_command",
        "created_at": "2026-01-10T08:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Mock job record
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_job():
    """A test job for Test Trades Co."""
    return {
        "id": "job-0001-test-uuid",
        "client_id": TEST_CLIENT_ID,
        "customer_id": "cust-0001-test-uuid",
        "job_type": "pump",
        "job_description": "Septic pump-out — 1,000 gal. tank",
        "status": "scheduled",
        "dispatch_status": "unassigned",
        "scheduled_date": "2026-03-29",
        "estimated_amount": 275.00,
        "raw_input": "pump out for Alice Acme",
    }
