"""
test_pricebook.py — Tests for pricebook_items CRUD and integration.

Tests:
  - get_pricebook returns items for a client
  - get_pricebook_for_prompt formats pricing string (mid-only per HARD RULE #8)
  - seed_from_pricing_json inserts items and dedupes
  - save_pricebook replaces all items
  - proposal_agent uses pricebook when available
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAKE_CLIENT_ID = "00000000-0000-0000-0000-000000000001"


def _mock_pricebook_items():
    """Return sample pricebook items."""
    return [
        {"id": "pb-1", "client_id": FAKE_CLIENT_ID, "job_name": "Septic Pump-Out (1000 gal)",
         "price_low": 275, "price_mid": 325, "price_high": 375, "unit_of_measure": "per job",
         "description": None, "is_active": True, "sort_order": 0},
        {"id": "pb-2", "client_id": FAKE_CLIENT_ID, "job_name": "Septic Inspection",
         "price_low": 150, "price_mid": 250, "price_high": 400, "unit_of_measure": "per job",
         "description": "Full system evaluation", "is_active": True, "sort_order": 1},
    ]


# ---------------------------------------------------------------------------
# get_pricebook_for_prompt — HARD RULE #8: standard price only, never the range
# ---------------------------------------------------------------------------

def test_get_pricebook_for_prompt_formats_correctly():
    """
    Should return only the standard (mid) price per line.
    HARD RULE #8: Claude must never see the low or high price —
    showing the range caused Claude to anchor on the low end and
    reprice tech estimates. Only the mid price is shown.
    """
    with patch("execution.db_pricebook.get_pricebook", return_value=_mock_pricebook_items()):
        from execution.db_pricebook import get_pricebook_for_prompt
        result = get_pricebook_for_prompt(FAKE_CLIENT_ID)

    assert "Septic Pump-Out" in result
    assert "$325" in result       # standard (mid) price must appear
    assert "$275" not in result   # low price must NOT leak to Claude
    assert "$375" not in result   # high price must NOT leak to Claude
    assert "Septic Inspection" in result
    assert "$250" in result       # standard price for inspection
    # description is intentionally excluded — keeps prompt tight
    assert "Full system evaluation" not in result


def test_get_pricebook_for_prompt_empty():
    """Should return empty string when no items."""
    with patch("execution.db_pricebook.get_pricebook", return_value=[]):
        from execution.db_pricebook import get_pricebook_for_prompt
        result = get_pricebook_for_prompt(FAKE_CLIENT_ID)

    assert result == ""


def test_get_pricebook_for_prompt_skips_zero_price():
    """Items with no mid or low price should be excluded from the prompt."""
    items = [
        {"id": "pb-1", "job_name": "No Price Item",
         "price_low": 0, "price_mid": 0, "price_high": 0,
         "unit_of_measure": "per job", "is_active": True},
        {"id": "pb-2", "job_name": "Has Price Item",
         "price_low": 100, "price_mid": 150, "price_high": 200,
         "unit_of_measure": "per job", "is_active": True},
    ]
    with patch("execution.db_pricebook.get_pricebook", return_value=items):
        from execution.db_pricebook import get_pricebook_for_prompt
        result = get_pricebook_for_prompt(FAKE_CLIENT_ID)

    assert "No Price Item" not in result
    assert "Has Price Item" in result
    assert "$150" in result


# ---------------------------------------------------------------------------
# seed_from_pricing_json
# ---------------------------------------------------------------------------

def test_seed_from_pricing_json_inserts():
    """Should insert items from pricing_json format."""
    mock_sb = MagicMock()
    mock_table = MagicMock()
    for m in ("select", "insert", "update", "delete", "eq", "order", "limit"):
        getattr(mock_table, m).return_value = mock_table

    result_empty = MagicMock()
    result_empty.data = []
    result_insert = MagicMock()
    result_insert.data = [{"id": "new-1"}]
    mock_table.execute.side_effect = [result_empty, result_insert, result_insert]
    mock_sb.table.return_value = mock_table

    with patch("execution.db_pricebook.get_supabase", return_value=mock_sb):
        from execution.db_pricebook import seed_from_pricing_json
        count = seed_from_pricing_json(
            FAKE_CLIENT_ID,
            [
                {"service": "Pump-Out", "low": 275, "typical": 325, "high": 375},
                {"service": "Inspection", "low": 150, "typical": 250, "high": 400},
            ],
            vertical_key="septic",
        )

    assert count == 2


# ---------------------------------------------------------------------------
# Proposal agent uses pricebook
# ---------------------------------------------------------------------------

def test_proposal_prompt_includes_pricebook():
    """When pricebook has items, prompt should include them and say PRICE BOOK."""
    with patch("execution.db_pricebook.get_pricebook_for_prompt",
               return_value="- Pump-Out: $325 per job"):
        from execution.proposal_agent import build_structured_prompt
        system, user = build_structured_prompt(
            client={"id": FAKE_CLIENT_ID, "business_name": "Test Septic", "personality": ""},
            customer_name="Joe",
            customer_address="123 Main St",
            job_type="pump_out",
            raw_input="pump out 1000 gal tank",
        )

    assert "PRICE BOOK" in system
    assert "Pump-Out: $325" in system


def test_proposal_prompt_falls_back_when_no_pricebook():
    """
    When pricebook is empty, prompt should use the no-pricebook fallback.
    HARD RULE #8: the fallback must NOT contain hardcoded prices — Claude
    should use 0 and let the tech fill in the price on review.
    """
    with patch("execution.db_pricebook.get_pricebook_for_prompt", return_value=""):
        from execution.proposal_agent import build_structured_prompt
        system, user = build_structured_prompt(
            client={"id": FAKE_CLIENT_ID, "business_name": "Test Septic", "personality": ""},
            customer_name="Joe",
            customer_address="123 Main St",
            job_type="pump_out",
            raw_input="pump out",
        )

    assert "PRICE BOOK" not in system
    # No hardcoded prices in the fallback — Claude uses 0, tech fills in on review
    assert "Pump-out (1,000 gal): $275" not in system
    assert "No price book is configured" in system or "no price" in system.lower()


# ---------------------------------------------------------------------------
# learn_from_adjustments
# ---------------------------------------------------------------------------

def test_learn_from_adjustments_updates_price():
    """3+ consistent adjustments should update the pricebook price_mid."""
    mock_sb = MagicMock()

    def _table(name):
        t = MagicMock()
        for m in ("select", "insert", "update", "delete", "eq", "neq",
                  "order", "limit", "ilike", "in_"):
            getattr(t, m).return_value = t
        r = MagicMock()

        if name == "pricing_adjustments":
            r.data = [
                {"service_name": "Pump-Out", "original_price": 275, "adjusted_price": 325, "delta": 50, "direction": "up", "created_at": "2026-04-01"},
                {"service_name": "Pump-Out", "original_price": 275, "adjusted_price": 330, "delta": 55, "direction": "up", "created_at": "2026-04-02"},
                {"service_name": "Pump-Out", "original_price": 275, "adjusted_price": 320, "delta": 45, "direction": "up", "created_at": "2026-04-03"},
            ]
        elif name == "pricebook_items":
            r.data = [
                {"id": "pb-1", "job_name": "Pump-Out", "price_low": 250, "price_mid": 275, "price_high": 350, "is_active": True, "sort_order": 0},
            ]
        else:
            r.data = []
        r.count = len(r.data)
        t.execute.return_value = r
        return t

    mock_sb.table.side_effect = _table

    with patch("execution.db_pricebook.get_supabase", return_value=mock_sb):
        from execution.db_pricebook import learn_from_adjustments
        result = learn_from_adjustments(FAKE_CLIENT_ID)

    assert result["services_updated"] == 1
    assert result["details"][0]["direction"] == "up"
    assert result["details"][0]["old_price"] == 275
    assert result["details"][0]["new_price"] == 325.0


def test_learn_skips_when_insufficient_adjustments():
    """Fewer than 3 adjustments should not trigger an update."""
    mock_sb = MagicMock()

    def _table(name):
        t = MagicMock()
        for m in ("select", "insert", "update", "delete", "eq", "neq",
                  "order", "limit", "ilike", "in_"):
            getattr(t, m).return_value = t
        r = MagicMock()
        if name == "pricing_adjustments":
            r.data = [
                {"service_name": "Pump-Out", "original_price": 275, "adjusted_price": 325, "delta": 50, "direction": "up", "created_at": "2026-04-01"},
                {"service_name": "Pump-Out", "original_price": 275, "adjusted_price": 330, "delta": 55, "direction": "up", "created_at": "2026-04-02"},
            ]
        elif name == "pricebook_items":
            r.data = [{"id": "pb-1", "job_name": "Pump-Out", "price_low": 250, "price_mid": 275, "price_high": 350, "is_active": True, "sort_order": 0}]
        else:
            r.data = []
        r.count = len(r.data)
        t.execute.return_value = r
        return t

    mock_sb.table.side_effect = _table

    with patch("execution.db_pricebook.get_supabase", return_value=mock_sb):
        from execution.db_pricebook import learn_from_adjustments
        result = learn_from_adjustments(FAKE_CLIENT_ID)

    assert result["services_updated"] == 0


def test_learn_skips_mixed_directions():
    """Mixed up/down adjustments should not trigger an update."""
    mock_sb = MagicMock()

    def _table(name):
        t = MagicMock()
        for m in ("select", "insert", "update", "delete", "eq", "neq",
                  "order", "limit", "ilike", "in_"):
            getattr(t, m).return_value = t
        r = MagicMock()
        if name == "pricing_adjustments":
            r.data = [
                {"service_name": "Pump-Out", "original_price": 275, "adjusted_price": 325, "delta": 50, "direction": "up", "created_at": "2026-04-01"},
                {"service_name": "Pump-Out", "original_price": 325, "adjusted_price": 275, "delta": -50, "direction": "down", "created_at": "2026-04-02"},
                {"service_name": "Pump-Out", "original_price": 275, "adjusted_price": 300, "delta": 25, "direction": "up", "created_at": "2026-04-03"},
            ]
        elif name == "pricebook_items":
            r.data = [{"id": "pb-1", "job_name": "Pump-Out", "price_low": 250, "price_mid": 275, "price_high": 350, "is_active": True, "sort_order": 0}]
        else:
            r.data = []
        r.count = len(r.data)
        t.execute.return_value = r
        return t

    mock_sb.table.side_effect = _table

    with patch("execution.db_pricebook.get_supabase", return_value=mock_sb):
        from execution.db_pricebook import learn_from_adjustments
        result = learn_from_adjustments(FAKE_CLIENT_ID)

    assert result["services_updated"] == 0
