"""Tests for vertical config wiring into proposal, invoice, and sms_router agents."""

import sys
import os
from unittest.mock import patch

# Ensure project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_proposal_agent_uses_vertical_job_types():
    """Mock load_vertical to return a custom job_type_map and verify detection."""
    from execution.proposal_agent import detect_job_type

    mock_config = {
        "sms_keywords": {
            "job_type_map": {
                "mow": ["mow", "cut", "grass"],
            }
        },
        "default_job_type": "mow",
    }

    with patch("execution.proposal_agent.load_vertical", return_value=mock_config):
        with patch("execution.proposal_agent.get_default_job_type", return_value="mow"):
            result = detect_job_type("need the lawn mowed", vertical_key="landscaping")
            assert result == "mow", f"Expected 'mow', got '{result}'"


def test_invoice_agent_loads_vertical_prompts():
    """Verify vertical prompts are injected into the invoice system prompt."""
    from execution.invoice_agent import build_system_prompt

    mock_client = {
        "business_name": "Test Landscaping",
        "owner_name": "Test Owner",
        "personality": "We mow lawns.",
        "trade_vertical": "landscaping",
    }

    with patch(
        "execution.invoice_agent._load_vertical_prompts",
        return_value="USE LANDSCAPING LANGUAGE",
    ):
        prompt = build_system_prompt(mock_client)
        assert "USE LANDSCAPING LANGUAGE" in prompt, "Vertical prompts not found in system prompt"


def test_sms_router_uses_vertical_keywords():
    """Mock load_vertical to return custom invoice keywords for a different vertical."""
    from execution.sms_router import _invoice_keywords

    mock_config = {
        "sms_keywords": {
            "invoice": ["mowed", "cut", "trimmed"],
        }
    }

    with patch("execution.sms_router.load_vertical", return_value=mock_config):
        result = _invoice_keywords("landscaping")
        assert "mowed" in result
        assert "baffle" not in result


def test_sewer_drain_keywords_still_work():
    """Verify real sewer_drain config loads without mocking."""
    from execution.sms_router import _invoice_keywords

    result = _invoice_keywords("sewer_drain")
    assert "pump" in result, f"'pump' not found in sewer_drain invoice keywords: {result}"
    assert "baffle" in result, f"'baffle' not found in sewer_drain invoice keywords: {result}"
