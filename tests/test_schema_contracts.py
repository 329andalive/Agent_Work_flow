"""
test_schema_contracts.py — Verify DB columns match code expectations (T5).

These tests connect to the real Supabase database and check that every
column the code writes to actually exists. Catches "column not found"
errors before they hit production.

Run:  pytest tests/test_schema_contracts.py -v -m integration
Skip: these are skipped in normal pytest runs (no -m integration flag)
"""

import os
import sys

import pytest
import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytestmark = pytest.mark.integration

# Cache to avoid hitting the API once per test
_column_cache: dict[str, set] = {}


def get_table_columns(table_name: str) -> set:
    """
    Query Supabase PostgREST for actual columns in a table.

    Uses the OpenAPI definition endpoint which returns column definitions
    for every table without needing information_schema access.
    """
    if table_name in _column_cache:
        return _column_cache[table_name]

    from dotenv import load_dotenv
    load_dotenv()

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]

    # PostgREST OpenAPI spec lists all tables and their columns
    resp = httpx.get(
        f"{url}/rest/v1/",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
        },
        timeout=15,
    )
    resp.raise_for_status()
    spec = resp.json()

    # OpenAPI spec: definitions.<TableName>.properties has the columns
    definitions = spec.get("definitions", {})
    for def_name, def_body in definitions.items():
        props = def_body.get("properties", {})
        _column_cache[def_name] = set(props.keys())

    return _column_cache.get(table_name, set())


def _assert_columns(table_name: str, required: set):
    """Assert all required columns exist, with actionable failure message."""
    actual = get_table_columns(table_name)
    if not actual:
        pytest.fail(
            f"Table '{table_name}' not found in Supabase schema. "
            f"Check that the table exists and is exposed via PostgREST."
        )
    missing = required - actual
    if missing:
        sql_fixes = "\n".join(
            f"    ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {col} TEXT;"
            for col in sorted(missing)
        )
        pytest.fail(
            f"MISSING COLUMNS in '{table_name}' table: {missing}\n"
            f"Run this SQL to fix:\n{sql_fixes}"
        )


def test_jobs_table_has_required_columns():
    _assert_columns("jobs", {
        "id", "client_id", "customer_id", "job_type", "job_description",
        "job_address", "status", "scheduled_date", "estimated_amount",
        "agent_used", "raw_input", "job_notes", "created_at",
    })


def test_customers_table_has_required_columns():
    _assert_columns("customers", {
        "id", "client_id", "customer_name", "customer_phone", "customer_address",
        "customer_email", "sms_consent", "notes", "created_at",
    })


def test_invoices_table_has_required_columns():
    _assert_columns("invoices", {
        "id", "client_id", "job_id", "status", "amount_due",
        "tax_amount", "tax_rate", "payment_link_url", "square_payment_link_id",
        "square_order_id", "square_payment_id", "paid_at", "created_at",
    })


def test_proposals_table_has_required_columns():
    _assert_columns("proposals", {
        "id", "client_id", "job_id", "customer_id", "status",
        "amount_estimate", "line_items", "created_at",
    })


def test_invoice_links_table_has_required_columns():
    _assert_columns("invoice_links", {
        "id", "token", "job_id", "client_phone", "type",
        "square_order_id", "square_payment_link_id", "payment_link_url",
        "expires_at", "created_at",
    })


def test_jobs_reschedule_columns():
    """Verify columns used by the reschedule API exist."""
    cols = get_table_columns("jobs")
    required = {"id", "client_id", "scheduled_date", "status", "assigned_worker_id"}
    missing = required - cols
    if missing:
        sql_fixes = "\n".join(
            f"    ALTER TABLE jobs ADD COLUMN IF NOT EXISTS {col} TEXT;"
            for col in sorted(missing)
        )
        pytest.fail(
            f"MISSING columns in 'jobs': {missing}\n"
            f"Run this SQL to fix:\n{sql_fixes}"
        )
