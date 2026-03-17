"""
db_noshow.py — No-show alert table queries

Usage:
    from execution.db_noshow import has_open_noshow_alert
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def has_open_noshow_alert(client_id: str) -> bool:
    """
    Return True if any open noshow_alert exists for this client.
    Returns False on any error — fail safe prevents accidental noshow routing.

    Args:
        client_id: UUID of the client to check

    Returns:
        True if at least one open alert exists, False otherwise.
    """
    try:
        supabase = get_client()
        result = (
            supabase.table("noshow_alerts")
            .select("id")
            .eq("client_id", client_id)
            .eq("status", "open")
            .limit(1)
            .execute()
        )
        found = bool(result.data)
        if found:
            print(f"[{timestamp()}] INFO db_noshow: Open alert found for client={client_id}")
        return found

    except Exception as e:
        print(f"[{timestamp()}] WARN db_noshow: Alert check failed for client={client_id} — {e}")
        return False
