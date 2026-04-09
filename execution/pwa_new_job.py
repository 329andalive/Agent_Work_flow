"""
pwa_new_job.py — PWA-facing wrapper for creating proposals from tech input

The tech enters a job description (and optional customer details) on the
PWA's New Job screen. This module:
  1. Resolves or creates the customer record
  2. Calls proposal_agent.run() to generate the proposal via Claude
  3. Fetches the proposal_id + edit_token + review URL
  4. Returns everything to the PWA so it can navigate the tech to the
     review screen for approval before sending to the customer

The DB writes are identical to the SMS path (same customer creation,
same proposal_agent.run() call, same notify routing). Only the
return shape differs — JSON with the review URL instead of an SMS reply.
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase
from execution.db_customer import get_customer_by_phone, create_customer


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_client_by_id(client_id: str) -> dict | None:
    """Look up a client (business) record by UUID."""
    try:
        sb = get_supabase()
        result = sb.table("clients").select("*").eq("id", client_id).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[{_ts()}] WARN pwa_new_job: client lookup failed — {e}")
        return None


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


def _resolve_or_create_customer(client_id: str, name: str, phone: str, address: str, email: str) -> dict:
    """
    Find an existing customer or create a new one. Used before calling
    proposal_agent.run() so the proposal lands on a real customer record.

    Returns:
        {
            "success": bool,
            "customer_id": str or None,
            "customer_phone": str,  # normalized E.164
            "created": bool,
            "error": str or None,
        }
    """
    name = (name or "").strip()
    phone = _normalize_phone(phone)
    address = (address or "").strip()
    email = (email or "").strip()

    # Phone is required by HARD RULE #1 for new customers
    if not phone:
        # Try to find an existing customer by name only
        if name:
            try:
                sb = get_supabase()
                # Try last-name fuzzy match first (most distinctive)
                parts = name.split()
                last = parts[-1] if parts else name
                result = sb.table("customers").select(
                    "id, customer_name, customer_phone"
                ).eq("client_id", client_id).ilike(
                    "customer_name", f"%{last}%"
                ).limit(5).execute()

                if result.data:
                    # Prefer one whose first name also matches
                    first = parts[0].lower() if parts else ""
                    for c in result.data:
                        if first and first in (c.get("customer_name") or "").lower():
                            return {
                                "success": True,
                                "customer_id": c["id"],
                                "customer_phone": c.get("customer_phone", ""),
                                "created": False,
                                "error": None,
                            }
                    # Single result — use it
                    if len(result.data) == 1:
                        c = result.data[0]
                        return {
                            "success": True,
                            "customer_id": c["id"],
                            "customer_phone": c.get("customer_phone", ""),
                            "created": False,
                            "error": None,
                        }
            except Exception as e:
                print(f"[{_ts()}] WARN pwa_new_job: name search failed — {e}")

        return {
            "success": False,
            "customer_id": None,
            "customer_phone": "",
            "created": False,
            "error": "Customer phone required for new customers (HARD RULE #1)",
        }

    # Look up by phone first
    try:
        existing = get_customer_by_phone(client_id, phone)
        if existing:
            return {
                "success": True,
                "customer_id": existing["id"],
                "customer_phone": phone,
                "created": False,
                "error": None,
            }
    except Exception as e:
        print(f"[{_ts()}] WARN pwa_new_job: customer lookup failed — {e}")

    # Create new customer
    try:
        customer_id = create_customer(
            client_id=client_id,
            name=name or "Customer",
            phone=phone,
            address=address or None,
            email=email or None,
        )
        if not customer_id:
            return {
                "success": False,
                "customer_id": None,
                "customer_phone": phone,
                "created": False,
                "error": "Could not create customer record",
            }
        return {
            "success": True,
            "customer_id": customer_id,
            "customer_phone": phone,
            "created": True,
            "error": None,
        }
    except ValueError as ve:
        return {"success": False, "customer_id": None, "customer_phone": phone, "created": False, "error": str(ve)}
    except Exception as e:
        print(f"[{_ts()}] ERROR pwa_new_job: create_customer failed — {e}")
        return {"success": False, "customer_id": None, "customer_phone": phone, "created": False, "error": "Database error"}


def create_proposal_from_pwa(
    client_id: str,
    employee_id: str,
    raw_input: str,
    customer_name: str = "",
    customer_phone: str = "",
    customer_address: str = "",
    customer_email: str = "",
) -> dict:
    """
    Main entry point. Called from POST /pwa/api/job/new.

    Args:
        client_id:        UUID of the client (business)
        employee_id:      UUID of the tech submitting the job
        raw_input:        Free-form job description (e.g. "pump out 1000gal $325")
        customer_name:    Optional — if existing customer, this can be empty
        customer_phone:   Optional — required if creating a new customer
        customer_address: Optional
        customer_email:   Optional

    Returns:
        {
            "success": bool,
            "proposal_id": str or None,
            "review_url": str or None,
            "amount": float or None,
            "customer_id": str or None,
            "customer_name": str or None,
            "customer_created": bool,
            "error": str or None,
        }
    """
    if not raw_input or not raw_input.strip():
        return {"success": False, "error": "Job description required"}

    # 1. Load the client record (we need client_phone for proposal_agent.run)
    client = _get_client_by_id(client_id) if client_id else None
    if not client:
        return {"success": False, "error": "Client not found"}

    client_phone = client.get("phone", "")
    if not client_phone:
        return {"success": False, "error": "Client has no phone configured"}

    # 2. Resolve or create the customer
    cust_result = _resolve_or_create_customer(
        client_id=client_id,
        name=customer_name,
        phone=customer_phone,
        address=customer_address,
        email=customer_email,
    )
    if not cust_result["success"]:
        return {"success": False, "error": cust_result["error"]}

    customer_id = cust_result["customer_id"]
    customer_phone_normalized = cust_result["customer_phone"]

    # 3. Call the proposal agent — it does customer lookup again internally,
    #    but it's idempotent: it'll find the customer we just resolved/created
    try:
        from execution.proposal_agent import run as proposal_run
        proposal_text = proposal_run(
            client_phone=client_phone,
            customer_phone=customer_phone_normalized,
            raw_input=raw_input.strip(),
        )
    except Exception as e:
        print(f"[{_ts()}] ERROR pwa_new_job: proposal_agent.run() failed — {e}")
        return {"success": False, "error": "Could not generate proposal"}

    if not proposal_text:
        return {"success": False, "error": "Proposal generation failed"}

    # 4. Look up the proposal we just created so we can return the review URL
    try:
        sb = get_supabase()
        result = sb.table("proposals").select(
            "id, edit_token, amount_estimate, customer_id"
        ).eq("client_id", client_id).eq(
            "customer_id", customer_id
        ).order("created_at", desc=True).limit(1).execute()

        if not result.data:
            return {
                "success": False,
                "error": "Proposal generated but not found in database",
            }

        prop = result.data[0]
        proposal_id = prop["id"]
        edit_token = prop.get("edit_token")
        amount = float(prop.get("amount_estimate") or 0)
    except Exception as e:
        print(f"[{_ts()}] ERROR pwa_new_job: proposal lookup failed — {e}")
        return {"success": False, "error": "Could not retrieve created proposal"}

    # 5. Build the review URL
    review_url = None
    if edit_token:
        base_url = os.environ.get("BOLTS11_BASE_URL", "https://app.bolts11.com")
        review_url = f"{base_url.rstrip('/')}/doc/review/{edit_token}?type=proposal"

    print(f"[{_ts()}] INFO pwa_new_job: Created proposal {proposal_id[:8]} amount=${amount:.2f}")

    return {
        "success": True,
        "proposal_id": proposal_id,
        "review_url": review_url,
        "amount": amount,
        "customer_id": customer_id,
        "customer_name": customer_name or "Customer",
        "customer_created": cust_result["created"],
        "error": None,
    }
