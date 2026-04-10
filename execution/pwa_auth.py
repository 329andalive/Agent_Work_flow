"""
pwa_auth.py — Magic-link authentication for the PWA

Flow:
  1. Tech enters their phone number on /pwa/login
  2. create_magic_link() looks up the employee, generates a token,
     saves a row in pwa_tokens, returns the URL
  3. Caller sends the URL via the notify router (email-first per CLAUDE.md)
  4. Tech taps the link → /pwa/auth/<token>
  5. consume_magic_link() validates and burns the token, returns the
     employee record so the caller can set the session

Tokens:
  - 8 characters, alphanumeric
  - 15-minute expiry
  - One-shot (consumed_at marks them spent)
  - Stored in pwa_tokens table

Sessions set after consumption:
  session["client_id"]      — multi-tenant filter
  session["employee_id"]    — None for owner_mobile_fallback
  session["employee_name"]
  session["employee_role"]
  session["employee_phone"]
  session["pwa_authed"] = True
"""

import os
import sys
import string
import secrets
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase
from execution.db_employee import get_employee_by_phone


TOKEN_LENGTH = 8
TOKEN_CHARS = string.ascii_letters + string.digits
EXPIRY_MINUTES = 15
MAX_COLLISION_RETRIES = 5


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _generate_token() -> str:
    """Cryptographically random 8-char alphanumeric token."""
    return "".join(secrets.choice(TOKEN_CHARS) for _ in range(TOKEN_LENGTH))


def _normalize_phone(raw: str) -> str:
    """E.164 normalize."""
    import re
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if digits:
        return f"+{digits}"
    return ""


def create_magic_link(client_id: str, phone: str, base_url: str) -> dict:
    """
    Look up an employee by phone, create a magic-link token, return the URL.

    Args:
        client_id: UUID of the client (the business this employee belongs to)
        phone:     Employee's phone number (any format)
        base_url:  Base URL for building the link (e.g. https://app.bolts11.com)

    Returns:
        {
            "success": bool,
            "url": str or None,         # the magic link URL
            "employee": dict or None,   # the resolved employee
            "expires_at": str,          # ISO timestamp
            "error": str or None,
        }
    """
    normalized = _normalize_phone(phone)
    if not normalized:
        return {"success": False, "url": None, "employee": None, "error": "Invalid phone number"}

    employee = get_employee_by_phone(client_id, normalized)
    if not employee:
        return {
            "success": False,
            "url": None,
            "employee": None,
            "error": "No team member found for that phone number",
        }

    sb = get_supabase()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=EXPIRY_MINUTES)).isoformat()

    token = None
    for attempt in range(MAX_COLLISION_RETRIES):
        candidate = _generate_token()
        try:
            existing = sb.table("pwa_tokens").select("id").eq("token", candidate).execute()
            if existing.data:
                continue

            result = sb.table("pwa_tokens").insert({
                "token": candidate,
                "client_id": client_id,
                "tech_id": employee.get("id"),
                "employee_phone": normalized,
                "purpose": "pwa_login",
                "expires_at": expires_at,
            }).execute()

            if result.data:
                token = candidate
                break
        except Exception as e:
            print(f"[{_ts()}] WARN pwa_auth: token insert attempt {attempt+1} failed — {e}")
            continue

    if not token:
        return {"success": False, "url": None, "employee": employee, "error": "Could not generate token"}

    url = f"{base_url.rstrip('/')}/pwa/auth/{token}"
    print(f"[{_ts()}] INFO pwa_auth: Magic link created for {employee.get('name')} → {url}")

    return {
        "success": True,
        "url": url,
        "employee": employee,
        "expires_at": expires_at,
        "error": None,
    }


def consume_magic_link(token: str, request_ip: str = None, user_agent: str = None) -> dict:
    """
    Verify and burn a magic-link token. Returns the employee record so
    the caller can set the session.

    Args:
        token:       The 8-char token from the URL
        request_ip:  Optional client IP for audit log
        user_agent:  Optional user agent for audit log

    Returns:
        {
            "success": bool,
            "client_id": str or None,
            "employee_id": str or None,
            "employee_name": str or None,
            "employee_role": str or None,
            "employee_phone": str or None,
            "error": str or None,
        }
    """
    if not token:
        return {"success": False, "error": "Missing token"}

    try:
        sb = get_supabase()
        result = sb.table("pwa_tokens").select("*").eq("token", token).limit(1).execute()
    except Exception as e:
        print(f"[{_ts()}] ERROR pwa_auth: token lookup failed — {e}")
        return {"success": False, "error": "Lookup failed"}

    if not result.data:
        return {"success": False, "error": "Invalid or expired link"}

    row = result.data[0]

    # Check if already consumed
    if row.get("consumed_at"):
        return {"success": False, "error": "This link has already been used"}

    # Check expiry
    try:
        expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) >= expires:
            return {"success": False, "error": "This link has expired. Request a new one."}
    except Exception:
        return {"success": False, "error": "Invalid expiry on token"}

    # Burn the token
    try:
        sb.table("pwa_tokens").update({
            "consumed_at": datetime.now(timezone.utc).isoformat(),
            "consumed_ip": request_ip,
            "user_agent": (user_agent or "")[:500],
        }).eq("id", row["id"]).execute()
    except Exception as e:
        print(f"[{_ts()}] WARN pwa_auth: token burn failed — {e}")

    # Look up the employee record by ID, or fall back to phone (owner_mobile path)
    employee = None
    employee_id = row.get("tech_id")
    employee_phone = row.get("employee_phone", "")
    client_id = row.get("client_id")

    if employee_id:
        try:
            er = sb.table("employees").select("*").eq("id", employee_id).limit(1).execute()
            if er.data:
                employee = er.data[0]
        except Exception as e:
            print(f"[{_ts()}] WARN pwa_auth: employee lookup by id failed — {e}")

    if not employee:
        # Fallback: re-resolve via phone (will hit owner_mobile fallback if needed)
        employee = get_employee_by_phone(client_id, employee_phone) or {}

    print(f"[{_ts()}] INFO pwa_auth: Token consumed → {employee.get('name', 'unknown')} ({employee_phone})")

    return {
        "success": True,
        "client_id": client_id,
        "employee_id": employee.get("id"),
        "employee_name": employee.get("name", "Tech"),
        "employee_role": employee.get("role", "field_tech"),
        "employee_phone": employee_phone,
        "error": None,
    }


def find_client_by_phone(phone: str) -> str | None:
    """
    Find a client_id by checking if the given phone matches any employee's
    phone OR any client's owner_mobile/phone. Used by the login form when
    the tech doesn't yet know which business they belong to.

    Returns the first matching client_id, or None.
    """
    normalized = _normalize_phone(phone)
    if not normalized:
        return None

    try:
        sb = get_supabase()

        # Check employees first
        emp = sb.table("employees").select("client_id").eq(
            "phone", normalized
        ).eq("active", True).limit(1).execute()
        if emp.data:
            return emp.data[0].get("client_id")

        # Check clients owner_mobile
        cli = sb.table("clients").select("id").eq(
            "owner_mobile", normalized
        ).limit(1).execute()
        if cli.data:
            return cli.data[0].get("id")

        # Check clients phone
        cli2 = sb.table("clients").select("id").eq(
            "phone", normalized
        ).limit(1).execute()
        if cli2.data:
            return cli2.data[0].get("id")

    except Exception as e:
        print(f"[{_ts()}] WARN pwa_auth: find_client_by_phone failed — {e}")

    return None
