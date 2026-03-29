"""
test_invoice_math.py — Cents preservation and tax calculation tests (T2)

Tests the pure math functions extracted from invoice_agent.py:
  - parse_all_amounts / sum_amounts: dollar extraction from free text
  - calculate_line_item_tax: taxable-only tax with Maine 5.5% rate
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.invoice_agent import parse_all_amounts, sum_amounts, calculate_line_item_tax


# ---------------------------------------------------------------------------
# Amount parsing
# ---------------------------------------------------------------------------

def test_multi_amount_sum_preserves_cents():
    text = "Pumped tank $350, camera $400, extra line $175.25"
    assert sum_amounts(text) == 925.25


def test_single_amount_with_cents():
    text = "$175.25 service call"
    assert sum_amounts(text) == 175.25


def test_comma_formatted_amount():
    text = "$1,250.00 for the job"
    assert sum_amounts(text) == 1250.00


def test_no_amounts_returns_zero():
    """No dollar signs in text — sum_amounts returns 0.0."""
    text = "no cost mentioned"
    assert sum_amounts(text) == 0.0


# ---------------------------------------------------------------------------
# Tax calculation
# ---------------------------------------------------------------------------

MAINE_TAX_RATE = 0.055


def test_tax_calculation_on_taxable_items_only():
    line_items = [
        {"amount": 100, "taxable": True},
        {"amount": 200, "taxable": False},
    ]
    tax, total = calculate_line_item_tax(line_items, MAINE_TAX_RATE)
    assert tax == 5.50
    assert total == 305.50
