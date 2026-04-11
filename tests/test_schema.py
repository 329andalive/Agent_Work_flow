"""
test_schema.py — Regression guards for execution/schema.py.

This file is the safety net for the single-source-of-truth schema
constants. Every test in here exists because we shipped a column-name
bug at least once. The pattern is:

  - Assert the LIVE column name is present on the schema class
  - Assert the DEAD alternative name (the one that bit us in
    production) is NOT present, so it can never come back via
    "the old name should still work" muscle memory

Adding a new dead-column guard takes about 30 seconds and pays off the
first time someone tries to "fix" it back. The cost of running these
tests is microseconds — they're pure attribute lookups, no I/O.
"""

import inspect

import execution.schema as schema


# ---------------------------------------------------------------------------
# Universal sanity — every class has a TABLE constant
# ---------------------------------------------------------------------------

def test_every_schema_class_has_a_table_constant():
    """
    Every class in schema.py represents a Supabase table and must have
    a TABLE constant pointing at the literal table name. If you add a
    class without TABLE, this catches it.
    """
    table_classes = [
        cls for name, cls in inspect.getmembers(schema, inspect.isclass)
        if cls.__module__ == schema.__name__
    ]
    assert table_classes, "schema.py should define at least one table class"
    for cls in table_classes:
        assert hasattr(cls, "TABLE"), (
            f"{cls.__name__} is missing the TABLE constant — every class "
            f"in schema.py must declare its underlying Supabase table name."
        )
        assert isinstance(cls.TABLE, str) and cls.TABLE, (
            f"{cls.__name__}.TABLE must be a non-empty string"
        )
        # No camelCase / PascalCase / spaces in real Supabase table names
        assert cls.TABLE == cls.TABLE.lower(), (
            f"{cls.__name__}.TABLE = {cls.TABLE!r} should be lowercase_snake_case"
        )


def test_every_constant_is_a_string():
    """Class attributes on schema tables must all be plain strings."""
    table_classes = [
        cls for name, cls in inspect.getmembers(schema, inspect.isclass)
        if cls.__module__ == schema.__name__
    ]
    for cls in table_classes:
        for attr in vars(cls):
            if attr.startswith("_"):
                continue
            value = getattr(cls, attr)
            assert isinstance(value, str), (
                f"{cls.__name__}.{attr} should be a string, got {type(value).__name__}"
            )


# ---------------------------------------------------------------------------
# Customers — the address/phone/name/email confusion
# ---------------------------------------------------------------------------

def test_customers_uses_prefixed_names():
    """
    The customers table uses the customer_* prefix for the
    homeowner-facing fields. Every other spelling we've ever used
    (`name`, `phone`, `email`, `address`) was wrong and caused at
    least one production bug.
    """
    from execution.schema import Customers
    assert Customers.CUSTOMER_NAME == "customer_name"
    assert Customers.CUSTOMER_PHONE == "customer_phone"
    assert Customers.CUSTOMER_EMAIL == "customer_email"
    assert Customers.CUSTOMER_ADDRESS == "customer_address"


def test_customers_does_not_define_unprefixed_names():
    """
    Dead-column guard: the unprefixed names (`name`, `phone`, `email`,
    `address`) must NOT be defined on Customers. They were the bug.
    """
    from execution.schema import Customers
    forbidden = ["NAME", "PHONE", "EMAIL", "ADDRESS"]
    for f in forbidden:
        assert not hasattr(Customers, f), (
            f"Customers.{f} must NOT be defined — the column is "
            f"customer_{f.lower()}, not just {f.lower()}. See the "
            f"bug history in CONVENTIONS.md."
        )


# ---------------------------------------------------------------------------
# Proposals — the subtotal/tax/tax_amount columns DO NOT EXIST on this table
# ---------------------------------------------------------------------------

def test_proposals_money_column_is_amount_estimate():
    """The canonical money column on proposals is amount_estimate."""
    from execution.schema import Proposals
    assert Proposals.AMOUNT_ESTIMATE == "amount_estimate"


def test_proposals_does_not_define_subtotal_or_tax_columns():
    """
    HARD: subtotal, tax_rate, and tax_amount DO NOT EXIST on the
    proposals table. They exist on invoices but writing them to
    proposals raises 'Could not find the subtotal column of proposals'
    in PostgREST. We've shipped a fix for this; this guard prevents
    silent reintroduction.
    """
    from execution.schema import Proposals
    assert not hasattr(Proposals, "SUBTOTAL"), (
        "Proposals.SUBTOTAL must NOT be defined — that column does not "
        "exist on the proposals table. See the April 2026 db_document "
        "fix and the GOTCHA comment block in schema.py."
    )
    assert not hasattr(Proposals, "TAX_RATE"), (
        "Proposals.TAX_RATE must NOT be defined — that column does not "
        "exist on the proposals table."
    )
    assert not hasattr(Proposals, "TAX_AMOUNT"), (
        "Proposals.TAX_AMOUNT must NOT be defined — that column does "
        "not exist on the proposals table."
    )


def test_invoices_does_define_subtotal_and_tax_columns():
    """
    The asymmetry: subtotal/tax_rate/tax_amount DO exist on invoices.
    update_invoice_fields() writes them and has never errored. Document
    the asymmetry by asserting both directions.
    """
    from execution.schema import Invoices
    assert Invoices.SUBTOTAL == "subtotal"
    assert Invoices.TAX_RATE == "tax_rate"
    assert Invoices.TAX_AMOUNT == "tax_amount"


def test_invoices_money_column_is_amount_due():
    """The canonical money column on invoices is amount_due."""
    from execution.schema import Invoices
    assert Invoices.AMOUNT_DUE == "amount_due"


# ---------------------------------------------------------------------------
# pwa_tokens — tech_id, NOT employee_id
# ---------------------------------------------------------------------------

def test_pwa_tokens_uses_tech_id():
    """
    The pwa_tokens table column is `tech_id`, not `employee_id`. This
    bit us once already — the Flask session key IS named employee_id
    (that's a session key, not a DB column), but the DB column on
    pwa_tokens is tech_id.
    """
    from execution.schema import PwaTokens
    assert PwaTokens.TECH_ID == "tech_id"


def test_pwa_tokens_does_not_define_employee_id():
    """Dead-column guard: employee_id must not creep back onto PwaTokens."""
    from execution.schema import PwaTokens
    assert not hasattr(PwaTokens, "EMPLOYEE_ID"), (
        "PwaTokens.EMPLOYEE_ID must NOT be defined — the column on the "
        "pwa_tokens table is tech_id. See the April 2026 pwa_auth fix "
        "and the GOTCHA comment block in schema.py."
    )


# ---------------------------------------------------------------------------
# pwa_chat_messages — this one IS employee_id (asymmetry with pwa_tokens)
# ---------------------------------------------------------------------------

def test_pwa_chat_messages_uses_employee_id():
    """
    Asymmetric to pwa_tokens — this table uses employee_id. Document
    both sides so future devs don't try to "normalize" them.
    """
    from execution.schema import PwaChatMessages
    assert PwaChatMessages.EMPLOYEE_ID == "employee_id"


# ---------------------------------------------------------------------------
# route_assignments / dispatch_decisions — worker_id, NOT employee_id
# ---------------------------------------------------------------------------

def test_route_assignments_uses_worker_id():
    from execution.schema import RouteAssignments
    assert RouteAssignments.WORKER_ID == "worker_id"


def test_route_assignments_does_not_define_employee_id():
    from execution.schema import RouteAssignments
    assert not hasattr(RouteAssignments, "EMPLOYEE_ID"), (
        "RouteAssignments uses worker_id, not employee_id. The whole "
        "dispatch domain is consistent on 'worker' terminology."
    )


def test_dispatch_decisions_uses_worker_id():
    from execution.schema import DispatchDecisions
    assert DispatchDecisions.WORKER_ID == "worker_id"


# ---------------------------------------------------------------------------
# follow_ups — follow_up_type with the underscore
# ---------------------------------------------------------------------------

def test_follow_ups_type_column_has_underscore():
    """
    The column is `follow_up_type` (with the underscore between follow
    and up), not `followup_type`. Both spellings are tempting; only
    one is in the database.
    """
    from execution.schema import FollowUps
    assert FollowUps.FOLLOW_UP_TYPE == "follow_up_type"


# ---------------------------------------------------------------------------
# Legacy tables — agent_activity / needs_attention use client_phone
# ---------------------------------------------------------------------------

def test_agent_activity_uses_client_phone_not_client_id():
    """
    Both agent_activity and needs_attention pre-date the multi-tenant
    ID refactor. They use client_phone (text), not client_id (uuid).
    Don't try to "fix" this without an actual data migration.
    """
    from execution.schema import AgentActivity
    assert AgentActivity.CLIENT_PHONE == "client_phone"
    assert not hasattr(AgentActivity, "CLIENT_ID"), (
        "AgentActivity uses client_phone (legacy), not client_id. "
        "Don't reintroduce CLIENT_ID without a real data migration."
    )


def test_needs_attention_uses_client_phone_not_client_id():
    from execution.schema import NeedsAttention
    assert NeedsAttention.CLIENT_PHONE == "client_phone"
    assert not hasattr(NeedsAttention, "CLIENT_ID"), (
        "NeedsAttention uses client_phone (legacy), not client_id."
    )


# ---------------------------------------------------------------------------
# Time entries — clock_in / clock_out are NOT _at suffixed
# ---------------------------------------------------------------------------

def test_time_entries_uses_unsuffixed_clock_columns():
    """
    Legacy quirk: time_entries.clock_in and clock_out are NOT _at
    suffixed even though our convention says they should be. Don't
    try to "fix" this without a migration.
    """
    from execution.schema import TimeEntries
    assert TimeEntries.CLOCK_IN == "clock_in"
    assert TimeEntries.CLOCK_OUT == "clock_out"
    assert not hasattr(TimeEntries, "CLOCK_IN_AT"), (
        "TimeEntries.CLOCK_IN_AT must not be defined — the actual "
        "column is clock_in (not clock_in_at). Legacy from before the "
        "_at convention existed."
    )


# ---------------------------------------------------------------------------
# Round-trip — every table referenced in CONVENTIONS.md has a class here
# ---------------------------------------------------------------------------

def test_conventions_md_table_helper_map_matches_schema_classes():
    """
    The CONVENTIONS.md "Repository pattern" section lists table-to-
    helper-file mappings. Every table in that map must have a matching
    class in schema.py. If you add a table to one without the other,
    this catches it.
    """
    from pathlib import Path
    src = (Path(__file__).parent.parent / "CONVENTIONS.md").read_text()

    # Tables called out by name in the CONVENTIONS.md helper-file map
    expected_tables = [
        "clients", "customers", "employees", "jobs", "proposals",
        "invoices", "follow_ups", "pwa_tokens", "pwa_chat_messages",
        "agent_activity",
    ]
    for tbl in expected_tables:
        assert f"`{tbl}`" in src, (
            f"CONVENTIONS.md must mention `{tbl}` in the table-to-helper map"
        )

    # Every expected table must have a class in schema.py whose TABLE
    # constant matches
    schema_tables = {
        getattr(cls, "TABLE", None)
        for name, cls in inspect.getmembers(schema, inspect.isclass)
        if cls.__module__ == schema.__name__
    }
    for tbl in expected_tables:
        assert tbl in schema_tables, (
            f"CONVENTIONS.md mentions {tbl!r} but schema.py has no class "
            f"with TABLE = {tbl!r}"
        )


# ---------------------------------------------------------------------------
# Schema constants are usable as Supabase query arguments
# ---------------------------------------------------------------------------

def test_schema_constants_round_trip_to_supabase_query_strings():
    """
    Smoke test: a schema constant should be a drop-in replacement for
    a literal column name in a supabase-py query. We don't actually
    hit the network — just assert the constants behave like strings
    and concatenate cleanly into a select() call.
    """
    from execution.schema import Customers as C
    select_clause = f"{C.ID}, {C.CUSTOMER_NAME}, {C.CUSTOMER_PHONE}"
    assert select_clause == "id, customer_name, customer_phone"
    # And the table name itself works
    assert C.TABLE == "customers"
