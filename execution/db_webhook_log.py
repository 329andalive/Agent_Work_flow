"""
db_webhook_log.py — Persist raw Telnyx webhook payloads before any processing

Implements CLAUDE.md Rule #5:
  "Save the raw inbound webhook payload to the database
   BEFORE any processing begins. This is the first line
   of every webhook handler, no exceptions."

Also drives webhook deduplication: if a message_id already exists
in this table we know Telnyx is retrying and we return 200 early.

Required Supabase table (run once in SQL editor):
  CREATE TABLE IF NOT EXISTS webhook_log (
      id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      received_at timestamptz DEFAULT now(),
      raw_payload jsonb        NOT NULL,
      message_id  text,
      processed   boolean      NOT NULL DEFAULT false,
      error       text
  );

  CREATE INDEX IF NOT EXISTS idx_webhook_log_message_id
      ON webhook_log (message_id)
      WHERE message_id IS NOT NULL;
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_duplicate(message_id: str) -> bool:
    """
    Return True if this message_id has already been saved to webhook_log.
    Telnyx retries deliver the same message_id — returning 200 early stops
    the retry loop without processing the message twice.

    On any DB error: returns False (fail open — better to process twice
    than to silently drop a message).
    """
    if not message_id:
        return False
    try:
        supabase = get_supabase()
        result = (
            supabase.table("webhook_log")
            .select("id")
            .eq("message_id", message_id)
            .limit(1)
            .execute()
        )
        found = bool(result.data)
        if found:
            print(f"[{_ts()}] INFO db_webhook_log: Duplicate message_id={message_id}")
        return found
    except Exception as e:
        print(f"[{_ts()}] WARN db_webhook_log: Duplicate check failed — {e}. Failing open.")
        return False


def save_webhook(raw_payload: dict, message_id: str | None) -> str | None:
    """
    Persist the raw Telnyx JSON payload to webhook_log.
    Must be called BEFORE any other processing.

    Args:
        raw_payload: The full JSON dict received from Telnyx
        message_id:  Telnyx message ID extracted from the payload (may be None
                     for non-SMS events like message.sent / message.finalized)

    Returns:
        The new log record's UUID string, or None on failure.
    """
    try:
        supabase = get_supabase()
        row = {"raw_payload": raw_payload}
        if message_id:
            row["message_id"] = message_id
        result = supabase.table("webhook_log").insert(row).execute()
        log_id = (result.data or [{}])[0].get("id")
        print(f"[{_ts()}] INFO db_webhook_log: Saved raw payload log_id={log_id} message_id={message_id}")
        return log_id
    except Exception as e:
        print(f"[{_ts()}] ERROR db_webhook_log: save_webhook failed — {e}")
        return None


def mark_processed(log_id: str | None) -> None:
    """Mark a webhook_log row as successfully processed."""
    if not log_id:
        return
    try:
        get_supabase().table("webhook_log").update({"processed": True}).eq("id", log_id).execute()
    except Exception as e:
        print(f"[{_ts()}] WARN db_webhook_log: mark_processed failed for {log_id} — {e}")


def mark_error(log_id: str | None, error_msg: str) -> None:
    """Stamp an error message onto a webhook_log row."""
    if not log_id:
        return
    try:
        get_supabase().table("webhook_log").update({
            "processed": False,
            "error": error_msg[:2000],   # guard against huge tracebacks
        }).eq("id", log_id).execute()
    except Exception as e:
        print(f"[{_ts()}] WARN db_webhook_log: mark_error failed for {log_id} — {e}")
