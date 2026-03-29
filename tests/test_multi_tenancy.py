"""
test_multi_tenancy.py — Multi-tenancy safety tests (T4)

Verifies that every list/query function filters by client_id so
no data from one business can leak to another.

Strategy: mock Supabase to hold rows from TWO clients. Call each query
with client_id="client-A" and assert zero rows from "client-B" appear.
The mock faithfully applies .eq() filters, so a missing client_id filter
would return both clients' data — and the test would catch it.
"""

import sys
import os
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CLIENT_A = "client-A-uuid"
CLIENT_B = "client-B-uuid"


# ---------------------------------------------------------------------------
# Filtering mock Supabase — applies .eq() filters against seeded rows
# ---------------------------------------------------------------------------

class FilteringQuery:
    """Chains Supabase query methods and applies .eq() filters on execute()."""

    def __init__(self, rows):
        self._rows = list(rows)
        self._filters = []
        self._single = False

    def select(self, *a, **kw):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def lt(self, col, val):
        # For overdue invoices — don't filter in tests, just pass through
        return self

    def order(self, *a, **kw):
        return self

    def single(self):
        self._single = True
        return self

    def execute(self):
        rows = self._rows
        for col, val in self._filters:
            rows = [r for r in rows if r.get(col) == val]
        result = MagicMock()
        if getattr(self, "_single", False):
            # .single() returns one dict or raises — mirror that behavior
            result.data = rows[0] if rows else None
            if not rows:
                raise Exception("No rows found")
        else:
            result.data = rows
        result.count = len(rows) if isinstance(rows, list) else (1 if rows else 0)
        return result


class FilteringSupabase:
    """Mock Supabase client that seeds rows per table and filters on query."""

    def __init__(self):
        self._tables = {}

    def seed(self, table_name, rows):
        self._tables[table_name] = rows

    def table(self, name):
        return FilteringQuery(self._tables.get(name, []))


# ---------------------------------------------------------------------------
# Seed data — two clients, distinct rows
# ---------------------------------------------------------------------------

CUSTOMER_A = {
    "id": "cust-a1",
    "client_id": CLIENT_A,
    "customer_name": "Alice A",
    "customer_phone": "+12075550001",
}
CUSTOMER_B = {
    "id": "cust-b1",
    "client_id": CLIENT_B,
    "customer_name": "Bob B",
    "customer_phone": "+12075550002",
}

yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()

INVOICE_A = {
    "id": "inv-a1",
    "client_id": CLIENT_A,
    "status": "sent",
    "due_date": yesterday,
    "amount_due": 350.00,
}
INVOICE_B = {
    "id": "inv-b1",
    "client_id": CLIENT_B,
    "status": "sent",
    "due_date": yesterday,
    "amount_due": 500.00,
}

JOB_A = {
    "id": "job-a1",
    "client_id": CLIENT_A,
    "status": "scheduled",
    "created_at": "2026-03-29T08:00:00",
}
JOB_B = {
    "id": "job-b1",
    "client_id": CLIENT_B,
    "status": "scheduled",
    "created_at": "2026-03-29T09:00:00",
}


def _make_supabase():
    sb = FilteringSupabase()
    sb.seed("customers", [CUSTOMER_A, CUSTOMER_B])
    sb.seed("invoices", [INVOICE_A, INVOICE_B])
    sb.seed("jobs", [JOB_A, JOB_B])
    return sb


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@patch("execution.db_customer.get_client", side_effect=lambda: _make_supabase())
def test_customer_query_filters_by_client_id(mock_gc):
    from execution.db_customer import get_customer_by_phone

    result = get_customer_by_phone(CLIENT_A, "+12075550001")
    # Should find Alice (client A)
    assert result is not None
    assert result["client_id"] == CLIENT_A

    # Should NOT find Bob's number under client A
    result_b = get_customer_by_phone(CLIENT_A, "+12075550002")
    assert result_b is None or result_b.get("client_id") != CLIENT_B, \
        "TENANT LEAK: client-B customer returned for client-A query"


@patch("execution.db_invoices.get_client", side_effect=lambda: _make_supabase())
def test_invoice_query_filters_by_client_id(mock_gc):
    from execution.db_invoices import get_overdue_invoices

    results = get_overdue_invoices(CLIENT_A)
    client_ids = {r["client_id"] for r in results}
    assert CLIENT_B not in client_ids, \
        "TENANT LEAK: client-B invoice returned in client-A overdue query"
    assert all(r["client_id"] == CLIENT_A for r in results)


@patch("execution.db_jobs.get_client", side_effect=lambda: _make_supabase())
def test_job_query_filters_by_client_id(mock_gc):
    from execution.db_jobs import get_jobs_by_client

    results = get_jobs_by_client(CLIENT_A)
    client_ids = {r["client_id"] for r in results}
    assert CLIENT_B not in client_ids, \
        "TENANT LEAK: client-B job returned in client-A job query"
    assert all(r["client_id"] == CLIENT_A for r in results)
