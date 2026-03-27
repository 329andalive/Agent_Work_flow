"""
geocode.py — Address to lat/lng + Somerset County zone clustering

Takes a raw address string and returns lat, lng, zone_cluster, and
formatted_address using the Google Maps Geocoding API.

Zone thresholds for Somerset County ME:
  North   = lat > 44.85
  Central = 44.65 < lat <= 44.85
  South   = lat <= 44.65

Never raises on geocode failure — a job without coordinates is still
a valid job. Returns zone_cluster='Unknown' on error.

Environment:
  GOOGLE_MAPS_API_KEY — set in .env

Usage:
    from execution.geocode import geocode_address
    result = await geocode_address("14 Oak Street, Norridgewock ME")
    # → {"lat": 44.714, "lng": -69.789, "zone_cluster": "Central",
    #    "formatted_address": "14 Oak St, Norridgewock, ME 04957, USA"}
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

# Google Maps API key — add to .env:
# GOOGLE_MAPS_API_KEY=your_key_here
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

# Somerset County ME zone thresholds
ZONE_THRESHOLDS = [
    ("North", lambda lat: lat > 44.85),
    ("Central", lambda lat: 44.65 < lat <= 44.85),
    ("South", lambda lat: lat <= 44.65),
]


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _classify_zone(lat: float) -> str:
    """Classify a latitude into a Somerset County zone."""
    for zone_name, test in ZONE_THRESHOLDS:
        if test(lat):
            return zone_name
    return "Unknown"


async def geocode_address(address: str) -> dict:
    """
    Geocode a raw address string to lat/lng + zone cluster.

    Args:
        address: Free-text address string (e.g. "14 Oak Street, Norridgewock ME")

    Returns:
        dict with keys: lat, lng, zone_cluster, formatted_address.
        On error: lat=None, lng=None, zone_cluster='Unknown', error=str.
        Never raises — a job without coordinates is still a valid job.
    """
    error_result = {
        "lat": None,
        "lng": None,
        "zone_cluster": "Unknown",
        "formatted_address": None,
        "error": None,
    }

    if not address or not address.strip():
        error_result["error"] = "Empty address"
        print(f"[{timestamp()}] WARN geocode: Empty address — skipping")
        return error_result

    if not GOOGLE_MAPS_API_KEY:
        error_result["error"] = "GOOGLE_MAPS_API_KEY not set"
        print(f"[{timestamp()}] WARN geocode: GOOGLE_MAPS_API_KEY not set — skipping")
        return error_result

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                GEOCODE_URL,
                params={
                    "address": address,
                    "key": GOOGLE_MAPS_API_KEY,
                },
            )
            response.raise_for_status()
            data = response.json()

        status = data.get("status", "")
        if status != "OK":
            error_result["error"] = f"Google API status: {status}"
            print(f"[{timestamp()}] WARN geocode: Google returned status={status} for '{address}'")
            return error_result

        results = data.get("results", [])
        if not results:
            error_result["error"] = "No results returned"
            print(f"[{timestamp()}] WARN geocode: No results for '{address}'")
            return error_result

        location = results[0].get("geometry", {}).get("location", {})
        lat = location.get("lat")
        lng = location.get("lng")
        formatted = results[0].get("formatted_address", "")

        if lat is None or lng is None:
            error_result["error"] = "No coordinates in response"
            print(f"[{timestamp()}] WARN geocode: No coordinates for '{address}'")
            return error_result

        zone = _classify_zone(lat)

        print(f"[{timestamp()}] INFO geocode: '{address}' → {lat:.4f}, {lng:.4f} zone={zone}")

        return {
            "lat": lat,
            "lng": lng,
            "zone_cluster": zone,
            "formatted_address": formatted,
        }

    except Exception as e:
        error_result["error"] = str(e)
        print(f"[{timestamp()}] ERROR geocode: Failed for '{address}' — {e}")
        return error_result


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import asyncio

    async def test():
        result = await geocode_address("14 Oak Street, Norridgewock ME")
        import json
        print(json.dumps(result, indent=2))

    asyncio.run(test())
