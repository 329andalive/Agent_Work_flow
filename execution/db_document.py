"""
db_document.py — DB operations for the document edit/learn system

Handles lookups, updates, edit logging, and prompt override management
for both proposals and invoices.

Usage:
    from execution.db_document import get_document_by_token, update_proposal_fields
"""

import os
import sys
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Document lookups
# ---------------------------------------------------------------------------

def get_proposal_by_token(edit_token: str) -> dict | None:
    """Look up a proposal by its edit_token UUID."""
    try:
        supabase = get_supabase()
        result = (
            supabase.table("proposals")
            .select("*")
            .eq("edit_token", edit_token)
            .execute()
        )
        if result.data:
            print(f"[{timestamp()}] INFO db_document: Found proposal by edit_token={edit_token[:8]}...")
            return result.data[0]
        return None
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_document: get_proposal_by_token failed — {e}")
        return None


def get_invoice_by_token(edit_token: str) -> dict | None:
    """Look up an invoice by its edit_token UUID."""
    try:
        supabase = get_supabase()
        result = (
            supabase.table("invoices")
            .select("*")
            .eq("edit_token", edit_token)
            .execute()
        )
        if result.data:
            print(f"[{timestamp()}] INFO db_document: Found invoice by edit_token={edit_token[:8]}...")
            return result.data[0]
        return None
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_document: get_invoice_by_token failed — {e}")
        return None


def get_document_by_token(edit_token: str, doc_type: str) -> dict | None:
    """Route to proposal or invoice lookup based on doc_type."""
    if doc_type == "proposal":
        return get_proposal_by_token(edit_token)
    elif doc_type == "invoice":
        return get_invoice_by_token(edit_token)
    else:
        print(f"[{timestamp()}] ERROR db_document: Unknown doc_type '{doc_type}'")
        return None


# ---------------------------------------------------------------------------
# Document updates
# ---------------------------------------------------------------------------

def update_proposal_fields(
    proposal_id: str,
    line_items: list,
    subtotal: float,
    tax_rate: float,
    tax_amount: float,
    total: float,
    notes: str,
    html_url: str = None,
) -> bool:
    """Update proposal with structured line items and the computed total.

    The proposals table has only one money column (amount_estimate). The
    subtotal/tax_rate/tax_amount params are kept on the signature for
    backwards compatibility with existing callers but are NOT written to
    the DB — those columns don't exist on the proposals table.
    """
    # Touch the unused params explicitly so linters don't flag them and
    # so it's obvious to readers that they're intentionally dropped.
    _ = (subtotal, tax_rate, tax_amount)

    try:
        supabase = get_supabase()
        update = {
            "amount_estimate": total,
            "line_items": line_items,
            "proposal_text": notes,
        }
        if html_url:
            update["html_url"] = html_url

        supabase.table("proposals").update(update).eq("id", proposal_id).execute()
        print(f"[{timestamp()}] INFO db_document: Updated proposal {proposal_id} total=${total}")
        return True
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_document: update_proposal_fields failed — {e}")
        return False


def update_invoice_fields(
    invoice_id: str,
    line_items: list,
    subtotal: float,
    tax_rate: float,
    tax_amount: float,
    total: float,
    notes: str,
    html_url: str = None,
) -> bool:
    """Update invoice with structured line items and computed totals."""
    try:
        supabase = get_supabase()
        update = {
            "amount_due": total,
            "line_items": line_items,
            "subtotal": subtotal,
            "tax_rate": tax_rate,
            "tax_amount": tax_amount,
            "invoice_text": notes,
        }
        if html_url:
            update["html_url"] = html_url

        supabase.table("invoices").update(update).eq("id", invoice_id).execute()
        print(f"[{timestamp()}] INFO db_document: Updated invoice {invoice_id} total=${total}")
        return True
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_document: update_invoice_fields failed — {e}")
        return False


# ---------------------------------------------------------------------------
# Edit logging
# ---------------------------------------------------------------------------

def log_edit(
    document_type: str,
    document_id: str,
    client_id: str,
    field_changed: str,
    original_value: str,
    new_value: str,
) -> None:
    """Insert a row into estimate_edits to track what the owner changed."""
    try:
        supabase = get_supabase()
        supabase.table("estimate_edits").insert({
            "document_type": document_type,
            "document_id": document_id,
            "client_id": client_id,
            "field_changed": field_changed,
            "original_value": str(original_value)[:2000],
            "new_value": str(new_value)[:2000],
        }).execute()
        print(f"[{timestamp()}] INFO db_document: Logged edit — {document_type}/{field_changed} for client {client_id[:8]}...")
    except Exception as e:
        print(f"[{timestamp()}] WARN db_document: log_edit failed — {e}")


def get_recent_edits(client_id: str, doc_type: str, limit: int = 10) -> list:
    """Get recent edits for a client to feed the learning loop."""
    try:
        supabase = get_supabase()
        result = (
            supabase.table("estimate_edits")
            .select("*")
            .eq("client_id", client_id)
            .eq("document_type", doc_type)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_document: get_recent_edits failed — {e}")
        return []


# ---------------------------------------------------------------------------
# Prompt overrides (learning loop)
# ---------------------------------------------------------------------------

def get_prompt_override(client_id: str) -> dict | None:
    """Get the client's prompt override preferences."""
    try:
        supabase = get_supabase()
        result = (
            supabase.table("client_prompt_overrides")
            .select("*")
            .eq("client_id", client_id)
            .execute()
        )
        if result.data:
            return result.data[0]
        return None
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_document: get_prompt_override failed — {e}")
        return None


def upsert_prompt_override(client_id: str, doc_type: str, style_notes: str) -> bool:
    """
    Upsert style notes for a client.
    doc_type 'proposal' updates estimate_style_notes.
    doc_type 'invoice' updates invoice_style_notes.
    """
    try:
        supabase = get_supabase()
        now = datetime.now(timezone.utc).isoformat()

        # Determine which column to update
        if doc_type == "proposal":
            col = "estimate_style_notes"
        elif doc_type == "invoice":
            col = "invoice_style_notes"
        else:
            print(f"[{timestamp()}] ERROR db_document: Invalid doc_type for upsert: {doc_type}")
            return False

        # Check if record exists
        existing = get_prompt_override(client_id)

        if existing:
            supabase.table("client_prompt_overrides").update({
                col: style_notes,
                "updated_at": now,
            }).eq("client_id", client_id).execute()
        else:
            supabase.table("client_prompt_overrides").insert({
                "client_id": client_id,
                col: style_notes,
                "updated_at": now,
            }).execute()

        print(f"[{timestamp()}] INFO db_document: Upserted {col} for client {client_id[:8]}...")
        return True
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_document: upsert_prompt_override failed — {e}")
        return False


# ---------------------------------------------------------------------------
# Entity lookups
# ---------------------------------------------------------------------------

def get_client_by_id(client_id: str) -> dict | None:
    """Look up a client by their UUID."""
    try:
        supabase = get_supabase()
        result = (
            supabase.table("clients")
            .select("*")
            .eq("id", client_id)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_document: get_client_by_id failed — {e}")
        return None


def get_customer_by_id(customer_id: str) -> dict | None:
    """Look up a customer by their UUID."""
    try:
        supabase = get_supabase()
        result = (
            supabase.table("customers")
            .select("*")
            .eq("id", customer_id)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_document: get_customer_by_id failed — {e}")
        return None
