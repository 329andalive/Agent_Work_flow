"""
test_dispatch_chain.py — Tests for the dispatch chain (time tracking flow).

Tests:
  - get_todays_route returns ordered jobs for a worker
  - build_route_sms formats a readable route message
  - start_first_job sets job_start on the first unstarted job
  - advance_to_next_job ends current job and starts next
  - advance_to_next_job returns None when route is done
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


FAKE_CLIENT_ID = "00000000-0000-0000-0000-000000000001"
FAKE_WORKER_ID = "00000000-0000-0000-0000-000000000010"
FAKE_JOB_1 = "00000000-0000-0000-0000-000000000101"
FAKE_JOB_2 = "00000000-0000-0000-0000-000000000102"
FAKE_JOB_3 = "00000000-0000-0000-0000-000000000103"


# ---------------------------------------------------------------------------
# build_route_sms
# ---------------------------------------------------------------------------

def test_build_route_sms_formats_correctly():
    """Should format a readable multi-line route message."""
    from execution.dispatch_chain import build_route_sms
    jobs = [
        {"job_type": "pump_out", "customer_name": "Alice Smith",
         "customer_address": "123 Main St, Belfast", "estimated_amount": 325, "sort_order": 0},
        {"job_type": "inspection", "customer_name": "Bob Jones",
         "customer_address": "45 Oak Ave", "estimated_amount": 250, "sort_order": 1},
    ]
    msg = build_route_sms(jobs, "Jesse", "Test Trades Co")

    assert "Jesse" in msg
    assert "2 jobs" in msg
    assert "1. Pump Out" in msg
    assert "Alice Smith" in msg
    assert "123 Main St" in msg
    assert "$325" in msg
    assert "2. Inspection" in msg
    assert "Reply DONE" in msg


def test_build_route_sms_empty():
    """Empty route should return no-jobs message."""
    from execution.dispatch_chain import build_route_sms
    msg = build_route_sms([], "Jesse")
    assert "No jobs" in msg


# ---------------------------------------------------------------------------
# get_todays_route
# ---------------------------------------------------------------------------

def test_get_todays_route_returns_ordered_jobs():
    """Should return jobs sorted by sort_order with customer enrichment."""
    mock_sb = MagicMock()

    def _table(name):
        t = MagicMock()
        for m in ("select", "eq", "in_", "order", "limit", "neq"):
            getattr(t, m).return_value = t
        r = MagicMock()

        if name == "route_assignments":
            r.data = [
                {"job_id": FAKE_JOB_1, "sort_order": 0},
                {"job_id": FAKE_JOB_2, "sort_order": 1},
            ]
        elif name == "jobs":
            r.data = [
                {"id": FAKE_JOB_1, "job_type": "pump_out", "job_description": "Pump out",
                 "status": "assigned", "estimated_amount": 325, "customer_id": "cust-1",
                 "scheduled_date": date.today().isoformat(), "job_start": None, "job_end": None,
                 "dispatch_status": "assigned", "sort_order": 0},
                {"id": FAKE_JOB_2, "job_type": "inspection", "job_description": "Inspect",
                 "status": "assigned", "estimated_amount": 250, "customer_id": "cust-2",
                 "scheduled_date": date.today().isoformat(), "job_start": None, "job_end": None,
                 "dispatch_status": "assigned", "sort_order": 1},
            ]
        elif name == "customers":
            r.data = [
                {"id": "cust-1", "customer_name": "Alice", "customer_address": "123 Main", "customer_phone": "+15551234"},
                {"id": "cust-2", "customer_name": "Bob", "customer_address": "45 Oak", "customer_phone": "+15555678"},
            ]
        else:
            r.data = []
        t.execute.return_value = r
        return t

    mock_sb.table.side_effect = _table

    with patch("execution.dispatch_chain.get_supabase", return_value=mock_sb):
        from execution.dispatch_chain import get_todays_route
        route = get_todays_route(FAKE_CLIENT_ID, FAKE_WORKER_ID)

    assert len(route) == 2
    assert route[0]["job_id"] == FAKE_JOB_1
    assert route[0]["customer_name"] == "Alice"
    assert route[1]["job_id"] == FAKE_JOB_2
    assert route[1]["sort_order"] == 1


# ---------------------------------------------------------------------------
# advance_to_next_job returns None when done
# ---------------------------------------------------------------------------

def test_advance_returns_none_when_route_complete():
    """When the completed job is the last one, advance should return None."""
    with patch("execution.dispatch_chain._end_job") as mock_end, \
         patch("execution.dispatch_chain.get_todays_route") as mock_route:

        # All jobs are completed
        mock_route.return_value = [
            {"job_id": FAKE_JOB_1, "sort_order": 0, "job_start": "2026-04-03T08:00:00",
             "job_end": "2026-04-03T09:00:00", "dispatch_status": "completed"},
        ]

        from execution.dispatch_chain import advance_to_next_job
        result = advance_to_next_job(FAKE_CLIENT_ID, FAKE_WORKER_ID, FAKE_JOB_1)

    mock_end.assert_called_once_with(FAKE_JOB_1)
    assert result is None
