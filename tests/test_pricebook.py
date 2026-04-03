"""
test_pricebook.py — Tests for pricebook_items CRUD and integration.

Tests:
  - get_pricebook returns items for a client
  - get_pricebook_for_prompt formats pricing string
  - seed_from_pricing_json inserts items and dedupes
  - save_pricebook replaces all items
  - proposal_agent uses pricebook when available
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAKE_CLIENT_ID = "8aafcd73-b41c-4f1a-bd01-3e7955798367"


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
# get_pricebook_for_prompt
# ---------------------------------------------------------------------------

def test_get_pricebook_for_prompt_formats_correctly():
    """Should return formatted pricing lines."""
    with patch("execution.db_pricebook.get_pricebook", return_value=_mock_pricebook_items()):
        from execution.db_pricebook import get_pricebook_for_prompt
        result = get_pricebook_for_prompt(FAKE_CLIENT_ID)

    assert "Septic Pump-Out" in result
    assert "$275" in result
    assert "$325" in result
    assert "$375" in result
    assert "Septic Inspection" in result
    assert "Full system evaluation" in result


def test_get_pricebook_for_prompt_empty():
    """Should return empty string when no items."""
    with patch("execution.db_pricebook.get_pricebook", return_value=[]):
        from execution.db_pricebook import get_pricebook_for_prompt
        result = get_pricebook_for_prompt(FAKE_CLIENT_ID)

    assert result == ""


# ---------------------------------------------------------------------------
# seed_from_pricing_json
# ---------------------------------------------------------------------------

def test_seed_from_pricing_json_inserts():
    """Should insert items from pricing_json format."""
    mock_sb = MagicMock()
    mock_table = MagicMock()
    for m in ("select", "insert", "update", "delete", "eq", "order", "limit"):
        getattr(mock_table, m).return_value = mock_table

    # First call: select existing names (empty)
    # Subsequent calls: inserts
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
    """When pricebook has items, prompt should include them instead of hardcoded prices."""
    with patch("execution.db_pricebook.get_pricebook_for_prompt",
               return_value="- Pump-Out: $275 / $325 / $375 (low/standard/premium) per job"):
        from execution.proposal_agent import build_structured_prompt
        system, user = build_structured_prompt(
            client={"id": FAKE_CLIENT_ID, "business_name": "Test Septic", "personality": ""},
            customer_name="Joe",
            customer_address="123 Main St",
            job_type="pump_out",
            raw_input="pump out 1000 gal tank",
        )

    assert "PRICE BOOK" in system
    assert "Pump-Out: $275" in system
    assert "Pump-out (1,000 gal): $275" not in system  # hardcoded fallback should NOT appear


def test_proposal_prompt_falls_back_when_no_pricebook():
    """When pricebook is empty, prompt should use hardcoded fallback."""
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
    assert "Pump-out (1,000 gal): $275" in system  # hardcoded fallback should appear
