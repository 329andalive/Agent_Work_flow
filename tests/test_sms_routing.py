"""
test_sms_routing.py — Keyword and intent routing coverage (T3)

Tests route_message() from sms_router.py with all external calls mocked.
Verifies that inbound SMS bodies route to the correct agent.
"""

import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.sms_router import route_message


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

MOCK_CLIENT = {
    "id": "client-test-uuid",
    "business_name": "Test Trades Co",
    "owner_name": "Jeremy",
    "phone": "+15555550200",
    "owner_mobile": "+15555550100",
    "personality": "Hourly rate: $125/hr",
    "active": True,
}

MOCK_EMPLOYEE_OWNER = {
    "name": "Jeremy",
    "role": "owner",
    "phone": "+15555550100",
}

BASE_SMS = {
    "from_number": "+15555550100",
    "to_number": "+15555550200",
    "message_id": "test-msg-001",
}


def _sms(body: str) -> dict:
    return {**BASE_SMS, "body": body}


# Patches applied to every test — suppress all DB / external calls
COMMON_PATCHES = {
    "execution.sms_router.lookup_client": lambda phone: MOCK_CLIENT,
    "execution.sms_router.get_employee_by_phone": lambda cid, phone: MOCK_EMPLOYEE_OWNER,
    "execution.sms_router.detect_response_type": lambda body: None,
    "execution.sms_router.get_pending_followups_by_type": lambda cid, ftype: [],
    "execution.sms_router.get_pending_clarification": lambda cid, phone: None,
    "execution.sms_router.get_pending_approval_by_customer": lambda cid, phone: None,
    "execution.sms_router.dispatch": MagicMock(),
}


def _apply_patches(extra=None):
    """Return a list of started patchers. Caller must stop them."""
    patches = {**COMMON_PATCHES}
    if extra:
        patches.update(extra)
    started = []
    for target, replacement in patches.items():
        p = patch(target, replacement)
        p.start()
        started.append(p)
    return started


def _stop_patches(started):
    for p in started:
        p.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_stop_routes_to_opt_out():
    """STOP must route to optin_stop handler, never clarification_agent."""
    mock_sb_table = MagicMock()
    mock_sb_table.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data=MOCK_CLIENT)
    mock_handle_stop = MagicMock()

    extra = {
        "execution.sms_router.dispatch": MagicMock(),
        "execution.db_connection.get_client": lambda: mock_sb_table,
        "execution.optin_agent.handle_stop": mock_handle_stop,
    }
    patchers = _apply_patches(extra)
    try:
        result = route_message(_sms("STOP"))
        assert result == "optin_stop", f"Expected 'optin_stop', got '{result}'"
        mock_handle_stop.assert_called_once()
    finally:
        _stop_patches(patchers)


def test_done_routes_to_invoice_agent():
    """DONE + job details must route to invoice_agent."""
    mock_dispatch = MagicMock()
    extra = {"execution.sms_router.dispatch": mock_dispatch}
    patchers = _apply_patches(extra)
    try:
        result = route_message(_sms("DONE pumped 1000 gal tank Beverly Whitaker $350"))
        assert result == "invoice_agent", f"Expected 'invoice_agent', got '{result}'"
        mock_dispatch.assert_called_once()
        assert mock_dispatch.call_args[0][0] == "invoice_agent"
    finally:
        _stop_patches(patchers)


def test_est_prefix_routes_to_proposal_agent():
    """EST prefix must route to proposal_agent."""
    mock_dispatch = MagicMock()
    extra = {"execution.sms_router.dispatch": mock_dispatch}
    patchers = _apply_patches(extra)
    try:
        result = route_message(_sms("EST pump and camera Beverly Whitaker 123 Main St"))
        assert result == "proposal_agent", f"Expected 'proposal_agent', got '{result}'"
        mock_dispatch.assert_called_once()
        assert mock_dispatch.call_args[0][0] == "proposal_agent"
    finally:
        _stop_patches(patchers)


def test_ambiguous_message_routes_to_clarification():
    """Ambiguous text with no keywords must fall through to clarification_agent."""
    mock_dispatch = MagicMock()
    extra = {
        "execution.sms_router.dispatch": mock_dispatch,
        "execution.sms_router._load_full_client": lambda phone: MOCK_CLIENT,
    }
    patchers = _apply_patches(extra)
    try:
        result = route_message(_sms("hey what's up"))
        assert result == "clarification_agent", f"Expected 'clarification_agent', got '{result}'"
        mock_dispatch.assert_called_once()
        assert mock_dispatch.call_args[0][0] == "clarification_agent"
    finally:
        _stop_patches(patchers)


def test_set_optin_routes_to_optin_handler():
    """SET OPTIN +number must route to optin_set handler."""
    mock_optin = MagicMock()
    extra = {
        "execution.sms_router.handle_optin_command": mock_optin,
    }
    patchers = _apply_patches(extra)
    try:
        result = route_message(_sms("SET OPTIN +12075551234"))
        assert result == "optin_set", f"Expected 'optin_set', got '{result}'"
        mock_optin.assert_called_once()
    finally:
        _stop_patches(patchers)
