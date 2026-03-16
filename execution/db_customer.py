"""
db_customer.py — Customer table queries

A "customer" is the septic company's end customer (homeowner, farmer, etc.).
Each customer belongs to one client. Customers are looked up by phone number
when they text in, and created on first contact if they don't exist yet.

Usage:
    from execution.db_customer import get_customer_by_phone, create_customer
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_customer_by_phone(client_id: str, phone: str) -> dict | None:
    """
    Find a customer by their phone number within a specific client's customer base.
    Called when a customer texts in to check if we already know them.

    Args:
        client_id: UUID of the client (business owner)
        phone:     Customer's E.164 phone number

    Returns:
        Customer record as a dict, or None if not found / on error.
    """
    try:
        supabase = get_client()
        result = (
            supabase.table("customers")
            .select("*")
            .eq("client_id", client_id)
            .eq("customer_phone", phone)
            .single()
            .execute()
        )
        if result.data:
            print(f"[{timestamp()}] INFO db_customer: Found customer → {result.data['customer_name']}")
        return result.data

    except Exception as e:
        print(f"[{timestamp()}] INFO db_customer: No customer found for phone={phone} ({e})")
        return None


def create_customer(
    client_id: str,
    name: str,
    phone: str,
    address: str = None,
    email: str = None,
) -> str | None:
    """
    Create a new customer record under a client.
    Called on first contact when a customer isn't in the system yet.

    Args:
        client_id: UUID of the client (business owner)
        name:      Customer's full name
        phone:     Customer's E.164 phone number
        address:   Property address (optional)
        email:     Customer email (optional)

    Returns:
        New customer UUID as a string, or None on failure.
    """
    try:
        supabase = get_client()
        record = {
            "client_id":        client_id,
            "customer_name":    name,
            "customer_phone":   phone,
            "customer_address": address,
            "customer_email":   email,
        }
        result = supabase.table("customers").insert(record).execute()
        customer_id = result.data[0]["id"]
        print(f"[{timestamp()}] INFO db_customer: Created customer id={customer_id} name={name}")
        return customer_id

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_customer: create_customer failed — {e}")
        return None


def update_customer_notes(customer_id: str, notes: str) -> bool:
    """
    Update the property notes for a customer.
    Used to record tank size, last pump date, access info discovered during jobs.

    Args:
        customer_id: UUID of the customer
        notes:       New property notes text (replaces existing)

    Returns:
        True on success, False on failure.
    """
    try:
        supabase = get_client()
        supabase.table("customers").update(
            {"property_notes": notes}
        ).eq("id", customer_id).execute()
        print(f"[{timestamp()}] INFO db_customer: Updated notes for customer_id={customer_id}")
        return True

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_customer: update_customer_notes failed — {e}")
        return False
