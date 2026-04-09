"""
pwa_chat_messages.py — DB operations for PWA AI chat persistence

Three operations:
  - get_active_session_id(employee_id) → uuid for the current conversation
  - get_history(session_id, limit=20) → ordered messages
  - save_message(client_id, employee_id, session_id, role, content, metadata)

Session model:
  An employee's "active session" is the most recent session_id they've
  used. There's no explicit start_new_session() in 6a — the session
  carries forward across page loads. 6b can add a "new conversation"
  button that calls a wrapper to mint a fresh uuid.
"""

import os
import sys
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase

DEFAULT_HISTORY_LIMIT = 20


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_active_session_id(employee_id: str) -> str:
    """
    Return the session_id of the employee's most recent message, or
    a fresh uuid if they have no chat history yet.
    """
    try:
        sb = get_supabase()
        result = sb.table("pwa_chat_messages").select("session_id").eq(
            "employee_id", employee_id
        ).order("created_at", desc=True).limit(1).execute()
        if result.data:
            return result.data[0]["session_id"]
    except Exception as e:
        print(f"[{_ts()}] WARN pwa_chat_messages: get_active_session_id failed — {e}")
    return str(uuid.uuid4())


def get_history(session_id: str, employee_id: str, limit: int = DEFAULT_HISTORY_LIMIT) -> list:
    """
    Return the last `limit` messages for a session in chronological order.

    Both session_id and employee_id are required for tenant safety —
    we never trust the session_id alone.
    """
    try:
        sb = get_supabase()
        result = sb.table("pwa_chat_messages").select(
            "id, role, content, metadata, created_at"
        ).eq("session_id", session_id).eq(
            "employee_id", employee_id
        ).order("created_at", desc=True).limit(limit).execute()

        rows = result.data or []
        # Reverse so oldest is first (we fetched DESC for the limit)
        rows.reverse()
        return rows
    except Exception as e:
        print(f"[{_ts()}] WARN pwa_chat_messages: get_history failed — {e}")
        return []


def save_message(
    client_id: str,
    employee_id: str,
    session_id: str,
    role: str,
    content: str,
    metadata: dict = None,
) -> str | None:
    """
    Save a single message turn. Returns the new row id or None on failure.

    Args:
        client_id:   tenant identifier
        employee_id: who sent or received the message
        session_id:  conversation boundary
        role:        'user' or 'assistant'
        content:     message text
        metadata:    optional jsonb (action chips, model info, etc.)
    """
    if role not in ("user", "assistant"):
        print(f"[{_ts()}] WARN pwa_chat_messages: invalid role '{role}'")
        return None
    if not content or not content.strip():
        return None

    try:
        sb = get_supabase()
        result = sb.table("pwa_chat_messages").insert({
            "client_id": client_id,
            "employee_id": employee_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }).execute()
        if result.data:
            return result.data[0]["id"]
    except Exception as e:
        print(f"[{_ts()}] WARN pwa_chat_messages: save_message failed — {e}")
    return None
