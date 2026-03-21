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

from execution.briefing_agent import send_morning_briefing
from execution.db_client import list_all_clients
from execution.db_clarification import get_expired_approvals, update_approval_status
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


def _is_morning_hour() -> bool:
    """True between 06:00 and 09:59 UTC — the window for morning briefings."""
    return 6 <= datetime.now(timezone.utc).hour < 10


def _send_morning_briefings():
    """Send the daily job briefing to foremans and owners for every active client."""
    clients = list_all_clients()
    print(f"[{_timestamp()}] INFO cron_runner: Sending morning briefings to {len(clients)} clients")
    for client in clients:
        try:
            result = send_morning_briefing(client)
            print(f"[{_timestamp()}] INFO cron_runner: Briefing — {result}")
        except Exception as e:
            print(f"[{_timestamp()}] ERROR cron_runner: briefing failed for {client.get('id')} — {e}")


def _check_expired_approvals():
    """
    Check for expired customer approvals and send follow-up messages.

    Follow-up 1: Sent ~2 hours after original estimate (first cron after expiry)
    Follow-up 2: Sent ~24 hours after original estimate
    """
    approvals = get_expired_approvals()
    if not approvals:
        return

    print(f"[{_timestamp()}] INFO cron_runner: Processing {len(approvals)} expired approvals")

    for approval in approvals:
        try:
            approval_id = approval["id"]
            client_id = approval.get("client_id")
            customer_phone = approval.get("customer_phone", "")
            estimate_amount = float(approval.get("estimate_amount", 0))
            sent_at_str = approval.get("sent_at", "")
            followup_1 = approval.get("followup_1_sent_at")
            followup_2 = approval.get("followup_2_sent_at")

            if not customer_phone:
                update_approval_status(approval_id, "expired")
                continue

            # Load client for business name and phone
            client = None
            try:
                from execution.db_connection import get_client as _get_sb
                sb = _get_sb()
                c_result = sb.table("clients").select("*").eq("id", client_id).execute()
                if c_result.data:
                    client = c_result.data[0]
            except Exception:
                pass

            biz_name = client.get("business_name", "Your service provider") if client else "Your service provider"
            client_phone = client.get("phone", "") if client else ""

            # HARD RULE #2 — check SMS opt-in before follow-up
            try:
                customer_id = approval.get("customer_id")
                if customer_id:
                    sb = _get_sb()
                    cust_result = sb.table("customers").select("sms_consent").eq("id", customer_id).execute()
                    if cust_result.data and not cust_result.data[0].get("sms_consent"):
                        print(f"[{_timestamp()}] WARNING cron_runner: Skipping follow-up — customer not opted in | approval={approval_id}")
                        update_approval_status(approval_id, "expired")
                        continue
            except Exception:
                pass  # If we can't check, proceed with follow-up

            if followup_1 is None:
                # Send follow-up 1
                msg = (
                    f"Hi, this is {biz_name}. Our tech visited today "
                    f"and left an estimate for additional work — ${estimate_amount:.2f}. "
                    f"Still interested? Reply YES to schedule."
                )
                send_sms(to_number=customer_phone, message_body=msg, from_number=client_phone)
                update_approval_status(approval_id, "pending", field="followup_1")
                print(f"[{_timestamp()}] INFO cron_runner: Follow-up 1 sent for approval {approval_id}")

            elif followup_2 is None:
                # Check if 24+ hours since sent_at before sending follow-up 2
                try:
                    sent_at = datetime.fromisoformat(sent_at_str.replace("Z", "+00:00"))
                    hours_since = (datetime.now(timezone.utc) - sent_at).total_seconds() / 3600
                    if hours_since < 24:
                        continue  # Too early for follow-up 2
                except (ValueError, TypeError):
                    pass  # Can't parse — send anyway

                msg = (
                    f"Last follow-up from {biz_name} on the estimate "
                    f"for ${estimate_amount:.2f}. Reply YES to schedule or STOP to opt out."
                )
                send_sms(to_number=customer_phone, message_body=msg, from_number=client_phone)
                update_approval_status(approval_id, "expired", field="followup_2")
                print(f"[{_timestamp()}] INFO cron_runner: Follow-up 2 sent for approval {approval_id}")

            else:
                # Both follow-ups sent — mark as expired and move on
                update_approval_status(approval_id, "expired")
                print(f"[{_timestamp()}] INFO cron_runner: Approval {approval_id} fully expired")

        except Exception as e:
            print(f"[{_timestamp()}] ERROR cron_runner: expired approval processing failed — {e}")


def main():
    print(f"[{_timestamp()}] INFO cron_runner: Starting run")

    # --- Task 1: Morning briefing (06:00–09:59 UTC only) ---
    if _is_morning_hour():
        print(f"[{_timestamp()}] INFO cron_runner: Morning window — sending briefings")
        try:
            _send_morning_briefings()
        except Exception as e:
            print(f"[{_timestamp()}] ERROR cron_runner: morning briefings failed — {e}")

    # --- Task 2 & 3: Scheduled follow-ups + cold proposal handling ---
    try:
        processed = run_scheduled_followups()
        print(f"[{_timestamp()}] INFO cron_runner: Processed {processed} follow-ups")
    except Exception as e:
        print(f"[{_timestamp()}] ERROR cron_runner: run_scheduled_followups failed — {e}")

    # --- Task 4: Check expired customer approvals ---
    try:
        _check_expired_approvals()
    except Exception as e:
        print(f"[{_timestamp()}] ERROR cron_runner: expired approvals check failed — {e}")

    # --- Task 5: Monthly report (first of month only) ---
    if _is_first_of_month():
        print(f"[{_timestamp()}] INFO cron_runner: First of month — sending closing rate reports")
        try:
            _send_monthly_reports()
        except Exception as e:
            print(f"[{_timestamp()}] ERROR cron_runner: monthly reports failed — {e}")

    print(f"[{_timestamp()}] INFO cron_runner: Run complete")


if __name__ == "__main__":
    main()
