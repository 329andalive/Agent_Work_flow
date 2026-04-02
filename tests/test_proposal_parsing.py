"""
test_proposal_parsing.py — Tests for Claude Haiku-based job field extraction.

Tests:
  - parse_job_fields extracts name, address, price from natural text
  - Handles lowercase names, missing fields, empty input
  - Fallback returns safe defaults when Haiku call fails
"""

import os
import sys
import json
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mock_haiku(response_dict):
    """Return a mock call_claude that returns the given dict as JSON."""
    def _fake_call(system_prompt, user_prompt, model="haiku", max_tokens=256):
        return json.dumps(response_dict)
    return _fake_call


# ---------------------------------------------------------------------------
# Natural name + price extraction
# ---------------------------------------------------------------------------

def test_parse_natural_name_and_price():
    """'est. Jeremy holt replace inlet pipe at tank 5 feet $500' → name + price."""
    haiku_response = {
        "name": "Jeremy Holt",
        "address": "",
        "job_type": "repair",
        "price": 500,
        "notes": "replace inlet pipe at tank 5 feet",
    }
    with patch("execution.proposal_agent.call_claude", side_effect=_mock_haiku(haiku_response)):
        from execution.proposal_agent import parse_job_fields
        result = parse_job_fields("est. Jeremy holt replace inlet pipe at tank 5 feet $500")

    assert result["name"].lower() == "jeremy holt"
    assert result["price"] == 500
    assert result["job_type"] == "repair"


# ---------------------------------------------------------------------------
# Lowercase name with address
# ---------------------------------------------------------------------------

def test_parse_lowercase_name_with_address():
    """'carol duggan 12 school st pump out 300' → name + address."""
    haiku_response = {
        "name": "Carol Duggan",
        "address": "12 School St",
        "job_type": "pump_out",
        "price": 300,
        "notes": "pump out",
    }
    with patch("execution.proposal_agent.call_claude", side_effect=_mock_haiku(haiku_response)):
        from execution.proposal_agent import parse_job_fields
        result = parse_job_fields("carol duggan 12 school st pump out 300")

    assert "carol" in result["name"].lower()
    assert "school" in result["address"].lower()
    assert result["price"] == 300


# ---------------------------------------------------------------------------
# Name on a road
# ---------------------------------------------------------------------------

def test_parse_name_on_road():
    """'kevin peavey on pond road needs a cleanout' → name + address."""
    haiku_response = {
        "name": "Kevin Peavey",
        "address": "Pond Road",
        "job_type": "cleanout",
        "price": None,
        "notes": "needs a cleanout",
    }
    with patch("execution.proposal_agent.call_claude", side_effect=_mock_haiku(haiku_response)):
        from execution.proposal_agent import parse_job_fields
        result = parse_job_fields("kevin peavey on pond road needs a cleanout")

    assert result["name"].lower() == "kevin peavey"
    assert "pond" in result["address"].lower()
    assert result["price"] is None


# ---------------------------------------------------------------------------
# No name present
# ---------------------------------------------------------------------------

def test_parse_no_name():
    """'pump out 1000 gal tank route 9' → no name, has address."""
    haiku_response = {
        "name": "",
        "address": "Route 9",
        "job_type": "pump_out",
        "price": None,
        "notes": "1000 gal tank",
    }
    with patch("execution.proposal_agent.call_claude", side_effect=_mock_haiku(haiku_response)):
        from execution.proposal_agent import parse_job_fields
        result = parse_job_fields("pump out 1000 gal tank route 9")

    assert result["name"] == ""
    assert "route" in result["address"].lower()


# ---------------------------------------------------------------------------
# Empty input — safe fallback
# ---------------------------------------------------------------------------

def test_parse_empty_input():
    """Empty string should return safe defaults without calling Claude."""
    from execution.proposal_agent import parse_job_fields
    result = parse_job_fields("")
    assert isinstance(result, dict)
    assert result["name"] == ""
    assert result["address"] == ""
    assert result["price"] is None


# ---------------------------------------------------------------------------
# Claude call fails — fallback
# ---------------------------------------------------------------------------

def test_parse_fallback_on_failure():
    """If Haiku call returns None (API error), fallback returns safe defaults."""
    with patch("execution.proposal_agent.call_claude", return_value=None):
        from execution.proposal_agent import parse_job_fields
        result = parse_job_fields("jeremy holt pump out 350")

    assert isinstance(result, dict)
    assert "name" in result
    assert result["name"] == ""
    assert result["notes"] == "jeremy holt pump out 350"


# ---------------------------------------------------------------------------
# Claude returns markdown-fenced JSON
# ---------------------------------------------------------------------------

def test_parse_strips_markdown_fences():
    """If Haiku wraps response in ```json fences, we strip them."""
    fenced = '```json\n{"name": "Alice Smith", "address": "", "job_type": "pump_out", "price": 275, "notes": "pump out"}\n```'
    with patch("execution.proposal_agent.call_claude", return_value=fenced):
        from execution.proposal_agent import parse_job_fields
        result = parse_job_fields("alice smith pump out $275")

    assert result["name"] == "Alice Smith"
    assert result["price"] == 275
