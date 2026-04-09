"""
test_sms_routing.py — Coverage for the inbound-only sms_router.

Telnyx outbound is blocked at the carrier. The router has exactly two
behaviors:

    1. STOP / YES / START / UNSTOP — TCPA opt-in/opt-out (DB only)
    2. Everything else — log and ignore (PWA owns it)

The router must NEVER call send_sms — even via clock_agent. There's a
regression test below that asserts the module does not import send_sms.
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

BASE_SMS = {
    "from_number": "+15555550100",
    "to_number": "+15555550200",
    "message_id": "test-msg-001",
}


def _sms(body: str) -> dict:
    return {**BASE_SMS, "body": body}


def _apply_patches(extra=None):
    """Patch the tenant lookup + any extras the test asks for."""
    started = []
    patches = {"execution.sms_router.get_client_by_phone": lambda phone: MOCK_CLIENT}
    patches.update(extra or {})
    for target, replacement in patches.items():
        p = patch(target, replacement)
        p.start()
        started.append(p)
    return started


def _stop_patches(started):
    for p in started:
        p.stop()


# ---------------------------------------------------------------------------
# Step 1 — TCPA opt-in / opt-out
# ---------------------------------------------------------------------------

def test_stop_routes_to_opt_out():
    """STOP must call optin_agent.handle_stop and tag 'optin_stop'."""
    mock_handle_stop = MagicMock()
    extra = {"execution.optin_agent.handle_stop": mock_handle_stop}
    patchers = _apply_patches(extra)
    try:
        result = route_message(_sms("STOP"))
        assert result == "optin_stop"
        mock_handle_stop.assert_called_once()
        args = mock_handle_stop.call_args.args
        assert args[0]["id"] == MOCK_CLIENT["id"]
        assert args[1] == "+15555550100"
    finally:
        _stop_patches(patchers)


def test_yes_routes_to_opt_in_when_no_consent_yet():
    """YES with no existing consent → optin_agent.handle_yes is invoked."""
    mock_handle_yes = MagicMock()
    extra = {
        "execution.db_consent.check_consent": lambda cid, phone: False,
        "execution.optin_agent.handle_yes": mock_handle_yes,
    }
    patchers = _apply_patches(extra)
    try:
        result = route_message(_sms("YES"))
        assert result == "optin_yes"
        mock_handle_yes.assert_called_once()
    finally:
        _stop_patches(patchers)


def test_yes_skips_handler_when_already_consented():
    """YES from a phone that already has consent should not re-fire handle_yes."""
    mock_handle_yes = MagicMock()
    extra = {
        "execution.db_consent.check_consent": lambda cid, phone: True,
        "execution.optin_agent.handle_yes": mock_handle_yes,
    }
    patchers = _apply_patches(extra)
    try:
        result = route_message(_sms("YES"))
        assert result == "optin_yes"
        mock_handle_yes.assert_not_called()
    finally:
        _stop_patches(patchers)


def test_start_and_unstop_aliases_route_like_yes():
    """START and UNSTOP should behave the same as YES."""
    for word in ("START", "UNSTOP"):
        mock_handle_yes = MagicMock()
        extra = {
            "execution.db_consent.check_consent": lambda cid, phone: False,
            "execution.optin_agent.handle_yes": mock_handle_yes,
        }
        patchers = _apply_patches(extra)
        try:
            result = route_message(_sms(word))
            assert result == "optin_yes", f"{word} should tag optin_yes"
            mock_handle_yes.assert_called_once()
        finally:
            _stop_patches(patchers)


# ---------------------------------------------------------------------------
# Step 2 — Everything else gets logged and ignored
# ---------------------------------------------------------------------------

def test_clock_in_is_now_ignored():
    """
    CLOCK IN used to delegate to clock_agent. With Telnyx outbound dead,
    workers must use the PWA. Inbound CLOCK IN is logged and ignored.
    """
    patchers = _apply_patches()
    try:
        result = route_message(_sms("CLOCK IN"))
        assert result == "ignored"
    finally:
        _stop_patches(patchers)


def test_clock_out_is_now_ignored():
    """Same story as CLOCK IN — the PWA owns clock punches now."""
    patchers = _apply_patches()
    try:
        result = route_message(_sms("CLOCK OUT"))
        assert result == "ignored"
    finally:
        _stop_patches(patchers)


def test_done_is_ignored():
    """DONE used to route to invoice_agent. Now: ignored — PWA owns job state."""
    patchers = _apply_patches()
    try:
        result = route_message(_sms("DONE pumped 1000 gal Beverly $350"))
        assert result == "ignored"
    finally:
        _stop_patches(patchers)


def test_estimate_keyword_is_ignored():
    """EST used to route to proposal_agent. Now: ignored."""
    patchers = _apply_patches()
    try:
        result = route_message(_sms("EST pump and camera Beverly Whitaker"))
        assert result == "ignored"
    finally:
        _stop_patches(patchers)


def test_ambiguous_text_is_ignored():
    """Ambiguous text used to fall to clarification_agent. Now: ignored."""
    patchers = _apply_patches()
    try:
        result = route_message(_sms("hey what's up"))
        assert result == "ignored"
    finally:
        _stop_patches(patchers)


def test_set_optin_command_is_ignored():
    """SET OPTIN was a special command. Now it just gets logged + ignored."""
    patchers = _apply_patches()
    try:
        result = route_message(_sms("SET OPTIN +12075551234"))
        assert result == "ignored"
    finally:
        _stop_patches(patchers)


def test_no_client_for_telnyx_number_short_circuits():
    """When the inbound to_number doesn't match any tenant, return 'no_client'."""
    patchers = []
    for target, repl in {
        "execution.sms_router.get_client_by_phone": lambda phone: None,
    }.items():
        p = patch(target, repl)
        p.start()
        patchers.append(p)
    try:
        result = route_message(_sms("anything goes here"))
        assert result == "no_client"
    finally:
        _stop_patches(patchers)


# ---------------------------------------------------------------------------
# Hard guarantee: sms_router never sends SMS
# ---------------------------------------------------------------------------

def test_sms_router_does_not_import_send_sms():
    """
    Regression guard: this module must never import send_sms or sms_send.
    Telnyx outbound is blocked at the carrier — any outbound code path
    would be a footgun. We walk the AST so docstring mentions don't
    trip the check.
    """
    import ast
    import inspect
    import execution.sms_router as sms_router

    tree = ast.parse(inspect.getsource(sms_router))
    forbidden = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "sms_send" in node.module:
                forbidden.append(f"from {node.module} import ...")
            for alias in node.names:
                if "send_sms" in (alias.name or ""):
                    forbidden.append(f"from {node.module} import {alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name and "sms_send" in alias.name:
                    forbidden.append(f"import {alias.name}")
    assert not forbidden, (
        f"sms_router.py must not import send_sms — found: {forbidden}. "
        "Telnyx outbound is blocked at the carrier. Use notify() if you "
        "need to message anyone, and let the kill switch fall back to email."
    )


def test_clock_in_does_not_call_send_sms_anywhere_downstream():
    """
    Defense in depth: even when route_message handles a CLOCK IN body,
    no downstream import should reach send_sms. The router shouldn't
    even import clock_agent now, but if a future change re-introduces
    that import, this test will catch it.
    """
    patchers = _apply_patches({"execution.sms_send.send_sms": MagicMock()})
    try:
        result = route_message(_sms("CLOCK IN"))
        assert result == "ignored"
        from execution import sms_send
        # The patched MagicMock replaces sms_send.send_sms; if anything
        # downstream called it, the mock would have a positive call count.
        assert sms_send.send_sms.call_count == 0
    finally:
        _stop_patches(patchers)
