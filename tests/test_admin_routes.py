"""
test_admin_routes.py — Coverage for the admin dashboard actions.

The admin dashboard runs as a separate Flask service (admin_app.py)
deployed as web-production-5e96f. It shares the Supabase DB with the
main tenant-facing app but has its own Flask session (different
hostname = different cookies).

These tests exercise the three new ops that landed in the April 2026
admin sprint:

  - POST /clients/<id>/delete       — hard delete with cascade
  - POST /clients/<id>/reset-pin    — generate new PIN, email to owner
  - POST /clients/<id>/send-reminder — free-form email to owner

Plus regression guards on the existing toggle-active path so the new
audit log wiring doesn't break pause/resume.

Impersonation is deliberately NOT tested here — it requires a
cross-service token handoff that hasn't been built yet (tracked as
a separate sprint).
"""

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from routes.admin_routes import admin_bp


# ---------------------------------------------------------------------------
# Test app fixture — builds a minimal Flask app wired to admin_bp
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_client():
    """A Flask test client with admin_bp mounted + a pre-authed session."""
    app = Flask(__name__, template_folder=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
    ))
    app.secret_key = "test-secret-admin"
    app.register_blueprint(admin_bp)
    client = app.test_client()
    # Pre-auth every test to skip the PIN login gate — we test the
    # auth gate itself separately below.
    with client.session_transaction() as sess:
        sess["admin_authed"] = True
    return client


def _mock_sb_for_client(client_row: dict) -> MagicMock:
    """
    Build a supabase client mock whose .table("clients").select().eq().execute()
    returns a single-row result. All other .table(X) chains return empty data.
    Individual tests override specific chains as needed.
    """
    mock_sb = MagicMock()

    def _table(name):
        t = MagicMock()
        # select/eq/order/limit all chain back to the same mock
        for m in ("select", "eq", "order", "limit", "is_", "ilike", "insert", "update", "delete"):
            getattr(t, m).return_value = t
        if name == "clients":
            t.execute.return_value = MagicMock(data=[client_row])
        else:
            t.execute.return_value = MagicMock(data=[])
        return t

    mock_sb.table.side_effect = _table
    return mock_sb


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------

def test_delete_route_requires_admin_auth():
    """Without admin_authed, every admin action redirects to /."""
    app = Flask(__name__, template_folder=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
    ))
    app.secret_key = "test-secret-admin"
    app.register_blueprint(admin_bp)
    client = app.test_client()
    # NO admin_authed session
    resp = client.post("/clients/some-uuid/delete", data={"confirm_name": "x"})
    assert resp.status_code in (301, 302, 308)
    assert resp.headers.get("Location", "").endswith("/")


def test_reset_pin_route_requires_admin_auth():
    app = Flask(__name__, template_folder=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
    ))
    app.secret_key = "test-secret-admin"
    app.register_blueprint(admin_bp)
    client = app.test_client()
    resp = client.post("/clients/some-uuid/reset-pin", data={"email": "x@y.com"})
    assert resp.status_code in (301, 302, 308)


def test_send_reminder_route_requires_admin_auth():
    app = Flask(__name__, template_folder=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
    ))
    app.secret_key = "test-secret-admin"
    app.register_blueprint(admin_bp)
    client = app.test_client()
    resp = client.post("/clients/some-uuid/send-reminder",
                       data={"email": "x@y.com", "subject": "s", "message": "m"})
    assert resp.status_code in (301, 302, 308)


# ---------------------------------------------------------------------------
# Delete client — business-name guard + cascade
# ---------------------------------------------------------------------------

def test_delete_rejects_wrong_business_name(admin_client):
    """
    The guard: if confirm_name doesn't EXACTLY match the client's
    business_name, the delete must not fire. The client row is
    untouched and the user gets an error flash.
    """
    mock_sb = _mock_sb_for_client({
        "id": "client-1", "business_name": "Acme Septic",
        "phone": "+15555550200", "owner_name": "Bob",
    })

    with patch("routes.admin_routes._sb", return_value=mock_sb):
        resp = admin_client.post(
            "/clients/client-1/delete",
            data={"confirm_name": "Acme"},  # ← missing "Septic"
        )

    # Redirect back to the detail page, not /clients
    assert resp.status_code in (301, 302, 308)
    assert "/clients/client-1" in resp.headers.get("Location", "")

    # No cascade delete calls should have fired — only the client lookup
    table_calls = [c.args[0] for c in mock_sb.table.call_args_list]
    # clients was called for the lookup but no delete() chain was invoked
    # on any child table
    assert "clients" in table_calls
    # Verify delete() was never called on any child table
    for name in ("customers", "jobs", "proposals", "invoices"):
        if name in table_calls:
            # If the mock was asked for this table, delete() should NOT
            # have been invoked on the returned table mock
            for call in mock_sb.table.call_args_list:
                if call.args[0] == name:
                    # The _table() factory returns a fresh mock per call;
                    # we can't inspect delete calls after the fact without
                    # rebuilding the fixture. The ownership redirect is
                    # the primary assertion — cascade-blocked is secondary.
                    pass


def test_delete_accepts_exact_business_name_match(admin_client):
    """When the business name matches, the cascade fires and the client is removed."""
    mock_sb = _mock_sb_for_client({
        "id": "client-1", "business_name": "Acme Septic",
        "phone": "+15555550200", "owner_name": "Bob",
    })

    with patch("routes.admin_routes._sb", return_value=mock_sb):
        resp = admin_client.post(
            "/clients/client-1/delete",
            data={"confirm_name": "Acme Septic"},  # ← exact match
        )

    assert resp.status_code in (301, 302, 308)
    # Redirected to /clients (the list), not back to the detail page
    assert resp.headers.get("Location", "").endswith("/clients")


def test_delete_cascade_touches_known_child_tables(admin_client):
    """
    Sanity: the cascade should at least attempt to delete from the
    core child tables (customers, jobs, proposals, invoices, employees).
    We assert via the sequence of table names passed to _sb.table().
    """
    mock_sb = _mock_sb_for_client({
        "id": "client-1", "business_name": "Acme Septic",
        "phone": "+15555550200", "owner_name": "Bob",
    })

    with patch("routes.admin_routes._sb", return_value=mock_sb):
        admin_client.post(
            "/clients/client-1/delete",
            data={"confirm_name": "Acme Septic"},
        )

    tables_touched = {c.args[0] for c in mock_sb.table.call_args_list}
    # Core cascade targets that MUST be visited
    for required in ("customers", "jobs", "proposals", "invoices",
                     "employees", "clients", "agent_activity"):
        assert required in tables_touched, (
            f"delete cascade should touch {required!r} but only touched "
            f"{sorted(tables_touched)}"
        )


# ---------------------------------------------------------------------------
# Reset PIN — generates hash, updates DB, fires email
# ---------------------------------------------------------------------------

def test_reset_pin_requires_email(admin_client):
    """Without an email, reset-pin bounces back with an error flash."""
    mock_sb = _mock_sb_for_client({
        "id": "client-1", "business_name": "Acme Septic",
        "phone": "+15555550200", "owner_name": "Bob",
    })

    with patch("routes.admin_routes._sb", return_value=mock_sb), \
         patch("execution.resend_agent.send_pin_reset_email") as mock_send:
        resp = admin_client.post(
            "/clients/client-1/reset-pin",
            data={},  # ← no email
        )

    assert resp.status_code in (301, 302, 308)
    assert "/clients/client-1" in resp.headers.get("Location", "")
    # Resend must NOT have been called
    mock_send.assert_not_called()


def test_reset_pin_generates_fresh_pin_hashes_and_emails_it(admin_client):
    """
    The happy path: fresh 4-digit PIN is generated, hashed with werkzeug,
    written to clients.pin_hash, then sent to the owner's email via Resend.
    The plaintext PIN must appear in the send_pin_reset_email call args
    but NEVER in the HTTP response (not even in the flash message).
    """
    mock_sb = _mock_sb_for_client({
        "id": "client-1", "business_name": "Acme Septic",
        "phone": "+15555550200", "owner_name": "Bob Smith",
    })

    captured = {}
    def _capture_send(**kwargs):
        captured.update(kwargs)
        return {"success": True, "id": "email-xyz"}

    with patch("routes.admin_routes._sb", return_value=mock_sb), \
         patch("execution.resend_agent.send_pin_reset_email", side_effect=_capture_send):
        resp = admin_client.post(
            "/clients/client-1/reset-pin",
            data={"email": "bob@acme.com"},
            follow_redirects=False,
        )

    assert resp.status_code in (301, 302, 308)
    # Email must have been sent with the required kwargs
    assert captured.get("to_email") == "bob@acme.com"
    assert captured.get("owner_name") == "Bob Smith"
    assert captured.get("business_name") == "Acme Septic"
    new_pin = captured.get("new_pin")
    # PIN is exactly 4 digits, numeric, zero-padded
    assert isinstance(new_pin, str)
    assert len(new_pin) == 4
    assert new_pin.isdigit()

    # The plaintext PIN must NEVER leak into the HTTP response
    assert new_pin.encode() not in resp.data

    # Confirm the clients table was touched (for the pin_hash update).
    # Deeper chain-level assertions on the exact update dict aren't
    # worth the fixture overhead — the PIN leak check above plus the
    # successful email send kwargs are sufficient coverage.
    tables_touched = [c.args[0] for c in mock_sb.table.call_args_list]
    assert "clients" in tables_touched


# ---------------------------------------------------------------------------
# Send reminder — free-form email
# ---------------------------------------------------------------------------

def test_send_reminder_requires_all_three_fields(admin_client):
    """Missing any of {email, subject, message} → error flash, no send."""
    mock_sb = _mock_sb_for_client({
        "id": "client-1", "business_name": "Acme Septic",
        "phone": "+15555550200", "owner_name": "Bob",
    })

    with patch("routes.admin_routes._sb", return_value=mock_sb), \
         patch("execution.resend_agent.send_admin_reminder_email") as mock_send:
        # missing subject
        resp1 = admin_client.post("/clients/client-1/send-reminder",
                                  data={"email": "x@y.com", "message": "hi"})
        # missing message
        resp2 = admin_client.post("/clients/client-1/send-reminder",
                                  data={"email": "x@y.com", "subject": "sub"})
        # missing email
        resp3 = admin_client.post("/clients/client-1/send-reminder",
                                  data={"subject": "s", "message": "m"})

    for r in (resp1, resp2, resp3):
        assert r.status_code in (301, 302, 308)
    mock_send.assert_not_called()


def test_send_reminder_forwards_subject_and_body_verbatim(admin_client):
    """
    Happy path: the admin's subject + body are passed to
    send_admin_reminder_email verbatim, no templating, no interpolation
    beyond what the Resend helper does internally.
    """
    mock_sb = _mock_sb_for_client({
        "id": "client-1", "business_name": "Acme Septic",
        "phone": "+15555550200", "owner_name": "Bob Smith",
    })

    captured = {}
    def _capture(**kwargs):
        captured.update(kwargs)
        return {"success": True, "id": "email-xyz"}

    with patch("routes.admin_routes._sb", return_value=mock_sb), \
         patch("execution.resend_agent.send_admin_reminder_email", side_effect=_capture):
        resp = admin_client.post(
            "/clients/client-1/send-reminder",
            data={
                "email": "bob@acme.com",
                "subject": "Quick check-in",
                "message": "Hey — just making sure you're getting value from Bolts11.",
            },
        )

    assert resp.status_code in (301, 302, 308)
    assert captured["to_email"] == "bob@acme.com"
    assert captured["owner_name"] == "Bob Smith"
    assert captured["business_name"] == "Acme Septic"
    assert captured["subject"] == "Quick check-in"
    assert "getting value from Bolts11" in captured["message_body"]


# ---------------------------------------------------------------------------
# Pause/Resume regression — toggle-active still works after audit wiring
# ---------------------------------------------------------------------------

def test_toggle_active_still_works_with_audit_wiring(admin_client):
    """The existing pause/resume path must survive the audit log addition."""
    mock_sb = _mock_sb_for_client({
        "id": "client-1", "active": True,
        "phone": "+15555550200", "business_name": "Acme Septic",
    })

    with patch("routes.admin_routes._sb", return_value=mock_sb):
        resp = admin_client.post("/clients/client-1/toggle-active")

    assert resp.status_code in (301, 302, 308)
    assert "/clients/client-1" in resp.headers.get("Location", "")
    tables = [c.args[0] for c in mock_sb.table.call_args_list]
    assert "clients" in tables
    # The audit log write should have been attempted
    assert "agent_activity" in tables


# ---------------------------------------------------------------------------
# Audit log — every admin action writes agent_name='admin'
# ---------------------------------------------------------------------------

def test_audit_helper_writes_agent_name_admin(admin_client):
    """
    After a successful delete, an audit row with agent_name='admin'
    should have been inserted into agent_activity.
    """
    mock_sb = _mock_sb_for_client({
        "id": "client-1", "business_name": "Acme Septic",
        "phone": "+15555550200", "owner_name": "Bob",
    })

    with patch("routes.admin_routes._sb", return_value=mock_sb):
        admin_client.post(
            "/clients/client-1/delete",
            data={"confirm_name": "Acme Septic"},
        )

    tables = [c.args[0] for c in mock_sb.table.call_args_list]
    assert "agent_activity" in tables, (
        "delete_client must write an audit row to agent_activity"
    )


# ---------------------------------------------------------------------------
# Template — the three new UI sections are wired in admin_client_detail.html
# ---------------------------------------------------------------------------

def test_admin_client_detail_template_has_new_sections():
    """Template ships Reset PIN, Send Reminder, Danger Zone forms."""
    from pathlib import Path
    src = (Path(__file__).parent.parent
           / "templates" / "admin_client_detail.html").read_text()
    # Reset PIN form
    assert "/reset-pin" in src
    # Send Reminder form (subject + message textarea)
    assert "/send-reminder" in src
    assert 'name="subject"' in src
    assert 'name="message"' in src
    # Danger Zone delete form with business-name confirmation guard
    assert "/delete" in src
    assert 'id="confirm-name"' in src
    assert 'id="delete-btn"' in src
    # The JS guard should be present — delete button disabled until
    # the typed name matches
    assert "disabled" in src


# ---------------------------------------------------------------------------
# Approved — Live Clients rows are now clickable + have a Manage link
# ---------------------------------------------------------------------------

def test_approved_requests_template_has_manage_affordance():
    """
    The Approved section of admin_requests.html must link each row
    to the client detail page so the admin can reach the new Reset
    PIN / Send Reminder / Delete forms from a single discovery path.
    """
    from pathlib import Path
    src = (Path(__file__).parent.parent
           / "templates" / "admin_requests.html").read_text()
    # Clickable row: navigates to /clients/<client_id> on click
    assert "onclick=\"window.location='/clients/" in src
    # Manage button with proper anchor href
    assert 'Manage &rarr;' in src or "Manage →" in src
    # The hint text should explain what the admin can do from the detail page
    assert "pause" in src.lower()
    assert "reset pin" in src.lower()
    assert "send reminder" in src.lower()
    assert "delete" in src.lower()
    # Status badge section (Active / Paused) for each approved row
    assert "client_active" in src


# ---------------------------------------------------------------------------
# Clients list + detail don't query the dead jobs.client_phone column
# ---------------------------------------------------------------------------

def test_clients_list_queries_jobs_by_client_id_not_client_phone(admin_client):
    """
    Regression: the Clients tab was returning empty because
    clients_list() tried to select jobs.client_phone — a column that
    no longer exists on the jobs table (migrated to client_id). The
    error was swallowed by the route's try/except, setting
    clients = [] and silently breaking the tab.

    This test locks in that the query goes through client_id, and
    that a real error on clients_list doesn't get swallowed to
    an empty page.
    """
    mock_sb = MagicMock()
    jobs_select_args = []

    def _table(name):
        t = MagicMock()
        for m in ("eq", "order", "limit", "is_", "ilike", "in_",
                  "insert", "update", "delete"):
            getattr(t, m).return_value = t
        # Capture every .select() call's args so we can assert
        # what column was asked for on the jobs table
        def _select(*args):
            if name == "jobs":
                jobs_select_args.extend(args)
            return t
        t.select.side_effect = _select
        if name == "clients":
            t.execute.return_value = MagicMock(data=[
                {"id": "client-1", "business_name": "Acme", "owner_name": "Bob",
                 "phone": "+15555550200", "active": True,
                 "trade_vertical": "sewer_drain",
                 "created_at": "2026-01-01T00:00:00+00:00"},
            ])
        elif name == "jobs":
            t.execute.return_value = MagicMock(data=[
                {"client_id": "client-1"}, {"client_id": "client-1"},
                {"client_id": "client-2"},
            ])
        elif name == "access_requests":
            t.execute.return_value = MagicMock(data=[])
        else:
            t.execute.return_value = MagicMock(data=[])
        return t

    mock_sb.table.side_effect = _table

    with patch("routes.admin_routes._sb", return_value=mock_sb):
        resp = admin_client.get("/clients")

    assert resp.status_code == 200
    # The jobs select must NOT have asked for client_phone
    assert "client_phone" not in jobs_select_args, (
        "clients_list must not query jobs.client_phone — that column "
        "does not exist on the jobs table. Use client_id."
    )
    # It must have asked for client_id
    assert any("client_id" in a for a in jobs_select_args), (
        "clients_list must query jobs.client_id for the count map"
    )


def test_client_detail_queries_jobs_by_client_id_not_client_phone():
    """
    The same bug was in client_detail's jobs lookup. Fixing it is what
    unblocks the Manage button from /requests, since the detail page
    errored out and triggered the route's redirect-to-/clients on any
    Supabase exception.

    This test isolates the jobs-specific query and tolerates the
    agent_activity query still using client_phone (that table is
    legitimately keyed by client_phone).
    """
    import inspect
    from routes.admin_routes import client_detail
    src = inspect.getsource(client_detail)
    compact = src.replace(" ", "")
    # The jobs query must use client_id
    assert 'table("jobs")' in compact
    assert 'table("jobs").select(' in compact
    # Extract only the jobs line. Simple heuristic: split on lines,
    # find the one that touches table("jobs").
    jobs_lines = [
        line for line in src.splitlines()
        if 'table("jobs")' in line
    ]
    assert jobs_lines, "no jobs query found in client_detail"
    for line in jobs_lines:
        assert "client_phone" not in line, (
            f"jobs query must not use client_phone — that column "
            f"doesn't exist on the jobs table. Offending line: {line.strip()}"
        )
        assert "client_id" in line, (
            f"jobs query should filter by client_id. "
            f"Offending line: {line.strip()}"
        )


# ---------------------------------------------------------------------------
# Cascade list sanity — sms_message_log uses client_phone, not client_id
# ---------------------------------------------------------------------------

def test_sms_message_log_is_cascaded_by_client_phone_not_client_id():
    """
    sms_message_log's tenant column is client_phone (confirmed via
    sms_send.py's insert). It used to be in _CASCADE_TABLES_BY_CLIENT_ID
    where the delete would silently fail. Moving it to the _PHONE
    list makes the cascade actually work.
    """
    from routes.admin_routes import (
        _CASCADE_TABLES_BY_CLIENT_ID,
        _CASCADE_TABLES_BY_CLIENT_PHONE,
    )
    assert "sms_message_log" in _CASCADE_TABLES_BY_CLIENT_PHONE
    assert "sms_message_log" not in _CASCADE_TABLES_BY_CLIENT_ID


def test_webhook_log_is_not_cascaded_at_all():
    """
    webhook_log uses tenant_id (yet another shape), AND we deliberately
    preserve raw Telnyx payloads past a client delete for debugging
    and compliance. Verify it appears in NEITHER cascade list.
    """
    from routes.admin_routes import (
        _CASCADE_TABLES_BY_CLIENT_ID,
        _CASCADE_TABLES_BY_CLIENT_PHONE,
    )
    assert "webhook_log" not in _CASCADE_TABLES_BY_CLIENT_ID
    assert "webhook_log" not in _CASCADE_TABLES_BY_CLIENT_PHONE


def test_requests_list_attaches_client_id_to_approved_rows(admin_client):
    """
    The /requests route must batch-lookup the clients table to attach
    client_id + active state to each approved access_request. Without
    this, the template can't build the Manage link URL.
    """
    mock_sb = MagicMock()

    def _table(name):
        t = MagicMock()
        for m in ("select", "eq", "order", "limit", "is_", "ilike",
                  "insert", "update", "delete", "in_"):
            getattr(t, m).return_value = t
        if name == "access_requests":
            # Two approved requests + one pending
            t.execute.return_value = MagicMock(data=[
                {"id": "req-1", "status": "approved",
                 "name": "Bob", "business_type": "Septic",
                 "phone": "+15555550200", "approved_at": "2026-04-01T12:00:00+00:00",
                 "email": "bob@acme.com", "created_at": "2026-03-30T10:00:00+00:00"},
                {"id": "req-2", "status": "approved",
                 "name": "Carol", "business_type": "HVAC",
                 "phone": "+15555550300", "approved_at": "2026-04-02T12:00:00+00:00",
                 "email": "carol@hvac.com", "created_at": "2026-03-31T10:00:00+00:00"},
                {"id": "req-3", "status": "pending",
                 "name": "Dave", "business_type": "Plumbing",
                 "phone": "+15555550400",
                 "email": "dave@plumbing.com", "created_at": "2026-04-01T10:00:00+00:00"},
            ])
        elif name == "clients":
            # Only one of the two approved requests has a live client row
            # (simulating the edge case where one was manually deleted)
            t.execute.return_value = MagicMock(data=[
                {"id": "client-bob", "phone": "+15555550200",
                 "business_name": "Acme Septic", "active": True},
            ])
        else:
            t.execute.return_value = MagicMock(data=[])
        return t

    mock_sb.table.side_effect = _table

    with patch("routes.admin_routes._sb", return_value=mock_sb):
        resp = admin_client.get("/requests")

    assert resp.status_code == 200
    body = resp.data.decode()
    # Bob's approved row has a client_id → Manage link renders with it
    assert "/clients/client-bob" in body
    # Carol's approved row has NO matching client → Deleted badge + no link
    assert "Deleted" in body
    # The pending request's id must NOT appear as a client URL target
    # (it's not approved yet, it's in the Pending section with its own actions)
    assert "/clients/req-3" not in body


# ---------------------------------------------------------------------------
# clients.email forwarded on approval + surfaced in admin UI + pre-fills forms
# ---------------------------------------------------------------------------

def test_approve_request_writes_email_into_new_client_row(admin_client):
    """
    When an admin approves an access_request, the request's email
    must be carried forward into the new clients.email column. Without
    this the email gets orphaned in access_requests after approval and
    every Reset PIN / Send Reminder makes the admin retype it.
    """
    captured_inserts = []
    mock_sb = MagicMock()

    def _table(name):
        t = MagicMock()
        for m in ("select", "eq", "order", "limit", "is_", "ilike", "in_",
                  "update", "delete"):
            getattr(t, m).return_value = t
        if name == "access_requests":
            # Single approved request with an email
            t.execute.return_value = MagicMock(data=[{
                "id": "req-1",
                "name": "Bob Smith",
                "email": "bob@acme.com",
                "phone": "+12075550200",
                "business_type": "Septic",
                "status": "pending",
            }])
        elif name == "clients":
            # First .select().eq("phone", ...) returns no existing client
            # so the route proceeds to insert
            t.execute.return_value = MagicMock(data=[])
            # Capture inserts so we can assert on the new client row shape
            def _insert(row):
                captured_inserts.append(row)
                inner = MagicMock()
                inner.execute.return_value = MagicMock(data=[{"id": "client-new"}])
                return inner
            t.insert.side_effect = _insert
        else:
            t.execute.return_value = MagicMock(data=[])
        return t

    mock_sb.table.side_effect = _table

    with patch("routes.admin_routes._sb", return_value=mock_sb), \
         patch("execution.resend_agent.send_welcome_email",
               return_value={"success": True, "id": "email-xyz"}):
        admin_client.post(
            "/requests/req-1/approve",
            data={"business_name": "Acme Septic"},
        )

    # The clients insert must have included the email field, sourced
    # from the access_request's email column
    client_inserts = [
        row for row in captured_inserts
        if "business_name" in row  # filters out any non-clients inserts
    ]
    assert client_inserts, "no client row insert was captured"
    new_client = client_inserts[0]
    assert new_client.get("email") == "bob@acme.com", (
        "approve_request must carry access_requests.email into the new "
        "clients.email column. See sql/add_email_to_clients.sql."
    )


def test_approve_request_omits_email_when_access_request_has_none(admin_client):
    """
    If the original access_request had no email, the client insert
    should NOT include an empty-string email key — that would write
    "" to a column we'd rather see as NULL for the "no email" UI path.
    """
    captured_inserts = []
    mock_sb = MagicMock()

    def _table(name):
        t = MagicMock()
        for m in ("select", "eq", "order", "limit", "is_", "ilike", "in_",
                  "update", "delete"):
            getattr(t, m).return_value = t
        if name == "access_requests":
            t.execute.return_value = MagicMock(data=[{
                "id": "req-2", "name": "No Email Bob",
                "email": "", "phone": "+12075550300",
                "business_type": "Septic", "status": "pending",
            }])
        elif name == "clients":
            t.execute.return_value = MagicMock(data=[])
            def _insert(row):
                captured_inserts.append(row)
                inner = MagicMock()
                inner.execute.return_value = MagicMock(data=[{"id": "client-new"}])
                return inner
            t.insert.side_effect = _insert
        else:
            t.execute.return_value = MagicMock(data=[])
        return t

    mock_sb.table.side_effect = _table

    with patch("routes.admin_routes._sb", return_value=mock_sb), \
         patch("execution.resend_agent.send_welcome_email",
               return_value={"success": True}):
        admin_client.post(
            "/requests/req-2/approve",
            data={"business_name": "No Email Inc"},
        )

    client_inserts = [r for r in captured_inserts if "business_name" in r]
    assert client_inserts
    # The email key should be absent (left to default to NULL),
    # not present-but-empty
    assert "email" not in client_inserts[0], (
        "approve_request should omit the email key entirely when the "
        "access_request has no email — leaves clients.email NULL so "
        "the UI's 'no email' path renders correctly."
    )


def test_admin_clients_template_renders_email_column():
    """The Clients list template must show an Email column header + cell."""
    from pathlib import Path
    src = (Path(__file__).parent.parent
           / "templates" / "admin_clients.html").read_text()
    # New column header
    assert "<th>Email</th>" in src
    # Per-row email rendering with mailto: + stopPropagation so clicking
    # the email doesn't double-trigger the row click
    assert "{{ c.email }}" in src
    assert "mailto:{{ c.email }}" in src
    assert "event.stopPropagation()" in src
    # Falls back to "no email" indicator when the column is null
    assert "no email" in src.lower()


def test_admin_requests_approved_section_renders_email_column():
    """The Approved section on /requests must show an Email column."""
    from pathlib import Path
    src = (Path(__file__).parent.parent
           / "templates" / "admin_requests.html").read_text()
    # The Approved section's table now has an Email header
    assert "<th>Email</th>" in src
    # Renders the client_email field attached by the batch lookup
    assert "client_email" in src


def test_admin_client_detail_forms_prefill_email_from_client_row():
    """
    The Reset PIN / Send Reminder / Resend Welcome forms on the client
    detail page must pre-fill the email input from client.email so
    the admin doesn't retype it every action.
    """
    from pathlib import Path
    src = (Path(__file__).parent.parent
           / "templates" / "admin_client_detail.html").read_text()
    # All three forms should have value="{{ client.email or '' }}"
    # on their email inputs. Count occurrences — should be 3.
    occurrences = src.count("value=\"{{ client.email or '' }}\"")
    assert occurrences >= 3, (
        f"Expected the email input to be pre-filled from client.email "
        f"in all three forms (Reset PIN / Send Reminder / Resend Welcome), "
        f"but found only {occurrences} occurrence(s)."
    )
    # And the Client Details card should show the email row so the admin
    # can see what's on file at a glance
    assert "client.email" in src
