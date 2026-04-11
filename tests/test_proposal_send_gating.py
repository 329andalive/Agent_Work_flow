"""
test_proposal_send_gating.py — Regression tests for the proposal status
and follow-up timing bug.

Bug history (April 2026): proposal_agent.run() was marking newly drafted
proposals as status='sent' AND scheduling a 3-day estimate_followup at
draft time. The followup cron in followup_agent.py treats status='sent'
as "the customer received it," so 3 days later it would generate and
SEND a follow-up message to a customer who had never seen the original
estimate. The owner had no chance to review the draft before the
customer started getting messaged about it.

The fix:
  1. proposal_agent.run() leaves status alone (DB default = 'draft')
     and does NOT schedule the followup at draft time.
  2. /doc/send (the owner's "Approve & Send" route) flips status='sent',
     sets sent_at=now, AND schedules the 3-day followup — only when
     customer delivery actually succeeds.
  3. Belt-and-suspenders: db_proposals.get_latest_sent_proposal_for_customer
     and followup_agent's handle_lost_report query both require
     sent_at IS NOT NULL so any future write that flips status without
     setting sent_at can't surface a draft as if it were sent.
"""

import os
import sys
import inspect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Static guards on proposal_agent — these are what would have prevented the
# bug in the first place. They run as cheap source-level assertions instead
# of mocking the whole Supabase + Claude stack.
# ---------------------------------------------------------------------------

def _called_function_names(fn) -> set[str]:
    """
    Walk the AST of a function body and return the set of names that
    appear as call targets — e.g. `foo(...)` adds 'foo', `mod.bar(...)`
    adds 'bar'. Lets us assert that a function does NOT call certain
    helpers without false-positives from docstring or comment text.
    """
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


def test_proposal_agent_run_does_not_call_update_proposal_status():
    """
    The bug was proposal_agent.run() calling update_proposal_status(id, 'sent')
    after building a draft. That call must not exist in run().
    """
    import execution.proposal_agent as pa
    called = _called_function_names(pa.run)
    assert "update_proposal_status" not in called, (
        "proposal_agent.run() must NOT call update_proposal_status — "
        "drafts stay status='draft' until the owner taps Approve & Send "
        "in /doc/send."
    )


def test_proposal_agent_run_does_not_call_schedule_followup():
    """
    The bug also scheduled the 3-day estimate_followup at draft time.
    schedule_followup() must not be called from run() — that call lives
    in /doc/send now, gated on a successful customer delivery.
    """
    import execution.proposal_agent as pa
    called = _called_function_names(pa.run)
    assert "schedule_followup" not in called, (
        "proposal_agent.run() must NOT call schedule_followup — the "
        "estimate_followup timer starts in /doc/send when the owner "
        "actually approves the draft, not when the AI drafts it."
    )


def test_proposal_agent_no_longer_imports_schedule_followup():
    """
    Stricter version of the above: schedule_followup should not even be
    imported by proposal_agent anymore. If a future change re-introduces
    the import, this test catches it before runtime.
    """
    import ast
    import execution.proposal_agent as pa
    tree = ast.parse(inspect.getsource(pa))
    forbidden = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "schedule_followup":
                    forbidden.append(f"from {node.module} import {alias.name}")
                if alias.name == "update_proposal_status":
                    forbidden.append(f"from {node.module} import {alias.name}")
    assert not forbidden, (
        f"proposal_agent.py must not import these symbols anymore — "
        f"the draft path is gone: {forbidden}"
    )


# ---------------------------------------------------------------------------
# Static guards on document_routes — the send path now owns the followup
# scheduling AND the sent_at write. Verify both calls live there.
# ---------------------------------------------------------------------------

def test_doc_send_route_schedules_followup_after_delivery():
    """
    The /doc/send route must call schedule_followup with the
    estimate_followup type after a successful customer delivery, gated
    on doc_type=='proposal'. We grep the source rather than spinning up
    Flask + mocking 12 modules.
    """
    from pathlib import Path
    src = (Path(__file__).parent.parent
           / "routes" / "document_routes.py").read_text()
    assert "schedule_followup" in src
    assert "estimate_followup" in src
    # And the call must be after the delivery_result success branch.
    # We assert ordering by string offsets — schedule_followup must
    # appear after delivery_result is set.
    delivery_idx = src.find("delivery_result")
    schedule_idx = src.find("schedule_followup")
    assert delivery_idx < schedule_idx, (
        "schedule_followup must be called AFTER customer delivery, "
        "not before — otherwise we'd queue a follow-up for a customer "
        "we never reached."
    )


def test_doc_send_route_writes_sent_at_with_status_sent():
    """
    The /doc/send route must write sent_at alongside status='sent'.
    The belt-and-suspenders followup queries depend on sent_at being
    present whenever status is 'sent'.
    """
    from pathlib import Path
    src = (Path(__file__).parent.parent
           / "routes" / "document_routes.py").read_text()
    # Both keys should appear together in the same update dict
    assert '"status": "sent"' in src
    assert '"sent_at": now' in src


# ---------------------------------------------------------------------------
# Belt-and-suspenders filter — followup cron queries must require
# sent_at IS NOT NULL.
# ---------------------------------------------------------------------------

def test_db_proposals_latest_sent_filters_on_sent_at_not_null():
    """
    get_latest_sent_proposal_for_customer must include the sent_at
    not-null filter so a draft accidentally marked 'sent' can't surface
    here.
    """
    import inspect
    from execution.db_proposals import get_latest_sent_proposal_for_customer
    src = inspect.getsource(get_latest_sent_proposal_for_customer)
    assert ".not_.is_(\"sent_at\", \"null\")" in src, (
        "get_latest_sent_proposal_for_customer must filter on "
        "sent_at IS NOT NULL — see April 2026 draft-leak bug fix."
    )


def test_followup_handle_lost_report_filters_on_sent_at_not_null():
    """
    handle_lost_report must include the same sent_at filter.
    """
    import inspect
    from execution.followup_agent import handle_lost_report
    src = inspect.getsource(handle_lost_report)
    assert ".not_.is_(\"sent_at\", \"null\")" in src, (
        "followup_agent.handle_lost_report must filter on "
        "sent_at IS NOT NULL — same bug fix as get_latest_sent_proposal."
    )


# ---------------------------------------------------------------------------
# get_pending_proposals + get_cold_proposals were already correct
# (they filter on .lt('sent_at', cutoff) which excludes NULL rows by
# definition). Lock that in so a future refactor doesn't loosen them.
# ---------------------------------------------------------------------------

def test_db_proposals_get_pending_filters_on_sent_at():
    import inspect
    from execution.db_proposals import get_pending_proposals
    src = inspect.getsource(get_pending_proposals)
    assert "sent_at" in src and ".lt(" in src, (
        "get_pending_proposals must filter on sent_at — without it the "
        "3-day cron would pick up freshly drafted proposals."
    )


def test_db_proposals_get_cold_filters_on_sent_at():
    import inspect
    from execution.db_proposals import get_cold_proposals
    src = inspect.getsource(get_cold_proposals)
    assert "sent_at" in src and ".lt(" in src, (
        "get_cold_proposals must filter on sent_at — without it the "
        "14-day cold cron would pick up freshly drafted proposals."
    )
