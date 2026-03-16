"""
db_connection.py — Shared Supabase client

Every db_*.py script imports get_client() from here.
One place to manage the connection — change credentials here only.

Usage:
    from execution.db_connection import get_client
    supabase = get_client()
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

# Module-level singleton — created once, reused across all calls
_client: Client | None = None


def get_client() -> Client:
    """
    Return the Supabase client, creating it on first call.
    Uses the service key (not anon key) — RLS is bypassed server-side.
    Raises RuntimeError if credentials are missing from .env.
    """
    global _client

    if _client is not None:
        return _client

    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError(
            "Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env"
        )

    _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _client
