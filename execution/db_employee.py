"""
db_employee.py — Employee table queries

An "employee" is a field tech, foreman, or office staff member who texts into
the system. Every inbound SMS from a non-owner number is matched against the
employees table to determine identity and role before routing.

Usage:
    from execution.db_employee import get_employee_by_phone
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_employee_by_phone(client_id: str, phone: str) -> dict | None:
    """
    Look up an active employee by client_id and phone number.

    Args:
        client_id: UUID of the client (business) this employee belongs to
        phone:     E.164 phone number (e.g. "+12075550123")

    Returns:
        Full employee row as a dict, or None if not found / on error.
    """
    try:
        supabase = get_client()
        result = (
            supabase.table("employees")
            .select("*")
            .eq("client_id", client_id)
            .eq("phone", phone)
            .eq("active", True)
            .single()
            .execute()
        )
        if result.data:
            print(
                f"[{timestamp()}] INFO db_employee: Found employee → "
                f"{result.data['name']} role={result.data['role']}"
            )
        return result.data

    except Exception as e:
        # .single() raises when no row is found — treat as not found, not a crash
        print(f"[{timestamp()}] INFO db_employee: No employee found for client={client_id} phone={phone} ({e})")
        return None
