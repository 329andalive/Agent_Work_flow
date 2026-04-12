"""
test_guided_estimate.py — Unit tests for the guided estimate state machine.

Tests cover:
  - Intent detection (is_estimate_intent)
  - Line item parsing (_parse_line_item)
  - Dollar amount parsing (_parse_dollar_amount)
  - Pricing reference text generation (get_pricing_reference)
  - Full state machine happy path via handle_input()
  - Custom job type path (never dead-ends on unrecognised job)
  - Edge cases: no customer match, cancel, done with no line items, bad price

All DB calls are mocked — no Supabase connection required.
Run with: pytest tests/test_guided_estimate.py -v
"""

import json
import pytest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_CLIENT_ID   = "00000000-0000-0000-0000-000000000001"
TEST_EMPLOYEE_ID = "00000000-0000-0000-0000-000000000002"
TEST_SESSION_ID  = "00000000-0000-0000-0000-000000000003"
TEST_CUSTOMER_ID = "00000000-0000-0000-0000-000000000004"


def _make_session(**overrides) -> dict:
    """Return a minimal estimate_session dict."""
    base = {
        "id":                 "sess-pk-0001",
        "client_id":          TEST_CLIENT_ID,
        "employee_id":        TEST_EMPLOYEE_ID,
        "session_id":         TEST_SESSION_ID,
        "status":             "gathering",
        "customer_id":        None,
        "customer_confirmed": False,
        "job_type":           None,
        "job_type_confirmed": False,
        "primary_price":      None,
        "line_items":         [],
        "notes":              None,
        "current_step":       "ask_customer",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

class TestIsEstimateIntent:

    def test_create_estimate(self):
        from execution.guided_estimate import is_estimate_intent
        assert is_estimate_intent("create estimate") is True

    def test_new_estimate(self):
        from execution.guided_estimate import is_estimate_intent
        assert is_estimate_intent("new estimate") is True

    def test_estimate_for_name(self):
        from execution.guided_estimate import is_estimate_intent
        assert is_estimate_intent("estimate for Bill Tardif") is True

    def test_start_quote(self):
        from execution.guided_estimate import is_estimate_intent
        assert is_estimate_intent("start a quote") is True

    def test_make_bid(self):
        from execution.guided_estimate import is_estimate_intent
        assert is_estimate_intent("make a bid") is True

    def test_not_estimate_intent(self):
        from execution.guided_estimate import is_estimate_intent
        assert is_estimate_intent("mark job done") is False
        assert is_estimate_intent("clock in") is False
        assert is_estimate_intent("what's my route today") is False
        assert is_estimate_intent("pump out") is False


# ---------------------------------------------------------------------------
# Dollar amount parsing
# ---------------------------------------------------------------------------

class TestParseDollarAmount:

    def test_plain_number(self):
        from execution.guided_estimate import _parse_dollar_amount
        assert _parse_dollar_amount("325") == 325.0

    def test_dollar_sign(self):
        from execution.guided_estimate import _parse_dollar_amount
        assert _parse_dollar_amount("$325") == 325.0

    def test_with_cents(self):
        from execution.guided_estimate import _parse_dollar_amount
        assert _parse_dollar_amount("325.50") == 325.50

    def test_with_comma(self):
        from execution.guided_estimate import _parse_dollar_amount
        assert _parse_dollar_amount("1,250") == 1250.0

    def test_text_no_number(self):
        from execution.guided_estimate import _parse_dollar_amount
        assert _parse_dollar_amount("done") is None

    def test_empty(self):
        from execution.guided_estimate import _parse_dollar_amount
        assert _parse_dollar_amount("") is None

    def test_zero(self):
        from execution.guided_estimate import _parse_dollar_amount
        assert _parse_dollar_amount("0") == 0.0


# ---------------------------------------------------------------------------
# Line item parsing
# ---------------------------------------------------------------------------

class TestParseLineItem:

    def test_standard_format(self):
        from execution.guided_estimate import _parse_line_item
        desc, amt = _parse_line_item("disposal fee $45")
        assert desc == "Disposal fee"
        assert amt == 45.0

    def test_no_dollar_sign(self):
        from execution.guided_estimate import _parse_line_item
        desc, amt = _parse_line_item("travel charge 75")
        assert desc == "Travel charge"
        assert amt == 75.0

    def test_no_amount_returns_none(self):
        from execution.guided_estimate import _parse_line_item
        assert _parse_line_item("disposal fee") is None

    def test_empty_returns_none(self):
        from execution.guided_estimate import _parse_line_item
        assert _parse_line_item("") is None

    def test_capitalises_description(self):
        from execution.guided_estimate import _parse_line_item
        desc, _ = _parse_line_item("extra bags $30")
        assert desc[0].isupper()

    def test_decimal_amount(self):
        from execution.guided_estimate import _parse_line_item
        desc, amt = _parse_line_item("filter $12.50")
        assert amt == 12.50


# ---------------------------------------------------------------------------
# Custom job type slugification
# ---------------------------------------------------------------------------

class TestSlugifyJobType:

    def test_basic_slug(self):
        from execution.guided_estimate import _slugify_job_type
        assert _slugify_job_type("grease trap cleaning") == "custom_grease_trap_cleaning"

    def test_strips_special_chars(self):
        from execution.guided_estimate import _slugify_job_type
        result = _slugify_job_type("pump-out / inspection")
        assert result.startswith("custom_")
        assert "/" not in result
        assert "-" not in result

    def test_empty_falls_back(self):
        from execution.guided_estimate import _slugify_job_type
        result = _slugify_job_type("")
        assert result == "custom_job"

    def test_caps_length(self):
        from execution.guided_estimate import _slugify_job_type
        long_input = "a" * 200
        result = _slugify_job_type(long_input)
        # "custom_" (7) + up to 60 chars
        assert len(result) <= 68


# ---------------------------------------------------------------------------
# Pricing reference (mocked DB)
# ---------------------------------------------------------------------------

class TestGetPricingReference:

    def _mock_sb_with_rows(self, rows):
        mock_result = MagicMock()
        mock_result.data = rows
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.order.return_value = mock_table
        mock_table.limit.return_value = mock_table
        mock_table.execute.return_value = mock_result
        mock_sb = MagicMock()
        mock_sb.table.return_value = mock_table
        return mock_sb

    def test_customer_history_3_jobs(self):
        from execution.guided_estimate import get_pricing_reference
        rows = [{"amount": 300}, {"amount": 275}, {"amount": 325}]
        sb = self._mock_sb_with_rows(rows)
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            ref = get_pricing_reference(TEST_CLIENT_ID, "pump_out", TEST_CUSTOMER_ID)
        assert ref is not None
        assert "pump out" in ref.lower()
        assert "$" in ref

    def test_no_history_returns_none(self):
        from execution.guided_estimate import get_pricing_reference
        sb = self._mock_sb_with_rows([])
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            ref = get_pricing_reference(TEST_CLIENT_ID, "pump_out", TEST_CUSTOMER_ID)
        assert ref is None

    def test_single_record_returns_none(self):
        from execution.guided_estimate import get_pricing_reference
        sb = self._mock_sb_with_rows([{"amount": 300}])
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            ref = get_pricing_reference(TEST_CLIENT_ID, "pump_out", TEST_CUSTOMER_ID)
        assert ref is None


# ---------------------------------------------------------------------------
# State machine — handle_input() flows
# ---------------------------------------------------------------------------

class TestHandleInput:

    def _mock_sb(self, table_data: dict = None):
        """Mock supabase that returns configured data per table name."""
        table_data = table_data or {}

        def _make_table(name):
            rows = table_data.get(name, [])
            mock_result = MagicMock()
            mock_result.data = rows
            t = MagicMock()
            t.select.return_value = t
            t.insert.return_value = t
            t.update.return_value = t
            t.eq.return_value = t
            t.neq.return_value = t
            t.not_.return_value = t
            t.in_.return_value = t
            t.ilike.return_value = t
            t.order.return_value = t
            t.limit.return_value = t
            t.single.return_value = t
            t.execute.return_value = mock_result
            return t

        sb = MagicMock()
        sb.table.side_effect = _make_table
        return sb

    # --- Global: cancel ---

    def test_cancel_from_any_state(self):
        from execution.guided_estimate import handle_input
        session = _make_session(current_step="ask_price")
        sb = self._mock_sb()
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "cancel", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        assert result["success"] is True
        assert "cancel" in result["reply"].lower()
        assert result["action"] is None

    def test_nevermind_cancels(self):
        from execution.guided_estimate import handle_input
        session = _make_session(current_step="ask_job_type")
        sb = self._mock_sb()
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "nevermind", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        assert "cancel" in result["reply"].lower()

    # --- S1: ask_customer ---

    def test_customer_not_found(self):
        from execution.guided_estimate import handle_input
        session = _make_session(current_step="ask_customer")
        sb = self._mock_sb({"customers": []})
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "Zork Nonexistent", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        assert result["success"] is True
        assert result["action"] is None
        reply = result["reply"].lower()
        assert "don't have" in reply or "no customer" in reply or "zork" in reply

    def test_single_customer_found(self):
        from execution.guided_estimate import handle_input
        session = _make_session(current_step="ask_customer")
        customer = {
            "id": TEST_CUSTOMER_ID,
            "customer_name": "Bill Tardif",
            "customer_phone": "+12075551234",
            "customer_address": "140 Granite Hill Rd, Manchester ME",
        }
        sb = self._mock_sb({"customers": [customer]})
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "Bill Tardif", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        assert result["success"] is True
        assert "Bill Tardif" in result["reply"]
        assert "orrect" in result["reply"]

    def test_multiple_customers_shows_list(self):
        from execution.guided_estimate import handle_input
        session = _make_session(current_step="ask_customer")
        customers = [
            {"id": "c1", "customer_name": "Bob Smith", "customer_phone": "+1207", "customer_address": "1 Main St"},
            {"id": "c2", "customer_name": "Bob Jones", "customer_phone": "+1208", "customer_address": "2 Oak Ave"},
        ]
        sb = self._mock_sb({"customers": customers})
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "Bob", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        assert "1)" in result["reply"] or "1." in result["reply"]

    # --- S2: confirm_customer ---

    def test_confirm_yes_advances_to_job_type(self):
        from execution.guided_estimate import handle_input
        candidate = {"id": TEST_CUSTOMER_ID, "customer_name": "Bill Tardif",
                     "customer_phone": "+1207", "customer_address": "140 Granite Hill"}
        session = _make_session(
            current_step="confirm_customer",
            notes=json.dumps({"candidate": candidate}),
        )
        sb = self._mock_sb()
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "yes", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        assert "job" in result["reply"].lower() or "type" in result["reply"].lower()

    def test_confirm_no_goes_back(self):
        from execution.guided_estimate import handle_input
        candidate = {"id": TEST_CUSTOMER_ID, "customer_name": "Bill Tardif",
                     "customer_phone": "+1207", "customer_address": "140 Granite Hill"}
        session = _make_session(
            current_step="confirm_customer",
            notes=json.dumps({"candidate": candidate}),
        )
        sb = self._mock_sb()
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "no", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        assert "customer" in result["reply"].lower()

    # --- S6: ask_job_type ---

    def test_pump_out_keyword_matches(self):
        from execution.guided_estimate import handle_input
        session = _make_session(current_step="ask_job_type", customer_id=TEST_CUSTOMER_ID)
        sb = self._mock_sb({
            "clients":             [{"trade_vertical": "sewer_drain"}],
            "job_pricing_history": [],
        })
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "pump out", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        assert result["success"] is True
        assert "price" in result["reply"].lower()

    def test_unrecognised_job_type_uses_as_custom(self):
        """
        Unknown job types must NEVER dead-end the flow.
        The tech's text is stored as a custom job type and the flow
        advances to ask_price — same as any known type.
        """
        from execution.guided_estimate import handle_input
        session = _make_session(current_step="ask_job_type", customer_id=TEST_CUSTOMER_ID)
        sb = self._mock_sb({
            "clients":             [{"trade_vertical": "sewer_drain"}],
            "job_pricing_history": [],
        })
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            with patch("execution.guided_estimate.call_claude", return_value="other"):
                result = handle_input(session, "grease trap cleaning", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        # Flow must advance to price step — not error out
        assert result["success"] is True
        assert "price" in result["reply"].lower()
        assert result["action"] is None  # chip comes at review, not here

    def test_custom_job_type_label_uses_raw_text(self):
        """
        The display label in the reply should use the tech's original
        text (readable), not the internal custom_ slug.
        """
        from execution.guided_estimate import handle_input
        session = _make_session(current_step="ask_job_type", customer_id=TEST_CUSTOMER_ID)
        sb = self._mock_sb({
            "clients":             [{"trade_vertical": "sewer_drain"}],
            "job_pricing_history": [],
        })
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            with patch("execution.guided_estimate.call_claude", return_value="other"):
                result = handle_input(session, "grease trap cleaning", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        # "custom_grease_trap_cleaning" must NOT appear in the reply
        assert "custom_" not in result["reply"].lower()
        assert "grease trap" in result["reply"].lower()

    def test_pricing_reference_shown_when_history_exists(self):
        from execution.guided_estimate import handle_input
        session = _make_session(current_step="ask_job_type", customer_id=TEST_CUSTOMER_ID)
        history_rows = [{"amount": 300}, {"amount": 285}, {"amount": 310}]
        sb = self._mock_sb({
            "clients":             [{"trade_vertical": "sewer_drain"}],
            "job_pricing_history": history_rows,
        })
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "pump out", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        assert "$" in result["reply"] or "averaged" in result["reply"]

    # --- S7a: ask_price ---

    def test_valid_price_accepted(self):
        from execution.guided_estimate import handle_input
        session = _make_session(current_step="ask_price", job_type="pump_out")
        sb = self._mock_sb()
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "325", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        assert result["success"] is True
        assert "325" in result["reply"]
        assert "line item" in result["reply"].lower() or "additional" in result["reply"].lower()

    def test_zero_price_rejected(self):
        from execution.guided_estimate import handle_input
        session = _make_session(current_step="ask_price", job_type="pump_out")
        sb = self._mock_sb()
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "0", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        assert result["action"] is None

    def test_absurd_price_rejected(self):
        from execution.guided_estimate import handle_input
        session = _make_session(current_step="ask_price", job_type="pump_out")
        sb = self._mock_sb()
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "9999999", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        assert result["action"] is None

    def test_non_numeric_price_rejected(self):
        from execution.guided_estimate import handle_input
        session = _make_session(current_step="ask_price", job_type="pump_out")
        sb = self._mock_sb()
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "sounds good", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        assert result["action"] is None
        assert "price" in result["reply"].lower() or "amount" in result["reply"].lower()

    # --- S8: ask_line_items ---

    def test_line_item_parsed_and_added(self):
        from execution.guided_estimate import handle_input
        session = _make_session(
            current_step="ask_line_items",
            job_type="pump_out",
            primary_price=325.0,
            line_items=[],
        )
        sb = self._mock_sb()
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "disposal fee $45", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        assert "disposal" in result["reply"].lower() or "45" in result["reply"]
        assert result["action"] is None

    def test_line_item_no_price_prompts_clarify(self):
        from execution.guided_estimate import handle_input
        session = _make_session(
            current_step="ask_line_items",
            job_type="pump_out",
            primary_price=325.0,
            line_items=[],
        )
        sb = self._mock_sb()
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "disposal fee", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        assert result["action"] is None
        assert "price" in result["reply"].lower() or "amount" in result["reply"].lower()

    def test_done_advances_to_notes(self):
        from execution.guided_estimate import handle_input
        session = _make_session(
            current_step="ask_line_items",
            job_type="pump_out",
            primary_price=325.0,
            line_items=[],
        )
        sb = self._mock_sb()
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "done", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        assert "notes" in result["reply"].lower() or "photo" in result["reply"].lower()

    def test_no_also_advances(self):
        from execution.guided_estimate import handle_input
        session = _make_session(
            current_step="ask_line_items",
            job_type="pump_out",
            primary_price=325.0,
            line_items=[],
        )
        sb = self._mock_sb()
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "no", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)
        assert "notes" in result["reply"].lower() or "done" in result["reply"].lower()

    # --- S9: ask_notes → review chip ---

    def test_done_at_notes_produces_chip(self):
        from execution.guided_estimate import handle_input

        full_session_row = {
            "id":              "sess-pk-0001",
            "client_id":       TEST_CLIENT_ID,
            "employee_id":     TEST_EMPLOYEE_ID,
            "session_id":      TEST_SESSION_ID,
            "status":          "awaiting_line_items",
            "customer_id":     TEST_CUSTOMER_ID,
            "job_type":        "pump_out",
            "primary_price":   325.0,
            "line_items":      [{"description": "Disposal fee", "amount": 45.0}],
            "notes":           None,
            "current_step":    "ask_notes",
        }
        customer_row = {
            "customer_name":    "Bill Tardif",
            "customer_phone":   "+12075551234",
            "customer_address": "140 Granite Hill Rd",
        }
        session = _make_session(
            current_step="ask_notes",
            customer_id=TEST_CUSTOMER_ID,
            job_type="pump_out",
            primary_price=325.0,
            line_items=[{"description": "Disposal fee", "amount": 45.0}],
        )
        sb = self._mock_sb({
            "estimate_sessions": [full_session_row],
            "customers":         [customer_row],
        })
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "done", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)

        assert result["success"] is True
        action = result["action"]
        assert action is not None
        assert action["type"] == "create_proposal"
        assert action["params"]["amount"] == 370.0
        assert action["endpoint"] == "/pwa/api/job/new"

    def test_custom_job_type_chip_strips_prefix(self):
        """
        Review chip for a custom job type should show clean label,
        not the internal custom_ slug.
        """
        from execution.guided_estimate import handle_input

        full_session_row = {
            "id":              "sess-pk-0001",
            "client_id":       TEST_CLIENT_ID,
            "employee_id":     TEST_EMPLOYEE_ID,
            "session_id":      TEST_SESSION_ID,
            "status":          "awaiting_line_items",
            "customer_id":     TEST_CUSTOMER_ID,
            "job_type":        "custom_grease_trap_cleaning",
            "primary_price":   400.0,
            "line_items":      [],
            "notes":           None,
            "current_step":    "ask_notes",
        }
        customer_row = {
            "customer_name":    "Alice Smith",
            "customer_phone":   "+12075559999",
            "customer_address": "10 Elm St",
        }
        session = _make_session(
            current_step="ask_notes",
            customer_id=TEST_CUSTOMER_ID,
            job_type="custom_grease_trap_cleaning",
            primary_price=400.0,
            line_items=[],
        )
        sb = self._mock_sb({
            "estimate_sessions": [full_session_row],
            "customers":         [customer_row],
        })
        with patch("execution.guided_estimate.get_supabase", return_value=sb):
            result = handle_input(session, "done", TEST_CLIENT_ID, TEST_EMPLOYEE_ID)

        assert result["success"] is True
        action = result["action"]
        assert action is not None
        # The description sent to proposal_agent must not expose the slug
        assert "custom_" not in action["params"]["description"].lower()
        assert "grease trap" in action["params"]["description"].lower()


# ---------------------------------------------------------------------------
# db_pricing_history — record_sent_proposal
# ---------------------------------------------------------------------------

class TestRecordSentProposal:

    def test_valid_write_succeeds(self):
        from execution.db_pricing_history import record_sent_proposal
        mock_sb = MagicMock()
        mock_sb.table.return_value.insert.return_value.execute.return_value = MagicMock()
        with patch("execution.db_pricing_history.get_supabase", return_value=mock_sb):
            result = record_sent_proposal(
                client_id=TEST_CLIENT_ID,
                amount=325.0,
                job_type="pump_out",
                customer_id=TEST_CUSTOMER_ID,
            )
        assert result is True
        mock_sb.table.assert_called_once_with("job_pricing_history")

    def test_missing_client_id_returns_false(self):
        from execution.db_pricing_history import record_sent_proposal
        result = record_sent_proposal(client_id="", amount=325.0, job_type="pump_out")
        assert result is False

    def test_zero_amount_returns_false(self):
        from execution.db_pricing_history import record_sent_proposal
        result = record_sent_proposal(client_id=TEST_CLIENT_ID, amount=0, job_type="pump_out")
        assert result is False

    def test_missing_job_type_returns_false(self):
        from execution.db_pricing_history import record_sent_proposal
        result = record_sent_proposal(client_id=TEST_CLIENT_ID, amount=325.0, job_type="")
        assert result is False

    def test_db_exception_returns_false(self):
        from execution.db_pricing_history import record_sent_proposal
        mock_sb = MagicMock()
        mock_sb.table.side_effect = Exception("DB error")
        with patch("execution.db_pricing_history.get_supabase", return_value=mock_sb):
            result = record_sent_proposal(
                client_id=TEST_CLIENT_ID,
                amount=325.0,
                job_type="pump_out",
            )
        assert result is False
