"""Tests for the vertical config loader."""

import sys
import os

# Ensure project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from execution.vertical_loader import (
    load_vertical,
    get_tax_rate,
    get_default_job_type,
    _cache,
)


def setup_function():
    """Clear the loader cache before each test."""
    _cache.clear()


def test_sewer_drain_config_loads():
    config = load_vertical("sewer_drain")
    assert config, "Config should not be empty"
    assert "job_types" in config
    assert "pump" in config["job_types"]


def test_sewer_drain_tax_rate():
    assert get_tax_rate("sewer_drain") == 0.055


def test_unknown_vertical_returns_empty():
    config = load_vertical("unknown_vertical_xyz")
    assert config == {}


def test_default_job_type():
    assert get_default_job_type("sewer_drain") == "pump"


def test_landscaping_config_loads():
    _cache.clear()
    config = load_vertical("landscaping")
    assert config, "Landscaping config should not be empty"
    assert "job_types" in config
    assert "mow" in config["job_types"]


def test_landscaping_default_job_type():
    _cache.clear()
    assert get_default_job_type("landscaping") == "mow"


def test_landscaping_tax_rate_is_zero():
    _cache.clear()
    assert get_tax_rate("landscaping") == 0.0


def test_landscaping_has_snow_removal():
    _cache.clear()
    config = load_vertical("landscaping")
    assert "snow_removal" in config["job_types"]
