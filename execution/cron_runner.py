"""
cron_runner.py — Standalone cron script for the Trades AI agent stack

Runs on a schedule (every 30 minutes via Railway cron or systemd timer).
On the first of each month, also sends the monthly closing rate report.

Tasks performed every run:
  1. Send due estimate follow-ups and payment chasers
  2. Check for cold proposals (14+ days silent) and handle them
  3. (1st of month only) Send monthly closing rate SMS to each client

Usage:
    python execution/cron_runner.py

Deploy as a Railway cron job:
    Schedule: */30 * * * *
    Command:  python execution/cron_runner.py
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_client import list_all_clients
from execution.followup_agent import run_scheduled_followups
from execution.reporting_agent import get_closing_rate_summary
from execution.sms_send import send_sms


def _timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _is_first_of_month() -> bool:
    return datetime.now(timezone.utc).day == 1


def _send_monthly_reports():
    """
    Send a monthly closing rate SMS to every active client.
    Called once on the first of each month.
    """
    clients = list_all_clients()
    print(f"[{_timestamp()}] INFO cron_runner: Sending monthly reports to {len(clients)} clients")

    for client in clients:
        client_id    = client.get("id")
        client_phone = client.get("phone")

        if not client_id or not client_phone:
            continue

        try:
            summary = get_closing_rate_summary(client_id, months=1)
            result  = send_sms(
                to_number=client_phone,
                message_body=summary,
                from_number=client_phone,
            )
            if result.get("success"):
                print(f"[{_timestamp()}] INFO cron_runner: Monthly report sent to {client_phone}")
            else:
                print(f"[{_timestamp()}] ERROR cron_runner: Monthly report failed for {client_phone} — {result.get('error')}")
        except Exception as e:
            print(f"[{_timestamp()}] ERROR cron_runner: monthly report exception for {client_id} — {e}")


def main():
    print(f"[{_timestamp()}] INFO cron_runner: Starting run")

    # --- Task 1 & 2: Scheduled follow-ups + cold proposal handling ---
    try:
        processed = run_scheduled_followups()
        print(f"[{_timestamp()}] INFO cron_runner: Processed {processed} follow-ups")
    except Exception as e:
        print(f"[{_timestamp()}] ERROR cron_runner: run_scheduled_followups failed — {e}")

    # --- Task 3: Monthly report (first of month only) ---
    if _is_first_of_month():
        print(f"[{_timestamp()}] INFO cron_runner: First of month — sending closing rate reports")
        try:
            _send_monthly_reports()
        except Exception as e:
            print(f"[{_timestamp()}] ERROR cron_runner: monthly reports failed — {e}")

    print(f"[{_timestamp()}] INFO cron_runner: Run complete")


if __name__ == "__main__":
    main()
