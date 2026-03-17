"""
cron.py — Railway cron job entry point for morning briefings

# RAILWAY CRON SETUP:
# In Railway dashboard → your service → Settings → Cron Schedule
# Set to: */30 * * * *   (runs every 30 minutes)
# This is safe to run frequently — timezone check prevents duplicate sends
# Command: python cron.py

How it works:
  - Fetches all active clients from Supabase
  - For each client, checks if it is currently 6:00am–6:59am in their timezone
  - If yes, sends the morning briefing via briefing_agent
  - If no, skips silently
  - One client failure never stops the loop
"""

import os
import sys
from datetime import datetime

import pytz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from execution.briefing_agent import send_morning_briefing
from execution.noshow_agent import check_noshows
from execution.db_connection import get_client as get_supabase

DEFAULT_TIMEZONE = "America/New_York"
BRIEFING_HOUR    = 6   # 6:00am–6:59am local time


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fetch_active_clients() -> list | None:
    """
    Return all active client records, or None on failure.
    None vs [] lets main() distinguish a DB error from a genuinely empty table.
    """
    try:
        supabase = get_supabase()
        result = (
            supabase.table("clients")
            .select("*")
            .eq("active", True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        print(f"[{timestamp()}] ERROR cron: Failed to fetch clients — {e}")
        return None


def _is_briefing_hour(client: dict) -> bool:
    """
    Return True if it is currently 6:00am–6:59am in the client's local timezone.
    Falls back to America/New_York on missing or invalid timezone.
    """
    tz_name = client.get("timezone") or DEFAULT_TIMEZONE
    try:
        tz = pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        tz = pytz.timezone(DEFAULT_TIMEZONE)

    local_hour = datetime.now(tz).hour
    return local_hour == BRIEFING_HOUR


def main():
    print(f"[{timestamp()}] INFO cron: Starting cron run")

    clients = _fetch_active_clients()
    if clients is None:
        print(f"[{timestamp()}] ERROR cron: Aborting — could not fetch clients")
        sys.exit(1)

    briefings_sent = 0
    briefings_skipped = 0

    for client in clients:
        # ── Morning briefing — only fires once, at 6am local time ──
        if _is_briefing_hour(client):
            try:
                result = send_morning_briefing(client)
                print(f"[{timestamp()}] INFO cron: {result}")
                briefings_sent += 1
            except Exception as e:
                print(
                    f"[{timestamp()}] ERROR cron: Briefing failed for "
                    f"{client.get('business_name', client.get('id'))} — {e}"
                )
        else:
            briefings_skipped += 1

        # ── No-show check — runs every tick, threshold enforced inside ──
        try:
            noshow_result = check_noshows(client)
            print(f"[{timestamp()}] INFO cron: {noshow_result}")
        except Exception as e:
            print(
                f"[{timestamp()}] ERROR cron: No-show check failed for "
                f"{client.get('business_name', client.get('id'))} — {e}"
            )

    print(
        f"[{timestamp()}] INFO cron: Finished — "
        f"{briefings_sent} briefings sent, {briefings_skipped} skipped"
    )


if __name__ == "__main__":
    main()
