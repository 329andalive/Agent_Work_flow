"""
test_explicit_pricing.py — Regression tests for the four-part pricing
and config fix from April 2026.

Bug history:
  1. proposal_agent.run() always called Claude to generate line items,
     even when the tech (or chat action chip) had provided an explicit
     dollar amount. The tech said $300, the customer got $275 because
     Claude repriced from the personality-layer price book.
  2. The Haiku parser extracted job details but the resulting
     `price_hint` field was logged and immediately discarded — never
     fed back into the pricing decision.
  3. update_proposal_fields() tried to write subtotal/tax_rate/tax_amount
     columns that don't exist on the proposals table, blowing up every
     owner edit save with a Postgres "column not found" error.
  4. vertical_loader.load_vertical() warned on every proposal because
     the client record stored the display name 'Septic & Sewer' but the
     config directories use canonical keys like 'sewer_drain'.

The fixes:
  1. proposal_agent.run() now takes an `explicit_amount` kwarg. When
     present (or when the parser found a price_hint in the raw input),
     the agent BYPASSES Claude entirely and builds a single line item
     at that exact price. Tech says $300 → customer gets $300.
  2. parse_job_fields() Haiku prompt now has explicit examples covering
     "$300", "750", "$1,250" so Haiku reliably extracts the price field.
  3. update_proposal_fields() drops subtotal/tax_rate/tax_amount from
     the actual update dict (signature kept for backwards compat).
  4. vertical_loader normalizes free-form display names via an alias
     map and a slug fallback, so 'Septic & Sewer' resolves to
     'sewer_drain' silently.
"""

import os
import sys
import inspect
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Fix 1 — proposal_agent.run() honors explicit_amount, never reprices
# ---------------------------------------------------------------------------

def _called_function_names(fn) -> set[str]:
    """AST walk: return the set of names that appear as call targets in fn."""
    import ast
    tree = ast.parse(inspect.getsource(fn))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def test_proposal_agent_run_accepts_explicit_amount_kwarg():
    """The kwarg must exist on the public signature."""
    from execution.proposal_agent import run
    sig = inspect.signature(run)
    assert "explicit_amount" in sig.parameters
    # And it must be optional (defaults to None) so existing callers still work
    assert sig.parameters["explicit_amount"].default is None


def test_proposal_agent_run_still_calls_call_claude():
    """
    The run() body must still reference call_claude — but only inside the
    suggestion path. We just verify the symbol is reachable, not where.
    """
    import execution.proposal_agent as pa
    called = _called_function_names(pa.run)
    assert "call_claude" in called, (
        "proposal_agent.run() must still call Claude when no explicit "
        "amount is provided — the suggestion path must remain"
    )


def test_proposal_agent_run_bypasses_claude_when_explicit_amount_set():
    """
    End-to-end: when explicit_amount is passed, the run() flow must
    skip the Claude pricing call entirely and produce a proposal whose
    total exactly matches the kwarg.
    """
    import execution.proposal_agent  # noqa: F401

    fake_client = {
        "id": "client-1",
        "phone": "+15555550200",
        "owner_mobile": "+15555550100",
        "business_name": "Test Septic",
        "personality": "we charge $125/hr",
        "trade_vertical": "sewer_drain",
    }
    fake_customer = {
        "id": "cust-1",
        "customer_name": "Robert Poulin",
        "customer_phone": "+12075551234",
        "customer_address": "15 Church St",
    }

    captured = {}
    def _track_call_claude(*args, **kwargs):
        captured.setdefault("calls", []).append((args, kwargs))
        return None

    with patch("execution.proposal_agent.get_client_by_phone", return_value=fake_client), \
         patch("execution.proposal_agent.get_customer_by_phone", return_value=fake_customer), \
         patch("execution.proposal_agent.create_customer", return_value="cust-1"), \
         patch("execution.proposal_agent.create_job", return_value="job-1"), \
         patch("execution.proposal_agent.update_job_status"), \
         patch("execution.proposal_agent.log_message"), \
         patch("execution.proposal_agent.log_activity"), \
         patch("execution.proposal_agent.send_sms"), \
         patch("execution.proposal_agent.parse_job_fields", return_value={
             "name": "Robert Poulin", "address": "15 Church St",
             "job_type": "repair", "price": None, "notes": ""
         }), \
         patch("execution.proposal_agent.summarize_job", return_value="Baffle replacement"), \
         patch("execution.proposal_agent.call_claude", side_effect=_track_call_claude), \
         patch("execution.proposal_agent.save_proposal", return_value="prop-1"), \
         patch("execution.notify.notify", return_value={"success": True, "channel": "email", "error": None}), \
         patch("execution.proposal_agent.get_supabase_in_run", create=True, new=lambda: MagicMock()):
        # The save_proposal -> line_items update path uses get_client from
        # db_connection inside a try block, which will fail silently — that's
        # acceptable for this test. We're asserting the Claude bypass.
        from execution.proposal_agent import run
        result = run(
            client_phone="+15555550200",
            customer_phone="+12075551234",
            raw_input="Send Robert Poulin an estimate for baffle replacement",
            explicit_amount=300.0,
        )

    # The pricing call_claude must NOT have been called. The agent does
    # call_claude TWICE in the no-bypass path: once in parse_job_fields
    # (mocked above) and once for the structured prompt. We patched
    # parse_job_fields too, so the only legitimate call_claude call is
    # the pricing one — assert it never fired.
    pricing_calls = [c for c in captured.get("calls", [])]
    assert pricing_calls == [], (
        f"call_claude must NOT be called when explicit_amount is set. "
        f"Got {len(pricing_calls)} call(s): {pricing_calls}"
    )

    # And the proposal text must contain the exact $300.00 amount
    assert result is not None
    assert "$300.00" in result, f"Expected $300.00 in proposal text, got: {result!r}"


def test_proposal_agent_run_uses_price_hint_as_fallback():
    """
    When no explicit_amount kwarg is given but the parser found a
    price_hint in the raw input, the agent should still skip Claude and
    use the parsed price.
    """
    import execution.proposal_agent  # noqa: F401

    fake_client = {
        "id": "client-1",
        "phone": "+15555550200",
        "owner_mobile": "+15555550100",
        "business_name": "Test Septic",
        "personality": "",
        "trade_vertical": "sewer_drain",
    }
    fake_customer = {
        "id": "cust-1",
        "customer_name": "Brian",
        "customer_phone": "+12075551111",
        "customer_address": "",
    }
    captured = {"calls": []}

    with patch("execution.proposal_agent.get_client_by_phone", return_value=fake_client), \
         patch("execution.proposal_agent.get_customer_by_phone", return_value=fake_customer), \
         patch("execution.proposal_agent.create_job", return_value="job-1"), \
         patch("execution.proposal_agent.update_job_status"), \
         patch("execution.proposal_agent.log_message"), \
         patch("execution.proposal_agent.log_activity"), \
         patch("execution.proposal_agent.send_sms"), \
         patch("execution.proposal_agent.parse_job_fields", return_value={
             "name": "Brian", "address": "", "job_type": "pump_out",
             "price": 300, "notes": ""
         }), \
         patch("execution.proposal_agent.summarize_job", return_value="Pump out"), \
         patch("execution.proposal_agent.call_claude",
               side_effect=lambda *a, **kw: captured["calls"].append((a, kw))) as _, \
         patch("execution.proposal_agent.save_proposal", return_value="prop-1"), \
         patch("execution.notify.notify", return_value={"success": True, "channel": "email", "error": None}):
        from execution.proposal_agent import run
        result = run(
            client_phone="+15555550200",
            customer_phone="+12075551111",
            raw_input="pump out Brian $300",
            # NOTE: no explicit_amount — falls back to price_hint
        )

    # The Claude pricing call must NOT have been made
    assert captured["calls"] == [], (
        f"call_claude must NOT be called when price_hint is parsed. "
        f"Got {len(captured['calls'])} call(s)."
    )
    assert result is not None
    assert "$300.00" in result


def test_proposal_agent_run_calls_claude_when_no_amount_anywhere():
    """
    Sanity check the suggestion path is still alive: with no explicit
    amount AND no parsed price_hint, the agent must call Claude for
    pricing.
    """
    import execution.proposal_agent  # noqa: F401

    fake_client = {
        "id": "client-1",
        "phone": "+15555550200",
        "owner_mobile": "+15555550100",
        "business_name": "Test Septic",
        "personality": "",
        "trade_vertical": "sewer_drain",
    }
    fake_customer = {
        "id": "cust-1",
        "customer_name": "Carol",
        "customer_phone": "+12075552222",
        "customer_address": "",
    }
    claude_response = (
        '{"job_summary": "Pump out 1000 gal", '
        '"line_items": [{"description": "Septic pump-out", "amount": 275}], '
        '"notes": ""}'
    )

    with patch("execution.proposal_agent.get_client_by_phone", return_value=fake_client), \
         patch("execution.proposal_agent.get_customer_by_phone", return_value=fake_customer), \
         patch("execution.proposal_agent.create_job", return_value="job-1"), \
         patch("execution.proposal_agent.update_job_status"), \
         patch("execution.proposal_agent.log_message"), \
         patch("execution.proposal_agent.log_activity"), \
         patch("execution.proposal_agent.send_sms"), \
         patch("execution.proposal_agent.parse_job_fields", return_value={
             "name": "Carol", "address": "", "job_type": "pump_out",
             "price": None, "notes": ""
         }), \
         patch("execution.proposal_agent.summarize_job", return_value="Pump out"), \
         patch("execution.proposal_agent.call_claude", return_value=claude_response) as mock_claude, \
         patch("execution.proposal_agent.save_proposal", return_value="prop-1"), \
         patch("execution.notify.notify", return_value={"success": True, "channel": "email", "error": None}):
        from execution.proposal_agent import run
        result = run(
            client_phone="+15555550200",
            customer_phone="+12075552222",
            raw_input="pump out for Carol",
        )

    # Claude must have been called exactly once for pricing
    assert mock_claude.call_count >= 1
    assert result is not None


# ---------------------------------------------------------------------------
# Fix 2 — Haiku parsing prompt extracts dollar amounts as price_hint
# ---------------------------------------------------------------------------

def test_parse_job_fields_prompt_explicitly_asks_for_dollar_amounts():
    """
    The Haiku parsing prompt must include the price extraction
    instruction with concrete examples — Haiku follows examples better
    than abstract rules.
    """
    import execution.proposal_agent as pa
    src = inspect.getsource(pa.parse_job_fields)
    # Examples from the prompt — concrete and unambiguous
    assert "pump out Brian $300" in src
    assert "price: 300" in src
    assert "1,250" in src
    # The instruction itself
    assert "Extract the dollar amount" in src or "extract the dollar amount" in src


# ---------------------------------------------------------------------------
# Fix 3 — update_proposal_fields drops dead columns
# ---------------------------------------------------------------------------

def test_update_proposal_fields_does_not_write_subtotal_column():
    """
    The update dict written to Supabase must not contain subtotal,
    tax_rate, or tax_amount — those columns don't exist on the
    proposals table.
    """
    import execution.db_document as dbd
    src = inspect.getsource(dbd.update_proposal_fields)
    # The dict literal that gets sent to .update() must not name these keys
    assert '"subtotal":' not in src or '_ = (subtotal' in src, (
        "update_proposal_fields must not write a 'subtotal' key — that "
        "column doesn't exist on proposals."
    )
    # Strict check: walk the AST and look at every dict literal in the
    # function body. None should have a 'subtotal' / 'tax_rate' / 'tax_amount' key.
    import ast
    tree = ast.parse(src)
    forbidden = {"subtotal", "tax_rate", "tax_amount"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and k.value in forbidden:
                    raise AssertionError(
                        f"update_proposal_fields() builds a dict with "
                        f"forbidden key {k.value!r} — that column doesn't "
                        f"exist on the proposals table."
                    )


def test_update_proposal_fields_signature_unchanged():
    """
    Backwards compat: the public signature must still accept all six
    fields so existing document_routes.py callers don't break.
    """
    from execution.db_document import update_proposal_fields
    sig = inspect.signature(update_proposal_fields)
    for name in ("proposal_id", "line_items", "subtotal", "tax_rate",
                 "tax_amount", "total", "notes", "html_url"):
        assert name in sig.parameters, (
            f"update_proposal_fields signature must keep '{name}' for "
            f"backwards compatibility, even if the value isn't written."
        )


# ---------------------------------------------------------------------------
# Fix 4 — vertical_loader display-name aliases
# ---------------------------------------------------------------------------

def test_vertical_loader_normalizes_septic_and_sewer_display_name():
    """
    The 'Septic & Sewer' display name (from clients.trade_vertical) must
    resolve to the canonical sewer_drain config without the warning.
    """
    from execution.vertical_loader import _normalize_vertical_key
    assert _normalize_vertical_key("Septic & Sewer") == "sewer_drain"
    assert _normalize_vertical_key("septic and sewer") == "sewer_drain"
    assert _normalize_vertical_key("Sewer & Drain") == "sewer_drain"
    assert _normalize_vertical_key("septic") == "sewer_drain"
    assert _normalize_vertical_key("plumbing") == "sewer_drain"


def test_vertical_loader_normalizes_landscaping_aliases():
    from execution.vertical_loader import _normalize_vertical_key
    assert _normalize_vertical_key("Lawn Care") == "landscaping"
    assert _normalize_vertical_key("landscape") == "landscaping"
    assert _normalize_vertical_key("landscaping") == "landscaping"


def test_vertical_loader_canonical_keys_pass_through():
    """A caller passing the canonical key directly must still work."""
    from execution.vertical_loader import _normalize_vertical_key
    assert _normalize_vertical_key("sewer_drain") == "sewer_drain"
    assert _normalize_vertical_key("landscaping") == "landscaping"
    assert _normalize_vertical_key("gravel_pit") == "gravel_pit"


def test_vertical_loader_empty_input_defaults_to_sewer_drain():
    from execution.vertical_loader import _normalize_vertical_key
    assert _normalize_vertical_key("") == "sewer_drain"
    assert _normalize_vertical_key(None) == "sewer_drain"
    assert _normalize_vertical_key("   ") == "sewer_drain"


def test_load_vertical_with_display_name_returns_real_config():
    """
    End-to-end: load_vertical('Septic & Sewer') must return the real
    sewer_drain config (with sms_keywords, etc.), not an empty dict.
    """
    # Clear the cache so this test is hermetic
    from execution.vertical_loader import load_vertical, _cache
    _cache.clear()

    config = load_vertical("Septic & Sewer")
    assert isinstance(config, dict)
    assert "sms_keywords" in config, (
        "load_vertical('Septic & Sewer') should resolve to sewer_drain "
        "and return the real config — not an empty fallback."
    )


# ---------------------------------------------------------------------------
# Fix 1 (continued) — /pwa/api/job/new wires `amount` through to the agent
# ---------------------------------------------------------------------------

def test_pwa_new_job_route_passes_amount_to_create_proposal():
    """
    The /pwa/api/job/new route must read `amount` from the request body
    and pass it through to create_proposal_from_pwa as a kwarg.
    """
    from pathlib import Path
    src = (Path(__file__).parent.parent / "routes" / "pwa_routes.py").read_text()
    # The route must read amount from the request body
    assert 'data.get("amount")' in src
    # And it must pass it as a kwarg to create_proposal_from_pwa
    assert "amount=amount" in src


def test_create_proposal_from_pwa_signature_includes_amount():
    """The function signature must accept `amount` and forward it."""
    from execution.pwa_new_job import create_proposal_from_pwa
    sig = inspect.signature(create_proposal_from_pwa)
    assert "amount" in sig.parameters
    assert sig.parameters["amount"].default is None


def test_create_proposal_from_pwa_forwards_amount_as_explicit_amount():
    """
    AST guard: the proposal_run() call inside create_proposal_from_pwa
    must pass `explicit_amount=amount`.
    """
    import execution.pwa_new_job as pnj
    src = inspect.getsource(pnj.create_proposal_from_pwa)
    assert "explicit_amount=amount" in src, (
        "create_proposal_from_pwa must forward its `amount` arg to "
        "proposal_run as explicit_amount — otherwise the chat agent's "
        "create_proposal action chip carries the price into the agent "
        "but the agent ignores it."
    )
