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

def test_pwa_shell_redirects_when_unauthed(pwa_client):
    """GET /pwa/ without auth should redirect to /pwa/login."""
    resp = pwa_client.get("/pwa/")
    assert resp.status_code in (301, 302, 308)
    assert "/pwa/login" in resp.headers.get("Location", "")


def test_pwa_shell_renders_when_authed(pwa_client):
    """GET /pwa/ with PWA session should render the shell."""
    with pwa_client.session_transaction() as sess:
        sess["pwa_authed"] = True
        sess["client_id"] = "00000000-0000-0000-0000-000000000001"
        sess["employee_name"] = "Jesse"
        sess["employee_role"] = "field_tech"
    resp = pwa_client.get("/pwa/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Jesse" in body
    assert 'rel="manifest"' in body
    assert "/static/manifest.json" in body
    assert "serviceWorker.register" in body
    assert "/sw.js" in body
    assert 'name="theme-color"' in body


def test_pwa_shell_no_trailing_slash(pwa_client):
    """GET /pwa (no slash) should resolve."""
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


# ---------------------------------------------------------------------------
# PWA magic-link auth (step 2)
# ---------------------------------------------------------------------------

def test_pwa_login_form_renders(pwa_client):
    """GET /pwa/login should render the magic-link form."""
    resp = pwa_client.get("/pwa/login")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Sign in" in body
    assert "phone" in body.lower()


def test_pwa_login_post_no_phone_returns_400(pwa_client):
    """Empty phone should return 400."""
    import json as _json
    resp = pwa_client.post(
        "/pwa/login",
        data=_json.dumps({"phone": ""}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_pwa_login_unknown_phone_returns_generic_success(pwa_client):
    """
    Unknown phone should still return success (no enumeration).
    The magic message tells the tech to check email/texts whether
    or not the number exists.
    """
    import json as _json
    from unittest.mock import patch
    with patch("execution.pwa_auth.find_client_by_phone", return_value=None):
        resp = pwa_client.post(
            "/pwa/login",
            data=_json.dumps({"phone": "+19999999999"}),
            content_type="application/json",
        )
    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert data["success"] is True


def test_pwa_login_known_phone_creates_link_and_notifies(pwa_client):
    """Valid phone should create a magic link and call notify()."""
    import json as _json
    from unittest.mock import patch
    with patch("execution.pwa_auth.find_client_by_phone", return_value="client-1"), \
         patch("execution.pwa_auth.create_magic_link", return_value={
             "success": True,
             "url": "https://example.com/pwa/auth/abc12345",
             "employee": {"name": "Jesse Smith", "phone": "+12075551234"},
             "expires_at": "2026-04-09T15:00:00+00:00",
             "error": None,
         }) as mock_create, \
         patch("execution.notify.notify", return_value={
             "success": True, "channel": "email", "error": None,
         }) as mock_notify:
        resp = pwa_client.post(
            "/pwa/login",
            data=_json.dumps({"phone": "+12075551234"}),
            content_type="application/json",
        )

    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert data["success"] is True
    mock_create.assert_called_once()
    mock_notify.assert_called_once()
    # Verify the magic link URL was passed to notify
    call_args = mock_notify.call_args
    assert "abc12345" in call_args.kwargs.get("message", "")


def test_pwa_auth_token_invalid_returns_login_page(pwa_client):
    """Bad token should re-render the login page with an error."""
    from unittest.mock import patch
    with patch("execution.pwa_auth.consume_magic_link", return_value={
        "success": False, "error": "Invalid or expired link",
    }):
        resp = pwa_client.get("/pwa/auth/badtoken")
    assert resp.status_code == 401
    assert b"Invalid" in resp.data or b"expired" in resp.data


def test_pwa_auth_token_valid_sets_session_and_redirects(pwa_client):
    """Valid token should set the PWA session and redirect to /pwa/."""
    from unittest.mock import patch
    with patch("execution.pwa_auth.consume_magic_link", return_value={
        "success": True,
        "client_id": "client-1",
        "employee_id": "emp-1",
        "employee_name": "Jesse",
        "employee_role": "field_tech",
        "employee_phone": "+12075551234",
        "error": None,
    }):
        resp = pwa_client.get("/pwa/auth/goodtoken")

    assert resp.status_code in (301, 302, 308)
    assert "/pwa/" in resp.headers.get("Location", "")

    # Verify session was set
    with pwa_client.session_transaction() as sess:
        assert sess.get("pwa_authed") is True
        assert sess.get("client_id") == "client-1"
        assert sess.get("employee_name") == "Jesse"


def test_pwa_logout_clears_session(pwa_client):
    """GET /pwa/logout should clear PWA session and redirect."""
    with pwa_client.session_transaction() as sess:
        sess["pwa_authed"] = True
        sess["employee_name"] = "Jesse"

    resp = pwa_client.get("/pwa/logout")
    assert resp.status_code in (301, 302, 308)

    with pwa_client.session_transaction() as sess:
        assert sess.get("pwa_authed") is None
        assert sess.get("employee_name") is None


# ---------------------------------------------------------------------------
# PWA clock screen + API (step 3)
# ---------------------------------------------------------------------------

def _set_pwa_session(pwa_client, employee_id="emp-1"):
    """Helper: set a logged-in PWA session."""
    with pwa_client.session_transaction() as sess:
        sess["pwa_authed"] = True
        sess["client_id"] = "client-1"
        sess["employee_id"] = employee_id
        sess["employee_name"] = "Jesse"
        sess["employee_role"] = "field_tech"
        sess["employee_phone"] = "+12075551234"


def test_pwa_clock_screen_requires_auth(pwa_client):
    """GET /pwa/clock without session should redirect to /pwa/login."""
    resp = pwa_client.get("/pwa/clock")
    assert resp.status_code in (301, 302, 308)
    assert "/pwa/login" in resp.headers.get("Location", "")


def test_pwa_clock_screen_renders_when_authed(pwa_client):
    """GET /pwa/clock with PWA session should render the clock screen."""
    _set_pwa_session(pwa_client)
    resp = pwa_client.get("/pwa/clock")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Clock" in body
    assert "Jesse" in body


def test_pwa_clock_status_returns_json(pwa_client):
    """GET /pwa/api/clock/status should return JSON status."""
    _set_pwa_session(pwa_client)
    from unittest.mock import patch
    fake_status = {
        "clocked_in": False,
        "entry_id": None,
        "clock_in_at": None,
        "elapsed_minutes": None,
        "elapsed_label": None,
        "current_job": None,
        "todays_route": [],
        "completed_today": 0,
        "total_jobs": 0,
    }
    with patch("execution.pwa_clock.get_status", return_value=fake_status):
        resp = pwa_client.get("/pwa/api/clock/status")
    import json as _json
    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert data["success"] is True
    assert data["clocked_in"] is False


def test_pwa_clock_status_requires_employee_id(pwa_client):
    """API should return 400 if session has no employee_id."""
    with pwa_client.session_transaction() as sess:
        sess["pwa_authed"] = True
        sess["client_id"] = "client-1"
        # no employee_id

    resp = pwa_client.get("/pwa/api/clock/status")
    assert resp.status_code == 400


def test_pwa_clock_in_success(pwa_client):
    """POST /pwa/api/clock/in should call clock_in() and return success."""
    _set_pwa_session(pwa_client)
    from unittest.mock import patch
    with patch("execution.pwa_clock.clock_in", return_value={
        "success": True,
        "entry_id": "te-1",
        "clock_in_at": "2026-04-09T08:00:00+00:00",
        "job_label": None,
        "started_job": None,
        "error": None,
    }) as mock_in:
        resp = pwa_client.post("/pwa/api/clock/in")

    import json as _json
    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert data["success"] is True
    assert data["entry_id"] == "te-1"
    mock_in.assert_called_once_with("client-1", "emp-1")


def test_pwa_clock_in_already_clocked_in(pwa_client):
    """POST /pwa/api/clock/in when already clocked in should return 400."""
    _set_pwa_session(pwa_client)
    from unittest.mock import patch
    with patch("execution.pwa_clock.clock_in", return_value={
        "success": False,
        "error": "Already clocked in. Clock out before starting a new shift.",
    }):
        resp = pwa_client.post("/pwa/api/clock/in")

    import json as _json
    assert resp.status_code == 400
    data = _json.loads(resp.data)
    assert data["success"] is False
    assert "Already" in data["error"]


def test_pwa_clock_out_success(pwa_client):
    """POST /pwa/api/clock/out should call clock_out() and return duration."""
    _set_pwa_session(pwa_client)
    from unittest.mock import patch
    with patch("execution.pwa_clock.clock_out", return_value={
        "success": True,
        "entry_id": "te-1",
        "duration_minutes": 135,
        "duration_label": "2h 15m",
        "error": None,
    }) as mock_out:
        resp = pwa_client.post("/pwa/api/clock/out")

    import json as _json
    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert data["success"] is True
    assert data["duration_minutes"] == 135
    assert data["duration_label"] == "2h 15m"
    mock_out.assert_called_once_with("client-1", "emp-1")


def test_pwa_clock_out_not_clocked_in(pwa_client):
    """POST /pwa/api/clock/out when not clocked in should return 400."""
    _set_pwa_session(pwa_client)
    from unittest.mock import patch
    with patch("execution.pwa_clock.clock_out", return_value={
        "success": False,
        "error": "You're not clocked in.",
    }):
        resp = pwa_client.post("/pwa/api/clock/out")

    import json as _json
    assert resp.status_code == 400
    data = _json.loads(resp.data)
    assert data["success"] is False


def test_pwa_clock_endpoints_require_auth(pwa_client):
    """All clock API endpoints should redirect to login when unauthed."""
    for path, method in [
        ("/pwa/api/clock/status", "GET"),
        ("/pwa/api/clock/in", "POST"),
        ("/pwa/api/clock/out", "POST"),
    ]:
        resp = pwa_client.open(path, method=method)
        # Either redirect to login or 401/403
        assert resp.status_code in (301, 302, 308, 401, 403)


def test_pwa_shell_links_to_clock_screen(pwa_client):
    """Shell should have a link/tile to /pwa/clock."""
    _set_pwa_session(pwa_client)
    resp = pwa_client.get("/pwa/")
    body = resp.data.decode()
    assert "/pwa/clock" in body


# ---------------------------------------------------------------------------
# PWA route screen + job actions (step 4)
# ---------------------------------------------------------------------------

def test_pwa_route_screen_requires_auth(pwa_client):
    """GET /pwa/route without session should redirect to login."""
    resp = pwa_client.get("/pwa/route")
    assert resp.status_code in (301, 302, 308)
    assert "/pwa/login" in resp.headers.get("Location", "")


def test_pwa_route_screen_renders_when_authed(pwa_client):
    """GET /pwa/route with session should render the route screen."""
    _set_pwa_session(pwa_client)
    resp = pwa_client.get("/pwa/route")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Today" in body
    assert "Jesse" in body


def test_pwa_route_data_returns_json(pwa_client):
    """GET /pwa/api/route should return JSON route data."""
    _set_pwa_session(pwa_client)
    from unittest.mock import patch
    fake = {
        "success": True,
        "route": [
            {"job_id": "j1", "sort_order": 0, "customer_name": "Alice",
             "job_type": "pump_out", "estimated_amount": 325,
             "job_start": None, "job_end": None},
        ],
        "current_job_id": None,
        "completed_today": 0,
        "total_jobs": 1,
    }
    with patch("execution.pwa_jobs.get_route", return_value=fake):
        resp = pwa_client.get("/pwa/api/route")

    import json as _json
    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert data["success"] is True
    assert len(data["route"]) == 1
    assert data["route"][0]["customer_name"] == "Alice"


def test_pwa_job_start_calls_module(pwa_client):
    """POST /pwa/api/job/<id>/start should call start_job()."""
    _set_pwa_session(pwa_client)
    from unittest.mock import patch
    with patch("execution.pwa_jobs.start_job", return_value={
        "success": True,
        "job_id": "j1",
        "started_at": "2026-04-09T08:00:00+00:00",
        "customer_name": "Alice",
    }) as mock_start:
        resp = pwa_client.post("/pwa/api/job/j1/start")

    import json as _json
    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert data["success"] is True
    assert data["customer_name"] == "Alice"
    mock_start.assert_called_once_with("client-1", "emp-1", "j1")


def test_pwa_job_done_calls_complete_job(pwa_client):
    """POST /pwa/api/job/<id>/done should call complete_job() and return next job."""
    _set_pwa_session(pwa_client)
    from unittest.mock import patch
    with patch("execution.pwa_jobs.complete_job", return_value={
        "success": True,
        "job_id": "j1",
        "customer_name": "Alice",
        "invoice_id": "inv-1",
        "invoice_amount": 325.0,
        "next_job": {"job_id": "j2", "customer_name": "Bob"},
        "route_complete": False,
    }) as mock_done:
        resp = pwa_client.post("/pwa/api/job/j1/done")

    import json as _json
    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert data["success"] is True
    assert data["invoice_id"] == "inv-1"
    assert data["next_job"]["customer_name"] == "Bob"
    mock_done.assert_called_once_with("client-1", "emp-1", "j1")


def test_pwa_job_status_back_command(pwa_client):
    """POST /pwa/api/job/<id>/status with BACK should call set_status()."""
    _set_pwa_session(pwa_client)
    from unittest.mock import patch
    import json as _json
    with patch("execution.pwa_jobs.set_status", return_value={
        "success": True, "job_id": "j1", "status": "carry_forward",
        "customer_name": "Alice",
    }) as mock_set:
        resp = pwa_client.post(
            "/pwa/api/job/j1/status",
            data=_json.dumps({"command": "BACK"}),
            content_type="application/json",
        )

    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert data["status"] == "carry_forward"
    mock_set.assert_called_once_with("client-1", "emp-1", "j1", "BACK")


def test_pwa_job_status_missing_command_returns_400(pwa_client):
    """POST /pwa/api/job/<id>/status without command should return 400."""
    _set_pwa_session(pwa_client)
    import json as _json
    resp = pwa_client.post(
        "/pwa/api/job/j1/status",
        data=_json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_pwa_job_endpoints_require_auth(pwa_client):
    """All job action endpoints should require auth."""
    for path, method in [
        ("/pwa/api/route", "GET"),
        ("/pwa/api/job/j1/start", "POST"),
        ("/pwa/api/job/j1/done", "POST"),
        ("/pwa/api/job/j1/status", "POST"),
    ]:
        resp = pwa_client.open(path, method=method)
        assert resp.status_code in (301, 302, 308, 400, 401, 403)


def test_pwa_shell_links_to_route_screen(pwa_client):
    """Shell should link to /pwa/route now that step 4 is live."""
    _set_pwa_session(pwa_client)
    resp = pwa_client.get("/pwa/")
    body = resp.data.decode()
    assert "/pwa/route" in body


# ---------------------------------------------------------------------------
# pwa_jobs module unit tests (no Flask)
# ---------------------------------------------------------------------------

def test_pwa_jobs_start_rejects_job_not_in_route():
    """start_job should refuse if job is not in employee's route."""
    from unittest.mock import patch
    with patch("execution.pwa_jobs.get_todays_route", return_value=[
        {"job_id": "other-job", "customer_name": "Alice"},
    ]):
        from execution.pwa_jobs import start_job
        result = start_job("client-1", "emp-1", "wrong-job")
    assert result["success"] is False
    assert "route" in result["error"].lower()


def test_pwa_jobs_start_rejects_already_completed():
    """start_job should refuse if job already has job_end set."""
    from unittest.mock import patch
    with patch("execution.pwa_jobs.get_todays_route", return_value=[
        {"job_id": "j1", "customer_name": "Alice",
         "job_start": "2026-04-09T08:00:00", "job_end": "2026-04-09T09:00:00"},
    ]):
        from execution.pwa_jobs import start_job
        result = start_job("client-1", "emp-1", "j1")
    assert result["success"] is False
    assert "completed" in result["error"].lower()


def test_pwa_jobs_unknown_command_rejected():
    """set_status should reject unknown commands."""
    from execution.pwa_jobs import set_status
    result = set_status("client-1", "emp-1", "j1", "INVALID")
    assert result["success"] is False
    assert "unknown" in result["error"].lower()
