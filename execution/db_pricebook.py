"""
db_pricebook.py — CRUD operations for the pricebook_items table

The pricebook is the central pricing source for each client. Agents read
from it when generating proposals and invoices. It's seeded from the
vertical template on onboarding, then customized by the owner.

Usage:
    from execution.db_pricebook import get_pricebook, seed_from_template
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_pricebook(client_id: str, active_only: bool = True) -> list:
    """
    Get all pricebook items for a client, ordered by sort_order then name.

    Returns:
        List of dicts with all pricebook_items columns.
        Empty list on error or if table doesn't exist.
    """
    try:
        sb = get_supabase()
        query = sb.table("pricebook_items").select("*").eq("client_id", client_id)
        if active_only:
            query = query.eq("is_active", True)
        result = query.order("sort_order").order("job_name").execute()
        return result.data or []
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_pricebook: get_pricebook failed — {e}")
        return []


def get_pricebook_by_job_type(client_id: str, job_type: str) -> dict | None:
    """
    Find the best matching pricebook item for a job type.
    Tries exact match on job_name first, then ilike fuzzy match.

    Returns:
        Single pricebook item dict, or None if no match.
    """
    try:
        sb = get_supabase()
        # Exact match on category or job_name
        result = sb.table("pricebook_items").select("*").eq(
            "client_id", client_id
        ).eq("is_active", True).ilike(
            "job_name", f"%{job_type}%"
        ).limit(1).execute()
        if result.data:
            return result.data[0]

        # Try category match
        result = sb.table("pricebook_items").select("*").eq(
            "client_id", client_id
        ).eq("is_active", True).ilike(
            "category", f"%{job_type}%"
        ).limit(1).execute()
        if result.data:
            return result.data[0]

        return None
    except Exception as e:
        print(f"[{timestamp()}] ERROR db_pricebook: get_pricebook_by_job_type failed — {e}")
        return None


def get_pricebook_for_prompt(client_id: str) -> str:
    """
    Build a pricing context string for injection into Claude prompts.
    Returns a formatted string of all active pricebook items with
    their 3-tier pricing, ready to paste into a system prompt.

    Returns:
        Formatted string like:
            "Pump-out (1000 gal): $275 / $325 / $375 (low/mid/high)
             Septic Inspection: $150 / $250 / $400
             ..."
        Empty string if no pricebook items.
    """
    items = get_pricebook(client_id)
    if not items:
        return ""

    lines = []
    for item in items:
        name = item.get("job_name", "Service")
        low = item.get("price_low") or item.get("price_mid") or 0
        mid = item.get("price_mid") or 0
        high = item.get("price_high") or mid
        unit = item.get("unit_of_measure", "per job")
        line = f"- {name}: ${low:.0f} / ${mid:.0f} / ${high:.0f} (low/standard/premium) {unit}"
        if item.get("description"):
            line += f" — {item['description']}"
        lines.append(line)

    return "\n".join(lines)


def seed_from_pricing_json(client_id: str, pricing_json: list,
                           vertical_key: str = None, source: str = "onboarding") -> int:
    """
    Seed pricebook_items from a pricing_json array (from onboarding wizard).
    Skips items that already exist for this client (by job_name).

    Args:
        client_id: UUID of the client
        pricing_json: List of dicts with keys: service, low, typical, high
        vertical_key: Trade vertical (optional)
        source: Where the data came from (onboarding / csv_import / manual)

    Returns:
        Number of items inserted.
    """
    if not pricing_json:
        return 0

    try:
        sb = get_supabase()

        # Get existing job names to avoid duplicates
        existing = sb.table("pricebook_items").select("job_name").eq(
            "client_id", client_id
        ).eq("is_active", True).execute()
        existing_names = {(r.get("job_name") or "").lower() for r in (existing.data or [])}

        inserted = 0
        for i, item in enumerate(pricing_json):
            name = (item.get("service") or item.get("service_name") or "").strip()
            if not name:
                continue
            if name.lower() in existing_names:
                continue

            low = float(item.get("low") or item.get("price_low") or 0)
            mid = float(item.get("typical") or item.get("price_typical") or item.get("price_mid") or 0)
            high = float(item.get("high") or item.get("price_high") or 0)

            # Auto-calculate mid if not provided
            if not mid and low and high:
                mid = round((low + high) / 2, 2)

            try:
                sb.table("pricebook_items").insert({
                    "client_id": client_id,
                    "job_name": name,
                    "price_low": low or None,
                    "price_mid": mid or None,
                    "price_high": high or None,
                    "unit_of_measure": item.get("price_unit") or "per job",
                    "vertical_key": vertical_key,
                    "source": source,
                    "sort_order": i,
                    "is_active": True,
                }).execute()
                existing_names.add(name.lower())
                inserted += 1
            except Exception as e:
                # Likely unique constraint violation — skip
                print(f"[{timestamp()}] WARN db_pricebook: skipped '{name}' — {e}")

        print(f"[{timestamp()}] INFO db_pricebook: Seeded {inserted} pricebook items for client_id={client_id}")
        return inserted

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_pricebook: seed_from_pricing_json failed — {e}")
        return 0


def seed_from_benchmarks(client_id: str, vertical_key: str) -> int:
    """
    Seed pricebook_items from the pricing_benchmarks table (or hardcoded fallback).
    Used when a client has no pricing_json from onboarding.

    Returns:
        Number of items inserted.
    """
    from execution.db_pricing import get_benchmarks
    benchmarks = get_benchmarks(vertical_key)
    if not benchmarks:
        return 0

    # Convert benchmarks format to pricing_json format
    pricing = [
        {
            "service": b.get("service_name", ""),
            "low": b.get("price_low", 0),
            "typical": b.get("price_typical", 0),
            "high": b.get("price_high", 0),
            "price_unit": b.get("price_unit", "per job"),
        }
        for b in benchmarks
    ]
    return seed_from_pricing_json(client_id, pricing, vertical_key, source="template")


def save_pricebook(client_id: str, items: list) -> int:
    """
    Replace all pricebook items for a client. Used by the Admin pricing tab.
    Soft-deletes all existing items, then inserts the new set.

    Args:
        client_id: UUID of the client
        items: List of dicts with keys: service, low, typical, high
               (may also include id for existing items)

    Returns:
        Number of items inserted.
    """
    try:
        sb = get_supabase()

        # Soft-delete all existing items
        sb.table("pricebook_items").update({
            "is_active": False,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("client_id", client_id).eq("is_active", True).execute()

        # Insert the new set
        inserted = 0
        for i, item in enumerate(items):
            name = (item.get("service") or item.get("job_name") or "").strip()
            if not name:
                continue
            try:
                sb.table("pricebook_items").insert({
                    "client_id": client_id,
                    "job_name": name,
                    "price_low": float(item.get("low") or item.get("price_low") or 0) or None,
                    "price_mid": float(item.get("typical") or item.get("price_mid") or 0) or None,
                    "price_high": float(item.get("high") or item.get("price_high") or 0) or None,
                    "unit_of_measure": item.get("unit_of_measure") or "per job",
                    "vertical_key": item.get("vertical_key"),
                    "source": "manual",
                    "sort_order": i,
                    "is_active": True,
                }).execute()
                inserted += 1
            except Exception as e:
                print(f"[{timestamp()}] WARN db_pricebook: save item '{name}' failed — {e}")

        print(f"[{timestamp()}] INFO db_pricebook: Saved {inserted} pricebook items for client_id={client_id}")
        return inserted

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_pricebook: save_pricebook failed — {e}")
        return 0


def increment_usage(item_id: str) -> None:
    """Increment times_used and update last_used_at for a pricebook item."""
    try:
        sb = get_supabase()
        # Fetch current count
        result = sb.table("pricebook_items").select("times_used").eq("id", item_id).execute()
        if result.data:
            current = result.data[0].get("times_used", 0) or 0
            sb.table("pricebook_items").update({
                "times_used": current + 1,
                "last_used_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", item_id).execute()
    except Exception:
        pass  # Non-fatal


# ---------------------------------------------------------------------------
# Self-learning: update pricebook from consistent price adjustments
# ---------------------------------------------------------------------------

# Minimum adjustments in the same direction before auto-updating
LEARNING_THRESHOLD = 3
# Confidence assigned after auto-update
LEARNED_CONFIDENCE = 0.85


def learn_from_adjustments(client_id: str) -> dict:
    """
    Read pricing_adjustments for this client, find services where the
    owner has consistently adjusted prices in the same direction 3+ times,
    and update the pricebook's price_mid to reflect the learned preference.

    Also updates the confidence column on matched pricebook items.

    Returns:
        dict with keys: services_analyzed, services_updated, details[]
    """
    result = {"services_analyzed": 0, "services_updated": 0, "details": []}

    try:
        sb = get_supabase()

        # Fetch all adjustments for this client
        adj_result = sb.table("pricing_adjustments").select(
            "service_name, original_price, adjusted_price, delta, direction, created_at"
        ).eq("client_id", client_id).order("created_at").execute()

        adjustments = adj_result.data or []
        if not adjustments:
            return result

        # Group by service_name (lowercased for matching)
        from collections import defaultdict
        by_service = defaultdict(list)
        for adj in adjustments:
            name = (adj.get("service_name") or "").strip()
            if name:
                by_service[name.lower()].append(adj)

        result["services_analyzed"] = len(by_service)

        # Load current pricebook for matching
        pricebook = get_pricebook(client_id)
        pb_lookup = {item["job_name"].lower(): item for item in pricebook}

        for service_key, adjs in by_service.items():
            if len(adjs) < LEARNING_THRESHOLD:
                continue

            # Check if adjustments are consistently in one direction
            directions = [a.get("direction") for a in adjs]
            up_count = directions.count("up")
            down_count = directions.count("down")
            total = len(directions)

            # Need 70%+ consistency in one direction
            if up_count / total >= 0.7:
                consistent_direction = "up"
            elif down_count / total >= 0.7:
                consistent_direction = "down"
            else:
                continue  # Mixed signals — don't learn yet

            # Calculate the new price: average of last 3 adjusted prices
            recent = adjs[-LEARNING_THRESHOLD:]
            avg_adjusted = round(
                sum(float(a.get("adjusted_price", 0)) for a in recent) / len(recent), 2
            )

            # Find the matching pricebook item
            pb_item = pb_lookup.get(service_key)
            if not pb_item:
                # Try fuzzy match
                for pb_name, pb in pb_lookup.items():
                    if service_key in pb_name or pb_name in service_key:
                        pb_item = pb
                        break

            if not pb_item:
                continue

            current_mid = float(pb_item.get("price_mid") or 0)
            if abs(current_mid - avg_adjusted) < 1.0:
                continue  # Difference too small to matter

            # Update the pricebook item
            try:
                update = {
                    "price_mid": avg_adjusted,
                    "confidence": LEARNED_CONFIDENCE,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }

                # Also adjust low/high proportionally
                if current_mid > 0:
                    ratio = avg_adjusted / current_mid
                    old_low = float(pb_item.get("price_low") or 0)
                    old_high = float(pb_item.get("price_high") or 0)
                    if old_low:
                        update["price_low"] = round(old_low * ratio, 2)
                    if old_high:
                        update["price_high"] = round(old_high * ratio, 2)

                sb.table("pricebook_items").update(update).eq(
                    "id", pb_item["id"]
                ).execute()

                detail = {
                    "service": adjs[-1].get("service_name", service_key),
                    "direction": consistent_direction,
                    "adjustments": len(adjs),
                    "old_price": current_mid,
                    "new_price": avg_adjusted,
                }
                result["details"].append(detail)
                result["services_updated"] += 1

                print(
                    f"[{timestamp()}] INFO db_pricebook: Learned price for "
                    f"'{detail['service']}' — ${current_mid:.2f} → ${avg_adjusted:.2f} "
                    f"({consistent_direction}, {len(adjs)} adjustments, "
                    f"confidence={LEARNED_CONFIDENCE})"
                )

            except Exception as e:
                print(f"[{timestamp()}] WARN db_pricebook: pricebook update failed for {service_key} — {e}")

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_pricebook: learn_from_adjustments failed — {e}")

    return result
