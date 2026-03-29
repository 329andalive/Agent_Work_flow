"""
vertical_loader.py — Load trade vertical config for any client.

Usage:
    from execution.vertical_loader import load_vertical
    config = load_vertical("sewer_drain")
    tax_rate = config["tax_rules"]["parts_rate"]
    keywords = config["sms_keywords"]["invoice"]
"""

import json
import os

VERTICALS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "directives", "verticals"
)

_cache = {}


def load_vertical(vertical_key: str) -> dict:
    """Load config.json for a trade vertical. Cached after first load."""
    if vertical_key in _cache:
        return _cache[vertical_key]

    config_path = os.path.join(VERTICALS_DIR, vertical_key, "config.json")

    if not os.path.exists(config_path):
        # Graceful fallback — return empty config, never crash
        print(f"[vertical_loader] WARN: no config found for vertical '{vertical_key}', using empty defaults")
        return {}

    with open(config_path, "r") as f:
        config = json.load(f)

    _cache[vertical_key] = config
    return config


def load_vertical_prices(vertical_key: str) -> list:
    """Load prices.json for a trade vertical."""
    prices_path = os.path.join(VERTICALS_DIR, vertical_key, "prices.json")

    if not os.path.exists(prices_path):
        return []

    with open(prices_path, "r") as f:
        data = json.load(f)

    return data.get("services", [])


def get_tax_rate(vertical_key: str) -> float:
    """Return the parts tax rate for a vertical. Defaults to 0.0 if not set."""
    config = load_vertical(vertical_key)
    return config.get("tax_rules", {}).get("parts_rate", 0.0)


def get_tax_label(vertical_key: str) -> str:
    """Return the tax label string for display. e.g. 'Maine 5.5%'"""
    config = load_vertical(vertical_key)
    return config.get("tax_rules", {}).get("tax_label", "Tax")


def get_job_type_keywords(vertical_key: str) -> dict:
    """Return the job_type_map dict for SMS classification."""
    config = load_vertical(vertical_key)
    return config.get("sms_keywords", {}).get("job_type_map", {})


def get_default_job_type(vertical_key: str) -> str:
    """Return the default job type for a vertical."""
    config = load_vertical(vertical_key)
    return config.get("default_job_type", "service")
