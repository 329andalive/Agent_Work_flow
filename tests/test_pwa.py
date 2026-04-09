"""
test_pwa.py — Tests for the PWA scaffolding.

Verifies:
  - GET /pwa/ returns 200 and includes manifest + service worker registration
  - GET /static/manifest.json returns valid JSON with required PWA fields
  - GET /sw.js returns the service worker JS at root scope
  - Service-Worker-Allowed header is set so SW can control whole app
"""

import os
import sys
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def pwa_client():
    """Flask test client for PWA tests."""
    os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake-key")
    os.environ.setdefault("SECRET_KEY", "test-secret")

    from execution.sms_receive import app
    app.config["TESTING"] = True
    return app.test_client()


# ---------------------------------------------------------------------------
# /pwa/ shell
# ---------------------------------------------------------------------------

def test_pwa_shell_returns_200(pwa_client):
    """GET /pwa/ should serve the shell template."""
    resp = pwa_client.get("/pwa/")
    assert resp.status_code == 200


def test_pwa_shell_includes_manifest(pwa_client):
    """Shell HTML must link to /static/manifest.json."""
    resp = pwa_client.get("/pwa/")
    body = resp.data.decode()
    assert 'rel="manifest"' in body
    assert "/static/manifest.json" in body


def test_pwa_shell_registers_service_worker(pwa_client):
    """Shell HTML must register the service worker at /sw.js."""
    resp = pwa_client.get("/pwa/")
    body = resp.data.decode()
    assert "serviceWorker.register" in body
    assert "/sw.js" in body


def test_pwa_shell_includes_theme_color(pwa_client):
    """Shell HTML must include theme-color meta for browser chrome."""
    resp = pwa_client.get("/pwa/")
    body = resp.data.decode()
    assert 'name="theme-color"' in body


def test_pwa_shell_no_trailing_slash(pwa_client):
    """GET /pwa (no slash) should also resolve."""
    resp = pwa_client.get("/pwa")
    assert resp.status_code in (200, 301, 302, 308)


# ---------------------------------------------------------------------------
# /static/manifest.json
# ---------------------------------------------------------------------------

def test_manifest_returns_valid_json(pwa_client):
    """Manifest must be valid JSON."""
    resp = pwa_client.get("/static/manifest.json")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, dict)


def test_manifest_has_required_pwa_fields(pwa_client):
    """Manifest must include name, start_url, display, icons."""
    resp = pwa_client.get("/static/manifest.json")
    data = json.loads(resp.data)
    assert "name" in data
    assert "start_url" in data
    assert "display" in data
    assert "icons" in data
    assert len(data["icons"]) >= 1
    assert data["display"] == "standalone"
    assert data["start_url"].startswith("/pwa")


def test_manifest_icons_have_192_and_512(pwa_client):
    """PWA install requires both 192x192 and 512x512 icons."""
    resp = pwa_client.get("/static/manifest.json")
    data = json.loads(resp.data)
    sizes = {icon.get("sizes") for icon in data["icons"]}
    assert "192x192" in sizes
    assert "512x512" in sizes


# ---------------------------------------------------------------------------
# /sw.js — service worker at root scope
# ---------------------------------------------------------------------------

def test_sw_js_served_at_root(pwa_client):
    """Service worker must be reachable at /sw.js for root scope."""
    resp = pwa_client.get("/sw.js")
    assert resp.status_code == 200


def test_sw_js_has_service_worker_allowed_header(pwa_client):
    """Service-Worker-Allowed header must be set so SW can control entire app."""
    resp = pwa_client.get("/sw.js")
    assert resp.headers.get("Service-Worker-Allowed") == "/"


def test_sw_js_no_cache_header(pwa_client):
    """Service worker should not be cached so updates ship immediately."""
    resp = pwa_client.get("/sw.js")
    cache_control = resp.headers.get("Cache-Control", "")
    assert "no-cache" in cache_control or "no-store" in cache_control


def test_sw_js_javascript_content_type(pwa_client):
    """Service worker must be served with JavaScript content-type."""
    resp = pwa_client.get("/sw.js")
    assert "javascript" in resp.headers.get("Content-Type", "").lower()


# ---------------------------------------------------------------------------
# Static icons
# ---------------------------------------------------------------------------

def test_icon_192_served(pwa_client):
    """192x192 PWA icon must be reachable."""
    resp = pwa_client.get("/static/icon-192.png")
    assert resp.status_code == 200
    assert "image/png" in resp.headers.get("Content-Type", "")


def test_icon_512_served(pwa_client):
    """512x512 PWA icon must be reachable."""
    resp = pwa_client.get("/static/icon-512.png")
    assert resp.status_code == 200
    assert "image/png" in resp.headers.get("Content-Type", "")
