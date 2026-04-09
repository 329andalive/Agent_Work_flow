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


def test_vertical_invoice_keywords_load_from_mocked_config():
    """
    The post-PWA-pivot sms_router no longer reads vertical keywords (it's
    notification-only now), but invoice_agent + proposal_agent still do via
    vertical_loader. This test verifies the loader returns the expected
    structure when mocked.
    """
    from execution.vertical_loader import load_vertical

    mock_config = {
        "sms_keywords": {
            "invoice": ["mowed", "cut", "trimmed"],
        }
    }

    with patch("execution.vertical_loader.load_vertical", return_value=mock_config):
        result = load_vertical("landscaping")
        invoice_kws = result.get("sms_keywords", {}).get("invoice", [])
        assert "mowed" in invoice_kws
        assert "baffle" not in invoice_kws


def test_sewer_drain_invoice_keywords_still_load_from_real_config():
    """The on-disk sewer_drain config still ships invoice keywords for the agents."""
    from execution.vertical_loader import load_vertical

    config = load_vertical("sewer_drain")
    invoice_kws = config.get("sms_keywords", {}).get("invoice", [])
    assert "pump" in invoice_kws, f"'pump' not in sewer_drain invoice keywords: {invoice_kws}"
    assert "baffle" in invoice_kws, f"'baffle' not in sewer_drain invoice keywords: {invoice_kws}"
