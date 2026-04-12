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
        result = sb.table("pricebook_items").select("*").eq(
            "client_id", client_id
        ).eq("is_active", True).ilike(
            "job_name", f"%{job_type}%"
        ).limit(1).execute()
        if result.data:
            return result.data[0]

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

    CRITICAL: Only the standard (mid) price is shown — never the range.
    Showing low/mid/high causes Claude to anchor on the lowest number,
    which is the root cause of the repricing bug. Claude's only job is
    to use the standard price as a fallback when the tech didn't state
    one. The tech always has final say on price.

    Returns:
        Formatted string like:
            "- Pump-out (1000 gal): $325 per job
             - Baffle replacement: $200 per job"
        Empty string if no pricebook items.
    """
    items = get_pricebook(client_id)
    if not items:
        return ""

    lines = []
    for item in items:
        name = item.get("job_name", "Service")
        standard = item.get("price_mid") or item.get("price_low") or 0
        if not standard:
            continue
        unit = item.get("unit_of_measure", "per job")
        lines.append(f"- {name}: ${standard:.0f} {unit}")

    return "\n".join(lines)


def add_job_type(
    client_id: str,
    job_name: str,
    description: str,
    price_mid: float,
    unit_of_measure: str = "per job",
) -> dict | None:
    """
    Add a new job type to the client's pricebook during a chat session.

    Called by the guided estimate 'add_job_type' sub-flow when a tech
    encounters a job type the system doesn't recognise and wants to
    add it to their pricebook on the fly.

    Args:
        client_id:       UUID of the client (multi-tenant safe)
        job_name:        Human-readable name: "12 inch culvert replacement"
        description:     One-line scope description: "Install/replace 12\" culvert"
        price_mid:       Standard price — always tech-entered, never AI-generated
        unit_of_measure: "per job" | "per foot" | "per hour" | etc.

    Returns:
        The new pricebook_items row dict, or None on failure.
    """
    if not client_id or not job_name or not job_name.strip():
        print(f"[{timestamp()}] WARN db_pricebook: add_job_type called with missing args")
        return None

    try:
        sb = get_supabase()

        # Check for an existing item with this name to avoid duplicates
        existing = sb.table("pricebook_items").select("id, job_name").eq(
            "client_id", client_id
        ).ilike("job_name", job_name.strip()).limit(1).execute()

        if existing.data:
            print(f"[{timestamp()}] INFO db_pricebook: job type '{job_name}' already exists — skipping insert")
            return existing.data[0]

        # Get current max sort_order so the new item lands at the bottom
        order_result = sb.table("pricebook_items").select("sort_order").eq(
            "client_id", client_id
        ).eq("is_active", True).order("sort_order", desc=True).limit(1).execute()
        next_order = (order_result.data[0]["sort_order"] + 1) if order_result.data else 0

        row = {
            "client_id":       client_id,
            "job_name":        job_name.strip(),
            "description":     description.strip() if description else "",
            "price_mid":       float(price_mid),
            "unit_of_measure": unit_of_measure.strip() if unit_of_measure else "per job",
            "source":          "tech_chat",   # distinguishes from onboarding/template entries
            "sort_order":      next_order,
            "is_active":       True,
        }

        result = sb.table("pricebook_items").insert(row).execute()
        if result.data:
            new_item = result.data[0]
            print(
                f"[{timestamp()}] INFO db_pricebook: Added job type '{job_name}' "
                f"${price_mid:.0f} {unit_of_measure} for client_id={client_id[:8]}"
            )
            return new_item

        print(f"[{timestamp()}] WARN db_pricebook: add_job_type insert returned no data")
        return None

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_pricebook: add_job_type failed — {e}")
        return None


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
                print(f"[{timestamp()}] WARN db_pricebook: skipped '{name}' — {e}")

        print(f"[{timestamp()}] INFO db_pricebook: Seeded {inserted} pricebook items for client_id={client_id}")
        return inserted

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_pricebook: seed_from_pricing_json failed — {e}")
        return 0


def seed_from_benchmarks(client_id: str, vertical_key: str) -> int:
    """
    Seed pricebook_items from the pricing_benchmarks table (or hardcoded fallback).
    """
    from execution.db_pricing import get_benchmarks
    benchmarks = get_benchmarks(vertical_key)
    if not benchmarks:
        return 0

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
    """
    try:
        sb = get_supabase()

        sb.table("pricebook_items").update({
            "is_active": False,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("client_id", client_id).eq("is_active", True).execute()

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
        result = sb.table("pricebook_items").select("times_used").eq("id", item_id).execute()
        if result.data:
            current = result.data[0].get("times_used", 0) or 0
            sb.table("pricebook_items").update({
                "times_used": current + 1,
                "last_used_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", item_id).execute()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Self-learning: update pricebook from consistent price adjustments
# ---------------------------------------------------------------------------

LEARNING_THRESHOLD = 3
LEARNED_CONFIDENCE = 0.85


def learn_from_adjustments(client_id: str) -> dict:
    """
    Read pricing_adjustments for this client, find services where the
    owner has consistently adjusted prices in the same direction 3+ times,
    and update the pricebook's price_mid to reflect the learned preference.
    """
    result = {"services_analyzed": 0, "services_updated": 0, "details": []}

    try:
        sb = get_supabase()

        adj_result = sb.table("pricing_adjustments").select(
            "service_name, original_price, adjusted_price, delta, direction, created_at"
        ).eq("client_id", client_id).order("created_at").execute()

        adjustments = adj_result.data or []
        if not adjustments:
            return result

        from collections import defaultdict
        by_service = defaultdict(list)
        for adj in adjustments:
            name = (adj.get("service_name") or "").strip()
            if name:
                by_service[name.lower()].append(adj)

        result["services_analyzed"] = len(by_service)

        pricebook = get_pricebook(client_id)
        pb_lookup = {item["job_name"].lower(): item for item in pricebook}

        for service_key, adjs in by_service.items():
            if len(adjs) < LEARNING_THRESHOLD:
                continue

            directions = [a.get("direction") for a in adjs]
            up_count = directions.count("up")
            down_count = directions.count("down")
            total = len(directions)

            if up_count / total >= 0.7:
                consistent_direction = "up"
            elif down_count / total >= 0.7:
                consistent_direction = "down"
            else:
                continue

            recent = adjs[-LEARNING_THRESHOLD:]
            avg_adjusted = round(
                sum(float(a.get("adjusted_price", 0)) for a in recent) / len(recent), 2
            )

            pb_item = pb_lookup.get(service_key)
            if not pb_item:
                for pb_name, pb in pb_lookup.items():
                    if service_key in pb_name or pb_name in service_key:
                        pb_item = pb
                        break

            if not pb_item:
                continue

            current_mid = float(pb_item.get("price_mid") or 0)
            if abs(current_mid - avg_adjusted) < 1.0:
                continue

            try:
                update = {
                    "price_mid": avg_adjusted,
                    "confidence": LEARNED_CONFIDENCE,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }

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
                    f"({consistent_direction}, {len(adjs)} adjustments)"
                )

            except Exception as e:
                print(f"[{timestamp()}] WARN db_pricebook: pricebook update failed for {service_key} — {e}")

    except Exception as e:
        print(f"[{timestamp()}] ERROR db_pricebook: learn_from_adjustments failed — {e}")

    return result
