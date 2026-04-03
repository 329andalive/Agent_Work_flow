"""
db_pricing.py — Pricing benchmarks and adjustment logging

Handles:
  - Querying pricing_benchmarks table for onboarding templates
  - Querying trade_verticals for specialties and vertical list
  - Logging price adjustments when owners edit proposals/invoices

Usage:
    from execution.db_pricing import get_benchmarks, log_price_adjustment
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Hardcoded fallback when pricing_benchmarks table doesn't exist
# ---------------------------------------------------------------------------

FALLBACK_BENCHMARKS = {
    "septic": [
        {"service_name": "Septic Tank Pump-Out (up to 1000 gal)", "price_low": 275, "price_typical": 375, "price_high": 500, "price_unit": "per job", "notes": ""},
        {"service_name": "Septic Tank Pump-Out (1000-1500 gal)", "price_low": 350, "price_typical": 450, "price_high": 600, "price_unit": "per job", "notes": ""},
        {"service_name": "Septic Tank Pump-Out (1500+ gal)", "price_low": 450, "price_typical": 600, "price_high": 850, "price_unit": "per job", "notes": ""},
        {"service_name": "Septic Inspection", "price_low": 150, "price_typical": 250, "price_high": 400, "price_unit": "per job", "notes": ""},
        {"service_name": "Drain Field Assessment", "price_low": 300, "price_typical": 500, "price_high": 800, "price_unit": "per job", "notes": ""},
        {"service_name": "Effluent Filter Cleaning/Replacement", "price_low": 75, "price_typical": 150, "price_high": 250, "price_unit": "per job", "notes": ""},
    ],
    "plumbing": [
        {"service_name": "Service Call / Diagnostic", "price_low": 95, "price_typical": 150, "price_high": 225, "price_unit": "per job", "notes": ""},
        {"service_name": "Drain Cleaning", "price_low": 150, "price_typical": 250, "price_high": 400, "price_unit": "per job", "notes": ""},
        {"service_name": "Water Heater Replacement", "price_low": 800, "price_typical": 1200, "price_high": 2000, "price_unit": "per job", "notes": ""},
    ],
    "hvac": [
        {"service_name": "AC Tune-Up", "price_low": 89, "price_typical": 129, "price_high": 199, "price_unit": "per job", "notes": ""},
        {"service_name": "Furnace Tune-Up", "price_low": 89, "price_typical": 129, "price_high": 199, "price_unit": "per job", "notes": ""},
        {"service_name": "Refrigerant Recharge", "price_low": 200, "price_typical": 350, "price_high": 600, "price_unit": "per job", "notes": ""},
    ],
}


# ---------------------------------------------------------------------------
# Pricing benchmarks
# ---------------------------------------------------------------------------

def get_benchmarks(vertical_key: str, region: str = "northeast_us") -> list:
    """
    Get pricing benchmarks for a trade vertical.

    Args:
        vertical_key: e.g. 'septic', 'plumbing', 'hvac'
        region:       pricing region (default 'northeast_us')

    Returns:
        List of dicts with service_name, price_low, price_typical,
        price_high, price_unit, notes. Empty list on error.
    """
    try:
        supabase = get_supabase()
        result = (
            supabase.table("pricing_benchmarks")
            .select("service_name, price_low, price_typical, price_high, price_unit, notes")
            .eq("vertical_key", vertical_key)
            .eq("region", region)
            .eq("active", True)
            .order("sort_order")
            .execute()
        )
        services = result.data or []
        print(f"[{timestamp()}] INFO db_pricing: Loaded {len(services)} benchmarks for {vertical_key}")
        return services
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_pricing: get_benchmarks failed — {e}")
        fallback = FALLBACK_BENCHMARKS.get(vertical_key, [])
        if fallback:
            print(f"[{timestamp()}] INFO db_pricing: using hardcoded fallback for {vertical_key} ({len(fallback)} services)")
        return fallback


def get_verticals() -> list:
    """
    Get all active trade verticals for the onboarding wizard.

    Returns:
        List of dicts with vertical_key, vertical_label, icon, sort_order.
    """
    try:
        supabase = get_supabase()
        result = (
            supabase.table("trade_verticals")
            .select("vertical_key, vertical_label, icon, sort_order")
            .eq("active", True)
            .order("sort_order")
            .execute()
        )
        return result.data or []
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_pricing: get_verticals failed — {e}")
        return []


def get_specialties_from_db(vertical_key: str) -> list:
    """
    Get specialties for a trade vertical from the database.

    Returns:
        List of specialty strings, or empty list.
    """
    try:
        supabase = get_supabase()
        result = (
            supabase.table("trade_verticals")
            .select("specialties")
            .eq("vertical_key", vertical_key)
            .eq("active", True)
            .execute()
        )
        if result.data and result.data[0].get("specialties"):
            return result.data[0]["specialties"]
        return []
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_pricing: get_specialties_from_db failed — {e}")
        return []


# ---------------------------------------------------------------------------
# Price adjustment logging (learning foundation)
# ---------------------------------------------------------------------------

def log_price_adjustment(
    client_id: str,
    vertical_key: str,
    service_name: str,
    original_price: float,
    adjusted_price: float,
    context: str = "manual_override",
) -> None:
    """
    Log when an owner manually changes a price on a proposal or invoice.
    This data feeds the future pricing learning system.

    Args:
        client_id:      UUID of the client
        vertical_key:   Trade vertical (e.g. 'septic')
        service_name:   Name of the service adjusted
        original_price: Price before the owner's edit
        adjusted_price: Price after the owner's edit
        context:        Where the adjustment happened
                        (proposal_edit / invoice_edit / onboarding_setup / manual_override)
    """
    try:
        delta = round(adjusted_price - original_price, 2)
        if delta > 0:
            direction = "up"
        elif delta < 0:
            direction = "down"
        else:
            direction = "same"

        supabase = get_supabase()
        supabase.table("pricing_adjustments").insert({
            "client_id": client_id,
            "vertical_key": vertical_key,
            "service_name": service_name,
            "original_price": original_price,
            "adjusted_price": adjusted_price,
            "delta": delta,
            "direction": direction,
            "context": context,
        }).execute()

        print(
            f"[{timestamp()}] INFO db_pricing: Price adjustment logged | "
            f"client={client_id[:8]}... | service={service_name} | "
            f"${original_price} → ${adjusted_price} ({direction})"
        )
    except Exception as e:
        print(f"[{timestamp()}] WARN db_pricing: log_price_adjustment failed — {e}")
