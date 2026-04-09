"""
test_sms_routing.py — Coverage for the post-PWA-pivot sms_router.

The old 8-step conversational router is gone. The new router has
exactly three paths:

    1. STOP / YES / START / UNSTOP — TCPA opt-in/opt-out (always first)
    2. CLOCK IN / CLOCK OUT — delegate to clock_agent (echoes back)
    3. Everything else — one-line PWA redirect (employees only)

Customer-facing SMS is forbidden by Hard Rule #2 — non-employee
senders that aren't opt commands get logged and ignored, never replied
to.
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

MOCK_EMPLOYEE = {
    "id": "emp-1",
    "name": "Jesse Tech",
    "role": "field_tech",
    "phone": "+15555550100",
}

BASE_SMS = {
    "from_number": "+15555550100",
    "to_number": "+15555550200",
    "message_id": "test-msg-001",
}


def _sms(body: str) -> dict:
    return {**BASE_SMS, "body": body}


def _common_patches(employee=MOCK_EMPLOYEE):
    """
    Patches that every test needs:
      - tenant lookup → MOCK_CLIENT
      - employee lookup → MOCK_EMPLOYEE (or None to simulate unknown sender)
    """
    return {
        "execution.sms_router.get_client_by_phone": lambda phone: MOCK_CLIENT,
        "execution.sms_router.get_employee_by_phone": lambda cid, phone: employee,
    }


def _apply_patches(extra=None):
    started = []
    for target, replacement in {**_common_patches(), **(extra or {})}.items():
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
    """STOP must route through optin_agent.handle_stop and tag 'optin_stop'."""
    mock_handle_stop = MagicMock()
    extra = {"execution.optin_agent.handle_stop": mock_handle_stop}
    patchers = _apply_patches(extra)
    try:
        result = route_message(_sms("STOP"))
        assert result == "optin_stop"
        mock_handle_stop.assert_called_once()
        # First arg is the client dict, second is the sender phone
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
# Step 2 — Clock in / clock out (employees only)
# ---------------------------------------------------------------------------

def test_clock_in_delegates_to_clock_agent():
    """CLOCK IN from a known employee should call clock_agent.handle_clock."""
    mock_handle_clock = MagicMock()
    extra = {"execution.clock_agent.handle_clock": mock_handle_clock}
    patchers = _apply_patches(extra)
    try:
        result = route_message(_sms("CLOCK IN"))
        assert result == "clock_agent"
        mock_handle_clock.assert_called_once()
        kwargs = mock_handle_clock.call_args.kwargs
        assert kwargs["employee"]["id"] == MOCK_EMPLOYEE["id"]
        assert kwargs["client"]["id"] == MOCK_CLIENT["id"]
        assert kwargs["raw_input"] == "CLOCK IN"
    finally:
        _stop_patches(patchers)


def test_clock_out_delegates_to_clock_agent():
    """CLOCK OUT from a known employee should call clock_agent.handle_clock."""
    mock_handle_clock = MagicMock()
    extra = {"execution.clock_agent.handle_clock": mock_handle_clock}
    patchers = _apply_patches(extra)
    try:
        result = route_message(_sms("CLOCK OUT"))
        assert result == "clock_agent"
        mock_handle_clock.assert_called_once()
    finally:
        _stop_patches(patchers)


def test_clock_in_from_unknown_sender_falls_through_to_unmatched():
    """A non-employee texting CLOCK IN must NOT trigger clock_agent."""
    mock_handle_clock = MagicMock()
    mock_send = MagicMock()
    patchers = []
    for target, repl in {
        "execution.sms_router.get_client_by_phone": lambda phone: MOCK_CLIENT,
        "execution.sms_router.get_employee_by_phone": lambda cid, phone: None,
        "execution.clock_agent.handle_clock": mock_handle_clock,
        "execution.sms_send.send_sms": mock_send,
    }.items():
        p = patch(target, repl)
        p.start()
        patchers.append(p)
    try:
        result = route_message(_sms("CLOCK IN"))
        assert result == "unmatched"
        mock_handle_clock.assert_not_called()
        mock_send.assert_not_called()  # Hard Rule #2
    finally:
        _stop_patches(patchers)


# ---------------------------------------------------------------------------
# Step 3 — Everything else → PWA redirect (or ignored if non-employee)
# ---------------------------------------------------------------------------

def test_done_now_returns_pwa_redirect():
    """DONE used to route to invoice_agent. Now it should hit the PWA redirect."""
    mock_send = MagicMock()
    extra = {"execution.sms_send.send_sms": mock_send}
    patchers = _apply_patches(extra)
    try:
        result = route_message(_sms("DONE pumped 1000 gal tank Beverly $350"))
        assert result == "pwa_redirect"
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["to_number"] == "+15555550100"
        assert "Bolts11 app" in kwargs["message_body"]
        assert "/pwa/" in kwargs["message_body"]
        assert kwargs["from_number"] == MOCK_CLIENT["phone"]
    finally:
        _stop_patches(patchers)


def test_estimate_keyword_returns_pwa_redirect():
    """EST used to route to proposal_agent. Now it should hit the PWA redirect."""
    mock_send = MagicMock()
    extra = {"execution.sms_send.send_sms": mock_send}
    patchers = _apply_patches(extra)
    try:
        result = route_message(_sms("EST pump and camera Beverly Whitaker"))
        assert result == "pwa_redirect"
        mock_send.assert_called_once()
    finally:
        _stop_patches(patchers)


def test_ambiguous_text_returns_pwa_redirect():
    """Ambiguous text used to route to clarification_agent. Now: PWA redirect."""
    mock_send = MagicMock()
    extra = {"execution.sms_send.send_sms": mock_send}
    patchers = _apply_patches(extra)
    try:
        result = route_message(_sms("hey what's up"))
        assert result == "pwa_redirect"
        mock_send.assert_called_once()
    finally:
        _stop_patches(patchers)


def test_set_optin_command_now_returns_pwa_redirect():
    """SET OPTIN used to be a special command. Now it just hits the PWA redirect."""
    mock_send = MagicMock()
    extra = {"execution.sms_send.send_sms": mock_send}
    patchers = _apply_patches(extra)
    try:
        result = route_message(_sms("SET OPTIN +12075551234"))
        assert result == "pwa_redirect"
        mock_send.assert_called_once()
    finally:
        _stop_patches(patchers)


def test_pwa_redirect_uses_first_name_from_employee():
    """The redirect SMS should personalise with the tech's first name."""
    custom_employee = {**MOCK_EMPLOYEE, "name": "Casey Long"}
    mock_send = MagicMock()
    patchers = []
    for target, repl in {
        "execution.sms_router.get_client_by_phone": lambda phone: MOCK_CLIENT,
        "execution.sms_router.get_employee_by_phone": lambda cid, phone: custom_employee,
        "execution.sms_send.send_sms": mock_send,
    }.items():
        p = patch(target, repl)
        p.start()
        patchers.append(p)
    try:
        result = route_message(_sms("how do I create an estimate"))
        assert result == "pwa_redirect"
        body = mock_send.call_args.kwargs["message_body"]
        assert "Hey Casey!" in body
    finally:
        _stop_patches(patchers)


def test_unknown_sender_gets_ignored_no_sms():
    """A non-employee texting non-opt content must be silently ignored (Hard Rule #2)."""
    mock_send = MagicMock()
    patchers = []
    for target, repl in {
        "execution.sms_router.get_client_by_phone": lambda phone: MOCK_CLIENT,
        "execution.sms_router.get_employee_by_phone": lambda cid, phone: None,
        "execution.sms_send.send_sms": mock_send,
    }.items():
        p = patch(target, repl)
        p.start()
        patchers.append(p)
    try:
        result = route_message(_sms("hi can you give me a quote?"))
        assert result == "unmatched"
        mock_send.assert_not_called()  # never SMS a customer
    finally:
        _stop_patches(patchers)


def test_no_client_for_telnyx_number_short_circuits():
    """When the inbound `to_number` doesn't match any client, return 'no_client'."""
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
