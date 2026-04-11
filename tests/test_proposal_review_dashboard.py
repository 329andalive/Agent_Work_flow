"""
test_proposal_review_dashboard.py — Regression tests for the two
proposal review fixes from April 2026.

Issue 1: Customers were tapping "View full estimate online" in the
emailed proposal and seeing raw HTML source instead of a rendered
web page. The customer email pointed at the Supabase Storage public
URL whose content-type header had been silently dropped on upload.

Fix: /doc/send (the owner's Approve & Send route in document_routes)
now mints a public token via token_generator.generate_token(
link_type='proposal') and routes the customer to the Flask
/p/<token> route. That route renders proposal.html server-side via
render_template(), which always sets Content-Type: text/html and
gives us the same code path invoices already use at /i/<token>.
The Storage URL is now only a degraded fallback.

Issue 2: Owners had no way to review or edit a draft estimate from
the desktop dashboard — they had to either tap an email link or
open the PWA. The dashboard had a `proposal_view.html` template but
the line items were read-only and the "Send to Customer" button was
a stub flash message that did nothing.

Fix:
  - proposal_view.html: line items + notes are now inline-editable
    under an Edit toggle. Save button posts to a new endpoint.
  - new POST /api/proposals/<id>/save endpoint: validates ownership
    via session client_id, normalizes line items, writes through
    update_proposal_fields() (the same bottleneck the token-based
    /doc/save uses), and logs edits for the learning loop.
  - The "Send to Customer" button now points at the working
    /api/proposals/<id>/send-email endpoint instead of the broken
    /dashboard/proposal/<id>/action?action=send stub.
"""

import os
import sys
import inspect
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Issue 1 — /doc/send mints /p/<token> for the customer URL on proposals
# ---------------------------------------------------------------------------

def test_doc_send_route_mints_proposal_token_for_customer_url():
    """
    The /doc/send route in document_routes.py must call
    token_generator.generate_token with link_type='proposal' for the
    proposal branch, so customers always land on the server-rendered
    /p/<token> Flask route — not the Storage URL.
    """
    src = (Path(__file__).parent.parent / "routes" / "document_routes.py").read_text()
    # The token mint call must be present
    assert 'link_type="proposal"' in src, (
        "/doc/send must mint a link_type='proposal' token so the customer "
        "view URL is /p/<token>, not a Storage URL."
    )
    # And the resulting URL must be built as /p/<token>
    assert 'f"{base_url}/p/{proposal_token}"' in src or "/p/{proposal_token}" in src, (
        "The minted proposal token must be wrapped into a /p/<token> URL "
        "before being passed to notify_document."
    )


def test_doc_send_proposal_view_url_prefers_token_over_storage():
    """
    The view_url resolution for proposals must prefer the freshly
    minted /p/<token> URL over the Supabase Storage URL.
    """
    src = (Path(__file__).parent.parent / "routes" / "document_routes.py").read_text()
    # The resolution chain must be: proposal_view_url first, html_url fallback
    assert "proposal_view_url" in src, (
        "view_url must reference proposal_view_url so the token URL wins "
        "over the Storage URL."
    )
    # And the order must be token-first, then html_url, then doc/edit
    proposal_idx = src.find("proposal_view_url\n                or html_url")
    assert proposal_idx > 0, (
        "view_url chain must put proposal_view_url BEFORE html_url so "
        "the Flask route is preferred when the token mint succeeds."
    )


def test_doc_send_invoice_path_unchanged():
    """
    Sanity: invoices still go through the /i/<token> path. The
    proposal fix must not have broken the invoice URL resolution.
    """
    src = (Path(__file__).parent.parent / "routes" / "document_routes.py").read_text()
    assert 'link_type="invoice"' in src
    assert "/i/{token}" in src or "/i/{invoice_token}" in src or 'f"{base_url}/i/' in src


# ---------------------------------------------------------------------------
# Issue 2 — Dashboard proposal review: edit + save endpoint
# ---------------------------------------------------------------------------

def test_dashboard_save_proposal_endpoint_exists():
    """The new /api/proposals/<id>/save endpoint must be registered."""
    import execution.db_connection  # noqa: F401
    from routes.dashboard_routes import api_save_proposal
    sig = inspect.signature(api_save_proposal)
    assert "proposal_id" in sig.parameters


def test_dashboard_save_endpoint_requires_auth():
    """An unauthenticated POST must 401, not silently write."""
    from unittest.mock import patch
    from flask import Flask
    from routes.dashboard_routes import dashboard_bp
    import json as _json

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(dashboard_bp)

    # Force the resolver to return None (no session)
    with patch("routes.dashboard_routes._resolve_client_id", return_value=None):
        client = app.test_client()
        resp = client.post(
            "/api/proposals/some-uuid/save",
            data=_json.dumps({"line_items": [], "notes": ""}),
            content_type="application/json",
        )

    assert resp.status_code == 401
    body = _json.loads(resp.data)
    assert body["success"] is False


def test_dashboard_save_endpoint_rejects_proposal_from_other_tenant():
    """Multi-tenant guard — a proposal that doesn't match client_id must 404."""
    from unittest.mock import patch, MagicMock
    from flask import Flask
    from routes.dashboard_routes import dashboard_bp
    import json as _json

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(dashboard_bp)

    # Mock the supabase chain so the ownership lookup returns no rows
    mock_sb = MagicMock()
    mock_table = MagicMock()
    for m in ("select", "eq", "execute"):
        getattr(mock_table, m).return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[])
    mock_sb.table.return_value = mock_table

    with patch("routes.dashboard_routes._resolve_client_id", return_value="client-A"), \
         patch("routes.dashboard_routes._get_supabase", return_value=mock_sb):
        client = app.test_client()
        resp = client.post(
            "/api/proposals/proposal-from-tenant-B/save",
            data=_json.dumps({
                "line_items": [{"description": "Pump out", "amount": 300}],
                "notes": "",
            }),
            content_type="application/json",
        )

    assert resp.status_code == 404
    body = _json.loads(resp.data)
    assert body["success"] is False
    assert "not found" in body["error"].lower()


def test_dashboard_save_endpoint_writes_through_update_proposal_fields():
    """
    On a successful save the endpoint must call update_proposal_fields
    with the normalized line items and computed total. This locks in
    that the dashboard save uses the same bottleneck as /doc/save.
    """
    from unittest.mock import patch, MagicMock
    from flask import Flask
    from routes.dashboard_routes import dashboard_bp
    import json as _json

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(dashboard_bp)

    mock_sb = MagicMock()
    mock_table = MagicMock()
    for m in ("select", "eq", "execute"):
        getattr(mock_table, m).return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[{
        "id": "prop-1",
        "client_id": "client-A",
        "line_items": [],
        "proposal_text": "old notes",
        "amount_estimate": 200,
    }])
    mock_sb.table.return_value = mock_table

    with patch("routes.dashboard_routes._resolve_client_id", return_value="client-A"), \
         patch("routes.dashboard_routes._get_supabase", return_value=mock_sb), \
         patch("execution.db_document.update_proposal_fields", return_value=True) as mock_update, \
         patch("execution.db_document.log_edit"):
        client = app.test_client()
        resp = client.post(
            "/api/proposals/prop-1/save",
            data=_json.dumps({
                "line_items": [
                    {"description": "Baffle replacement", "amount": 175},
                    {"description": "Pump out", "amount": 275},
                ],
                "notes": "Tank in back yard",
            }),
            content_type="application/json",
        )

    assert resp.status_code == 200
    body = _json.loads(resp.data)
    assert body["success"] is True
    assert body["total"] == 450.0
    assert body["line_item_count"] == 2

    # update_proposal_fields must have been called with the right shape
    mock_update.assert_called_once()
    kwargs = mock_update.call_args.kwargs
    assert kwargs["proposal_id"] == "prop-1"
    assert kwargs["total"] == 450.0
    assert kwargs["notes"] == "Tank in back yard"
    assert len(kwargs["line_items"]) == 2
    # Each item must have both `amount` and `total` keys (downstream
    # readers in dashboard_routes.py check both)
    for li in kwargs["line_items"]:
        assert "amount" in li
        assert "total" in li
        assert li["amount"] == li["total"]


def test_dashboard_save_endpoint_drops_empty_rows():
    """Rows with no description AND zero amount must be dropped."""
    from unittest.mock import patch, MagicMock
    from flask import Flask
    from routes.dashboard_routes import dashboard_bp
    import json as _json

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(dashboard_bp)

    mock_sb = MagicMock()
    mock_table = MagicMock()
    for m in ("select", "eq", "execute"):
        getattr(mock_table, m).return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[{
        "id": "prop-1", "client_id": "client-A",
        "line_items": [], "proposal_text": "", "amount_estimate": 0,
    }])
    mock_sb.table.return_value = mock_table

    with patch("routes.dashboard_routes._resolve_client_id", return_value="client-A"), \
         patch("routes.dashboard_routes._get_supabase", return_value=mock_sb), \
         patch("execution.db_document.update_proposal_fields", return_value=True) as mock_update, \
         patch("execution.db_document.log_edit"):
        client = app.test_client()
        resp = client.post(
            "/api/proposals/prop-1/save",
            data=_json.dumps({
                "line_items": [
                    {"description": "Real item", "amount": 100},
                    {"description": "", "amount": 0},  # ← drop me
                    {"description": "", "amount": ""},  # ← drop me too
                ],
                "notes": "",
            }),
            content_type="application/json",
        )

    assert resp.status_code == 200
    kwargs = mock_update.call_args.kwargs
    assert len(kwargs["line_items"]) == 1


# ---------------------------------------------------------------------------
# Issue 2 — proposal_view.html template wires the new edit + save UI
# ---------------------------------------------------------------------------

def test_proposal_view_template_has_edit_toggle():
    """The dashboard template must ship the Edit toggle button + JS."""
    src = (Path(__file__).parent.parent / "templates" / "dashboard" / "proposal_view.html").read_text()
    assert 'id="edit-toggle"' in src
    assert "function toggleEdit" in src
    # And the toggle must show/hide both the line items AND the notes
    assert 'li-view' in src
    assert 'li-edit' in src
    assert 'notes-edit' in src


def test_proposal_view_template_has_save_endpoint_call():
    """The Save button must POST to /api/proposals/<id>/save."""
    src = (Path(__file__).parent.parent / "templates" / "dashboard" / "proposal_view.html").read_text()
    assert "/api/proposals/' + PROPOSAL_ID + '/save" in src
    assert "function saveEdits" in src
    # And there's an Add Row button + remove handlers
    assert "function addRow" in src
    assert "function removeRow" in src


def test_proposal_view_template_send_button_uses_email_endpoint():
    """
    The Send to Customer button must call the working
    /api/proposals/<id>/send-email endpoint, NOT the dead
    /dashboard/proposal/<id>/action?action=send stub.
    """
    src = (Path(__file__).parent.parent / "templates" / "dashboard" / "proposal_view.html").read_text()
    # Must call the email endpoint
    assert "/api/proposals/' + PROPOSAL_ID + '/send-email" in src
    # Must NOT have the broken stub send form anymore
    assert 'value="send"' not in src, (
        "The dead 'action=send' stub form must be removed from the "
        "template — it was a no-op flash from the pre-pivot SMS era."
    )


def test_proposal_view_template_print_styles_hide_edit_widgets():
    """When printing, the edit widgets and remove buttons must be hidden."""
    src = (Path(__file__).parent.parent / "templates" / "dashboard" / "proposal_view.html").read_text()
    # The @media print rule must hide edit-only controls
    assert "@media print" in src
    assert ".doc-edit-toggle" in src
    assert ".doc-add-row" in src
    assert ".li-remove" in src


# ---------------------------------------------------------------------------
# proposal_action send branch — the dead stub is still safe-ish
# ---------------------------------------------------------------------------

def test_proposal_action_send_branch_no_longer_claims_sms_queued():
    """
    The legacy /dashboard/proposal/<id>/action?action=send branch was a
    pre-pivot stub that flashed "SMS sending queued. Will send when
    10DLC is active." Now that SMS to customers is permanently dead per
    HARD RULE #2 and the dashboard send goes through the email endpoint,
    that misleading message must be gone from the proposal_action body.

    (The same stub still exists in invoice_action for now — that's a
    separate bug, tracked separately. This test scopes to proposal_action
    only so it doesn't accidentally fail on unrelated invoice work.)
    """
    from routes.dashboard_routes import proposal_action
    src = inspect.getsource(proposal_action)
    assert "SMS sending queued" not in src, (
        "The pre-pivot 'SMS sending queued' flash in proposal_action is "
        "misleading — Telnyx outbound is dead at the carrier and the "
        "dashboard send now uses /api/proposals/<id>/send-email."
    )
