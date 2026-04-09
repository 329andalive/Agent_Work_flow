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


# ---------------------------------------------------------------------------
# PWA new job screen + API (step 5)
# ---------------------------------------------------------------------------

def test_pwa_new_job_screen_requires_auth(pwa_client):
    """GET /pwa/job without session should redirect to login."""
    resp = pwa_client.get("/pwa/job")
    assert resp.status_code in (301, 302, 308)
    assert "/pwa/login" in resp.headers.get("Location", "")


def test_pwa_new_job_screen_renders_when_authed(pwa_client):
    """GET /pwa/job with session should render the new-job screen."""
    _set_pwa_session(pwa_client)
    resp = pwa_client.get("/pwa/job")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "New Job" in body
    assert "Job Description" in body
    assert "Jesse" in body


def test_pwa_new_job_create_success(pwa_client):
    """POST /pwa/api/job/new should return success with review URL."""
    _set_pwa_session(pwa_client)
    # Import the module first so unittest.mock can find the attribute
    import execution.pwa_new_job  # noqa: F401
    from unittest.mock import patch
    import json as _json

    fake_result = {
        "success": True,
        "proposal_id": "prop-1",
        "review_url": "https://app.bolts11.com/doc/review/abc12345?type=proposal",
        "amount": 325.0,
        "customer_id": "cust-1",
        "customer_name": "Alice Smith",
        "customer_created": True,
        "error": None,
    }
    with patch("execution.pwa_new_job.create_proposal_from_pwa", return_value=fake_result) as mock_create:
        resp = pwa_client.post(
            "/pwa/api/job/new",
            data=_json.dumps({
                "description": "pump out 1000 gal tank",
                "customer_name": "Alice Smith",
                "customer_phone": "(207) 555-1234",
                "customer_address": "123 Main St",
            }),
            content_type="application/json",
        )

    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert data["success"] is True
    assert data["proposal_id"] == "prop-1"
    assert data["amount"] == 325.0
    assert data["customer_created"] is True
    assert "review_url" in data

    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["client_id"] == "client-1"
    assert call_kwargs["employee_id"] == "emp-1"
    assert call_kwargs["raw_input"] == "pump out 1000 gal tank"
    assert call_kwargs["customer_name"] == "Alice Smith"


def test_pwa_new_job_create_no_description_returns_400(pwa_client):
    """POST without description should return 400."""
    _set_pwa_session(pwa_client)
    import json as _json
    resp = pwa_client.post(
        "/pwa/api/job/new",
        data=_json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = _json.loads(resp.data)
    assert "description" in data["error"].lower()


def test_pwa_new_job_failure_returns_400(pwa_client):
    """When create_proposal_from_pwa returns failure, the route should return 400."""
    _set_pwa_session(pwa_client)
    import execution.pwa_new_job  # noqa: F401
    from unittest.mock import patch
    import json as _json

    with patch("execution.pwa_new_job.create_proposal_from_pwa", return_value={
        "success": False,
        "error": "Customer phone required for new customers",
    }):
        resp = pwa_client.post(
            "/pwa/api/job/new",
            data=_json.dumps({"description": "test job"}),
            content_type="application/json",
        )

    assert resp.status_code == 400
    data = _json.loads(resp.data)
    assert data["success"] is False
    assert "phone" in data["error"].lower()


def test_pwa_new_job_endpoint_requires_auth(pwa_client):
    """POST /pwa/api/job/new without session should redirect or block."""
    import json as _json
    resp = pwa_client.post(
        "/pwa/api/job/new",
        data=_json.dumps({"description": "test"}),
        content_type="application/json",
    )
    assert resp.status_code in (301, 302, 308, 400, 401, 403)


def test_pwa_shell_links_to_new_job_screen(pwa_client):
    """Shell should link to /pwa/job now that step 5 is live."""
    _set_pwa_session(pwa_client)
    resp = pwa_client.get("/pwa/")
    body = resp.data.decode()
    assert "/pwa/job" in body


# ---------------------------------------------------------------------------
# pwa_new_job module unit tests
# ---------------------------------------------------------------------------

def test_pwa_new_job_normalize_phone():
    """E.164 normalization for various input formats."""
    from execution.pwa_new_job import _normalize_phone
    assert _normalize_phone("(207) 555-1234") == "+12075551234"
    assert _normalize_phone("207-555-1234") == "+12075551234"
    assert _normalize_phone("12075551234") == "+12075551234"
    assert _normalize_phone("+12075551234") == "+12075551234"
    assert _normalize_phone("") == ""
    assert _normalize_phone(None) == ""


def test_pwa_new_job_resolve_existing_customer_by_phone():
    """If phone matches existing customer, should return that record."""
    from unittest.mock import patch
    fake_customer = {"id": "cust-1", "customer_name": "Alice", "customer_phone": "+12075551234"}
    with patch("execution.pwa_new_job.get_customer_by_phone", return_value=fake_customer):
        from execution.pwa_new_job import _resolve_or_create_customer
        result = _resolve_or_create_customer(
            client_id="client-1",
            name="Alice",
            phone="2075551234",
            address="",
            email="",
        )
    assert result["success"] is True
    assert result["customer_id"] == "cust-1"
    assert result["created"] is False
    assert result["customer_phone"] == "+12075551234"


def test_pwa_new_job_creates_new_customer_with_phone():
    """If phone doesn't match existing, should create a new customer."""
    from unittest.mock import patch
    with patch("execution.pwa_new_job.get_customer_by_phone", return_value=None), \
         patch("execution.pwa_new_job.create_customer", return_value="new-cust-id"):
        from execution.pwa_new_job import _resolve_or_create_customer
        result = _resolve_or_create_customer(
            client_id="client-1",
            name="Bob Jones",
            phone="2075559999",
            address="45 Oak Ave",
            email="",
        )
    assert result["success"] is True
    assert result["customer_id"] == "new-cust-id"
    assert result["created"] is True


def test_pwa_new_job_rejects_no_phone_no_match():
    """No phone and no name match should fail with HARD RULE #1 message."""
    from unittest.mock import patch, MagicMock
    mock_sb = MagicMock()
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.ilike.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[])
    mock_sb.table.return_value = mock_table

    with patch("execution.pwa_new_job.get_supabase", return_value=mock_sb):
        from execution.pwa_new_job import _resolve_or_create_customer
        result = _resolve_or_create_customer(
            client_id="client-1",
            name="Unknown Person",
            phone="",
            address="",
            email="",
        )
    assert result["success"] is False
    assert "phone" in result["error"].lower()


# ---------------------------------------------------------------------------
# PWA chat screen + API + agent (step 6a)
# ---------------------------------------------------------------------------

def test_pwa_chat_screen_requires_auth(pwa_client):
    """GET /pwa/chat without session should redirect to login."""
    resp = pwa_client.get("/pwa/chat")
    assert resp.status_code in (301, 302, 308)
    assert "/pwa/login" in resp.headers.get("Location", "")


def test_pwa_chat_screen_renders_when_authed(pwa_client):
    """GET /pwa/chat with session should render the chat screen."""
    _set_pwa_session(pwa_client)
    resp = pwa_client.get("/pwa/chat")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Jesse" in body
    assert "Message" in body  # placeholder text in textarea


def test_pwa_chat_history_returns_messages(pwa_client):
    """GET /pwa/api/chat/messages should return ordered history."""
    _set_pwa_session(pwa_client)
    import execution.pwa_chat_messages  # noqa: F401
    from unittest.mock import patch
    fake_history = [
        {"id": "m1", "role": "user", "content": "hi", "metadata": {}, "created_at": "2026-04-09T08:00:00+00:00"},
        {"id": "m2", "role": "assistant", "content": "hey jesse", "metadata": {}, "created_at": "2026-04-09T08:00:01+00:00"},
    ]
    with patch("execution.pwa_chat_messages.get_active_session_id", return_value="sess-1"), \
         patch("execution.pwa_chat_messages.get_history", return_value=fake_history):
        resp = pwa_client.get("/pwa/api/chat/messages")

    import json as _json
    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert data["success"] is True
    assert data["session_id"] == "sess-1"
    assert len(data["messages"]) == 2
    assert data["messages"][0]["role"] == "user"


def test_pwa_chat_send_saves_user_and_assistant(pwa_client):
    """POST /pwa/api/chat/send should save user msg, call agent, save reply."""
    _set_pwa_session(pwa_client)
    import execution.pwa_chat_messages  # noqa: F401
    import execution.pwa_chat  # noqa: F401
    from unittest.mock import patch, MagicMock
    import json as _json

    fake_chat_result = {
        "success": True,
        "reply": "Got it, jesse — three jobs lined up for you.",
        "model": "haiku",
        "system_prompt_chars": 850,
        "error": None,
    }

    # Mock supabase for the business_name lookup
    mock_sb = MagicMock()
    mock_table = MagicMock()
    for m in ("select", "eq", "limit", "execute"):
        getattr(mock_table, m).return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[{"business_name": "Test Trades Co"}])
    mock_sb.table.return_value = mock_table

    with patch("execution.pwa_chat_messages.get_active_session_id", return_value="sess-1"), \
         patch("execution.pwa_chat_messages.get_history", return_value=[]), \
         patch("execution.pwa_chat_messages.save_message", return_value="msg-id") as mock_save, \
         patch("execution.pwa_chat.chat", return_value=fake_chat_result), \
         patch("execution.db_connection.get_client", return_value=mock_sb):
        resp = pwa_client.post(
            "/pwa/api/chat/send",
            data=_json.dumps({"message": "what jobs do i have today"}),
            content_type="application/json",
        )

    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert data["success"] is True
    assert "three jobs" in data["reply"]
    assert data["session_id"] == "sess-1"

    # User msg + assistant reply both saved
    assert mock_save.call_count == 2
    calls = [c.args for c in mock_save.call_args_list]
    roles = [c[3] for c in calls]
    assert "user" in roles
    assert "assistant" in roles


def test_pwa_chat_send_empty_message_returns_400(pwa_client):
    """POST with empty message should return 400."""
    _set_pwa_session(pwa_client)
    import json as _json
    resp = pwa_client.post(
        "/pwa/api/chat/send",
        data=_json.dumps({"message": "   "}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_pwa_chat_endpoints_require_auth(pwa_client):
    """Both chat API endpoints should require auth."""
    for path, method in [
        ("/pwa/api/chat/messages", "GET"),
        ("/pwa/api/chat/send", "POST"),
    ]:
        resp = pwa_client.open(path, method=method)
        assert resp.status_code in (301, 302, 308, 400, 401, 403)


def test_pwa_shell_links_to_chat_screen(pwa_client):
    """Shell should link to /pwa/chat now that step 6a is live."""
    _set_pwa_session(pwa_client)
    resp = pwa_client.get("/pwa/")
    body = resp.data.decode()
    assert "/pwa/chat" in body


# ---------------------------------------------------------------------------
# pwa_chat module unit tests (no Flask)
# ---------------------------------------------------------------------------

def test_pwa_chat_system_prompt_under_token_budget():
    """The system prompt MUST stay under the 500-token (~2000-char) target."""
    from execution.pwa_chat import (
        _build_system_prompt, _build_route_summary, SYSTEM_PROMPT_CHAR_TARGET,
    )

    # Realistic 5-job route — typical day
    route = [
        {"customer_name": "Alice Smith", "job_type": "pump_out", "estimated_amount": 325, "job_start": "x", "job_end": "y"},
        {"customer_name": "Bob Jones", "job_type": "inspection", "estimated_amount": 250, "job_start": "x", "job_end": None},
        {"customer_name": "Carol Duggan", "job_type": "repair", "estimated_amount": 500, "job_start": None, "job_end": None},
        {"customer_name": "Dan Reynolds", "job_type": "cleanout", "estimated_amount": 200, "job_start": None, "job_end": None},
        {"customer_name": "Eve Anderson", "job_type": "install", "estimated_amount": 1200, "job_start": None, "job_end": None},
    ]
    summary = _build_route_summary(route)
    prompt = _build_system_prompt("Jesse", "field_tech", "B&B Septic", summary)

    char_count = len(prompt)
    print(f"System prompt: {char_count} chars (~{char_count // 4} tokens)")
    assert char_count < SYSTEM_PROMPT_CHAR_TARGET, (
        f"System prompt is {char_count} chars (~{char_count // 4} tokens), "
        f"exceeds {SYSTEM_PROMPT_CHAR_TARGET}-char budget"
    )


def test_pwa_chat_route_summary_empty_route():
    """Empty route should produce a brief summary, not an error."""
    from execution.pwa_chat import _build_route_summary
    summary = _build_route_summary([])
    assert "empty" in summary.lower() or "no jobs" in summary.lower()
    assert len(summary) < 200  # tight even for empty case


def test_pwa_chat_route_summary_marks_current_and_done():
    """Route summary should mark done (✓), current (→), and pending jobs."""
    from execution.pwa_chat import _build_route_summary
    route = [
        {"customer_name": "Alice", "job_type": "pump_out", "job_start": "x", "job_end": "y"},
        {"customer_name": "Bob", "job_type": "inspection", "job_start": "x", "job_end": None},
        {"customer_name": "Carol", "job_type": "repair", "job_start": None, "job_end": None},
    ]
    summary = _build_route_summary(route)
    assert "✓" in summary  # done marker on Alice
    assert "→" in summary  # current marker on Bob
    assert "current" in summary.lower()
    assert "Alice" in summary
    assert "Bob" in summary
    assert "Carol" in summary


def test_pwa_chat_returns_dict_with_reply_key():
    """The chat() entry point must return a dict (not a string) so 6b can add 'action'."""
    from unittest.mock import patch
    with patch("execution.pwa_chat.call_claude", return_value="Sure thing, Jesse."), \
         patch("execution.pwa_chat._route_summary_for_employee", return_value="Today's route: empty."):
        from execution.pwa_chat import chat
        result = chat(
            client_id="c1", employee_id="e1", employee_name="Jesse",
            employee_role="field_tech", business_name="Test",
            user_message="hi", history=[],
        )
    assert isinstance(result, dict)
    assert "reply" in result
    assert "success" in result


def test_pwa_chat_strips_assistant_prefix():
    """If Claude echoes 'Assistant: ...' we strip the prefix."""
    from unittest.mock import patch
    with patch("execution.pwa_chat.call_claude", return_value="Assistant: Hi Jesse, how can I help?"), \
         patch("execution.pwa_chat._route_summary_for_employee", return_value="Today: empty."):
        from execution.pwa_chat import chat
        result = chat(
            client_id="c1", employee_id="e1", employee_name="Jesse",
            employee_role="field_tech", business_name="Test",
            user_message="hi", history=[],
        )
    assert result["success"] is True
    assert not result["reply"].lower().startswith("assistant:")
    assert "Hi Jesse" in result["reply"]


def test_pwa_chat_empty_response_returns_failure():
    """Empty Claude response should return success=False with friendly message."""
    from unittest.mock import patch
    with patch("execution.pwa_chat.call_claude", return_value=""), \
         patch("execution.pwa_chat._route_summary_for_employee", return_value="Today: empty."):
        from execution.pwa_chat import chat
        result = chat(
            client_id="c1", employee_id="e1", employee_name="Jesse",
            employee_role="field_tech", business_name="Test",
            user_message="hi", history=[],
        )
    assert result["success"] is False
    assert result["reply"]  # still has a fallback message for the UI


def test_pwa_chat_messages_save_rejects_invalid_role():
    """save_message should refuse roles other than user/assistant."""
    from execution.pwa_chat_messages import save_message
    result = save_message("c1", "e1", "s1", "system", "should fail")
    assert result is None


def test_pwa_chat_messages_save_rejects_empty_content():
    """save_message should refuse empty/whitespace content."""
    from execution.pwa_chat_messages import save_message
    result = save_message("c1", "e1", "s1", "user", "   ")
    assert result is None


# ---------------------------------------------------------------------------
# Step 6b — action chips, JSON parsing, server-side decoration
# ---------------------------------------------------------------------------

def test_pwa_chat_parse_response_plain_text():
    """Plain (non-JSON) Claude response should become a reply with no action."""
    from execution.pwa_chat import _parse_claude_response
    out = _parse_claude_response("Hi Jesse, three jobs lined up today.")
    assert out["action"] is None
    assert "Jesse" in out["reply"]


def test_pwa_chat_parse_response_json_reply_only():
    """JSON with reply only → reply set, action None."""
    from execution.pwa_chat import _parse_claude_response
    out = _parse_claude_response('{"reply": "All clear, boss."}')
    assert out["reply"] == "All clear, boss."
    assert out["action"] is None


def test_pwa_chat_parse_response_json_with_action():
    """JSON with reply + action → both extracted."""
    from execution.pwa_chat import _parse_claude_response
    raw = (
        '{"reply": "I can draft that estimate for Alice.",'
        ' "action": {"type": "create_proposal", '
        '"params": {"description": "pump out 1000 gal", "customer_name": "Alice"}}}'
    )
    out = _parse_claude_response(raw)
    assert "Alice" in out["reply"]
    assert out["action"]["type"] == "create_proposal"
    assert out["action"]["params"]["description"] == "pump out 1000 gal"


def test_pwa_chat_parse_response_strips_markdown_fences():
    """Claude sometimes wraps JSON in ```json fences. Strip them."""
    from execution.pwa_chat import _parse_claude_response
    raw = '```json\n{"reply": "Got it."}\n```'
    out = _parse_claude_response(raw)
    assert out["reply"] == "Got it."
    assert out["action"] is None


def test_pwa_chat_parse_response_drops_unknown_action_type():
    """Unknown action types should be dropped — keep the reply only."""
    from execution.pwa_chat import _parse_claude_response
    raw = '{"reply": "Sure.", "action": {"type": "delete_database", "params": {}}}'
    out = _parse_claude_response(raw)
    assert out["reply"] == "Sure."
    assert out["action"] is None


def test_pwa_chat_parse_response_strips_assistant_prefix_in_json():
    """Leading 'Assistant:' prefix should be stripped before JSON parse."""
    from execution.pwa_chat import _parse_claude_response
    raw = 'Assistant: {"reply": "Hey"}'
    out = _parse_claude_response(raw)
    assert out["reply"] == "Hey"


def test_pwa_chat_decorate_create_proposal_builds_chip():
    """create_proposal with a description should produce a chip targeting /pwa/api/job/new."""
    from execution.pwa_chat_actions import decorate_action
    chip = decorate_action(
        client_id="c1", employee_id="e1",
        action_type="create_proposal",
        params={"description": "pump out tank", "customer_name": "Alice", "amount": 325},
    )
    assert chip is not None
    assert chip["type"] == "create_proposal"
    assert chip["endpoint"] == "/pwa/api/job/new"
    assert chip["method"] == "POST"
    assert "325" in chip["label"]
    assert chip["params"]["description"] == "pump out tank"


def test_pwa_chat_decorate_create_proposal_requires_description():
    """No description → no chip."""
    from execution.pwa_chat_actions import decorate_action
    chip = decorate_action(
        client_id="c1", employee_id="e1",
        action_type="create_proposal",
        params={"customer_name": "Alice"},
    )
    assert chip is None


def test_pwa_chat_decorate_mark_job_done_resolves_customer_name():
    """mark_job_done should fuzzy-match customer name to a job_id from today's route."""
    from unittest.mock import patch
    import execution.pwa_chat_actions  # noqa: F401

    fake_route = [
        {"job_id": "job-aaa", "customer_name": "Alice Smith"},
        {"job_id": "job-bbb", "customer_name": "Bob Jones"},
    ]
    with patch("execution.dispatch_chain.get_todays_route", return_value=fake_route):
        from execution.pwa_chat_actions import decorate_action
        chip = decorate_action(
            client_id="c1", employee_id="e1",
            action_type="mark_job_done",
            params={"customer_name": "alice"},
        )
    assert chip is not None
    assert chip["type"] == "mark_job_done"
    assert chip["params"]["job_id"] == "job-aaa"
    assert chip["endpoint"] == "/pwa/api/job/job-aaa/done"
    assert "Alice" in chip["label"]


def test_pwa_chat_decorate_mark_job_done_no_match_returns_none():
    """If no job in the route matches, return None — drop the chip."""
    from unittest.mock import patch
    import execution.pwa_chat_actions  # noqa: F401

    fake_route = [{"job_id": "job-aaa", "customer_name": "Alice Smith"}]
    with patch("execution.dispatch_chain.get_todays_route", return_value=fake_route):
        from execution.pwa_chat_actions import decorate_action
        chip = decorate_action(
            client_id="c1", employee_id="e1",
            action_type="mark_job_done",
            params={"customer_name": "Zelda"},
        )
    assert chip is None


def test_pwa_chat_decorate_start_job_resolves_and_targets_start_endpoint():
    """start_job should resolve and target /pwa/api/job/<id>/start."""
    from unittest.mock import patch
    import execution.pwa_chat_actions  # noqa: F401

    fake_route = [{"job_id": "job-xyz", "customer_name": "Carol Duggan"}]
    with patch("execution.dispatch_chain.get_todays_route", return_value=fake_route):
        from execution.pwa_chat_actions import decorate_action
        chip = decorate_action(
            client_id="c1", employee_id="e1",
            action_type="start_job",
            params={"customer_name": "Carol"},
        )
    assert chip is not None
    assert chip["endpoint"] == "/pwa/api/job/job-xyz/start"


def test_pwa_chat_decorate_clock_in_no_params_needed():
    """clock_in/clock_out should always produce a chip (no params needed)."""
    from execution.pwa_chat_actions import decorate_action
    chip_in = decorate_action("c1", "e1", "clock_in", {})
    chip_out = decorate_action("c1", "e1", "clock_out", {})
    assert chip_in["endpoint"] == "/pwa/api/clock/in"
    assert chip_out["endpoint"] == "/pwa/api/clock/out"


def test_pwa_chat_decorate_unknown_action_returns_none():
    """Unknown action types should be rejected at the validator boundary too."""
    from execution.pwa_chat_actions import decorate_action
    chip = decorate_action("c1", "e1", "delete_everything", {"x": 1})
    assert chip is None


def test_pwa_chat_returns_action_when_claude_emits_one():
    """End-to-end: chat() should surface a decorated action when Claude returns JSON."""
    from unittest.mock import patch
    import execution.pwa_chat  # noqa: F401

    json_reply = (
        '{"reply": "Got it — clocking you in.",'
        ' "action": {"type": "clock_in", "params": {}}}'
    )

    with patch("execution.pwa_chat.call_claude", return_value=json_reply), \
         patch("execution.pwa_chat._route_summary_for_employee", return_value="Today: empty."):
        from execution.pwa_chat import chat
        result = chat(
            client_id="c1", employee_id="e1", employee_name="Jesse",
            employee_role="field_tech", business_name="Test",
            user_message="clock me in", history=[],
        )

    assert result["success"] is True
    assert result["action"] is not None
    assert result["action"]["type"] == "clock_in"
    assert result["action"]["endpoint"] == "/pwa/api/clock/in"


def test_pwa_chat_no_action_when_plain_text():
    """If Claude returns plain text, action should be None."""
    from unittest.mock import patch
    import execution.pwa_chat  # noqa: F401

    with patch("execution.pwa_chat.call_claude", return_value="No action needed, boss."), \
         patch("execution.pwa_chat._route_summary_for_employee", return_value="Today: empty."):
        from execution.pwa_chat import chat
        result = chat(
            client_id="c1", employee_id="e1", employee_name="Jesse",
            employee_role="field_tech", business_name="Test",
            user_message="hi", history=[],
        )
    assert result["success"] is True
    assert result["action"] is None
    assert "boss" in result["reply"]


def test_pwa_chat_send_persists_action_in_metadata(pwa_client):
    """The chat send route should save the action under metadata.action."""
    _set_pwa_session(pwa_client)
    import execution.pwa_chat_messages  # noqa: F401
    import execution.pwa_chat  # noqa: F401
    from unittest.mock import patch, MagicMock
    import json as _json

    fake_chat_result = {
        "success": True,
        "reply": "Clocking you in now.",
        "action": {
            "type": "clock_in",
            "label": "Clock in",
            "params": {},
            "endpoint": "/pwa/api/clock/in",
            "method": "POST",
        },
        "model": "haiku",
        "system_prompt_chars": 900,
        "error": None,
    }

    mock_sb = MagicMock()
    mock_table = MagicMock()
    for m in ("select", "eq", "limit", "execute"):
        getattr(mock_table, m).return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[{"business_name": "Test Co"}])
    mock_sb.table.return_value = mock_table

    with patch("execution.pwa_chat_messages.get_active_session_id", return_value="sess-1"), \
         patch("execution.pwa_chat_messages.get_history", return_value=[]), \
         patch("execution.pwa_chat_messages.save_message", return_value="msg-id") as mock_save, \
         patch("execution.pwa_chat.chat", return_value=fake_chat_result), \
         patch("execution.db_connection.get_client", return_value=mock_sb):
        resp = pwa_client.post(
            "/pwa/api/chat/send",
            data=_json.dumps({"message": "clock me in"}),
            content_type="application/json",
        )

    assert resp.status_code == 200
    data = _json.loads(resp.data)
    assert data["success"] is True
    assert data["action"]["type"] == "clock_in"

    # Find the assistant save call and verify metadata.action survived
    assistant_call = None
    for call in mock_save.call_args_list:
        if call.args[3] == "assistant":
            assistant_call = call
            break
    assert assistant_call is not None
    meta = assistant_call.kwargs.get("metadata") or {}
    assert meta.get("action", {}).get("type") == "clock_in"


def test_pwa_chat_html_renders_chip_and_voice_markup():
    """Chat template should ship the chip + mic button hooks."""
    # Cheap render-side check that the 6b pieces are wired into the template.
    from pathlib import Path
    html = Path(__file__).parent.parent / "templates" / "pwa" / "chat.html"
    body = html.read_text()
    # Action chip CSS class + render function
    assert "renderChip" in body
    assert ".chip" in body
    # Voice input button + Web Speech wiring
    assert "mic-btn" in body
    assert "SpeechRecognition" in body
