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
import re

VERTICALS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "directives", "verticals"
)

_cache = {}

# Display-name aliases — map friendly names that owners type into the
# clients.trade_vertical column to the canonical config directory keys
# under directives/verticals/. New aliases get added here as we onboard
# more clients with different naming conventions; the normalizer below
# also handles common variations automatically (case, separators).
_VERTICAL_ALIASES = {
    "septic & sewer":        "sewer_drain",
    "septic and sewer":      "sewer_drain",
    "septic":                "sewer_drain",
    "sewer":                 "sewer_drain",
    "sewer & drain":         "sewer_drain",
    "sewer and drain":       "sewer_drain",
    "drain":                 "sewer_drain",
    "drains":                "sewer_drain",
    "plumbing":              "sewer_drain",
    "lawn care":             "landscaping",
    "lawn":                  "landscaping",
    "landscape":             "landscaping",
    "gravel":                "gravel_pit",
    "gravel & stone":        "gravel_pit",
}


def _normalize_vertical_key(raw: str) -> str:
    """
    Map a free-form vertical string to a canonical config directory name.

    Handles three layers in order:
      1. Empty / None → "sewer_drain" (sensible default for the first vertical)
      2. Exact match against _VERTICAL_ALIASES (case-insensitive)
      3. Slug-style fallback: lowercase, strip punctuation, collapse
         whitespace and ' & ' / 'and' to underscores. So "Septic & Sewer"
         becomes "septic_sewer" — caught by the alias map above before
         reaching this fallback, but it provides a safety net for
         unknown variations.
    """
    if not raw or not raw.strip():
        return "sewer_drain"
    key = raw.strip().lower()
    if key in _VERTICAL_ALIASES:
        return _VERTICAL_ALIASES[key]
    # Slug fallback — replace separators with _, strip punctuation
    slug = re.sub(r"\s+&\s+|\s+and\s+", "_", key)
    slug = re.sub(r"[^a-z0-9_]+", "_", slug).strip("_")
    if slug in _VERTICAL_ALIASES:
        return _VERTICAL_ALIASES[slug]
    return slug or "sewer_drain"


def load_vertical(vertical_key: str) -> dict:
    """Load config.json for a trade vertical. Cached after first load.

    `vertical_key` may be either a canonical directory name ("sewer_drain")
    or a free-form display name from clients.trade_vertical
    ("Septic & Sewer"). Display names are normalized to the canonical
    key via _normalize_vertical_key() before the file lookup.
    """
    canonical = _normalize_vertical_key(vertical_key)

    if canonical in _cache:
        return _cache[canonical]

    config_path = os.path.join(VERTICALS_DIR, canonical, "config.json")

    if not os.path.exists(config_path):
        # Graceful fallback — return empty config, never crash
        print(
            f"[vertical_loader] WARN: no config found for vertical "
            f"'{vertical_key}' (normalized to '{canonical}'), using empty defaults"
        )
        _cache[canonical] = {}
        return {}

    with open(config_path, "r") as f:
        config = json.load(f)

    _cache[canonical] = config
    return config


def load_vertical_prices(vertical_key: str) -> list:
    """Load prices.json for a trade vertical. Accepts display names."""
    canonical = _normalize_vertical_key(vertical_key)
    prices_path = os.path.join(VERTICALS_DIR, canonical, "prices.json")

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
