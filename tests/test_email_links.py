# tests/test_email_links.py
# Tests that proposal and invoice emails always use token URLs (public, no login)
# and never use dashboard URLs (which require an active session).
#
# Rule: every outbound email link must go to /p/<token> or /i/<token>
# NEVER to /dashboard/proposal/<id> or /dashboard/invoice/<id>
#
# This bug was caught 2026-03-30: proposal email sent a /dashboard/ link,
# which 404s on any device without an active session (phone, customer device, etc.)
#
# Rule added 2026-03-30: All outbound email links must use token URLs
# (/p/<token>, /i/<token>). Never /dashboard/ URLs.

import re
import pytest

DASHBOARD_PATTERN = re.compile(r'https?://[^/]+/dashboard/(proposal|invoice)/[a-f0-9-]{36}')
TOKEN_PATTERN     = re.compile(r'https?://[^/]+/(p|i)/[A-Za-z0-9]{6,}')


def _extract_links(html: str) -> list[str]:
    """Pull all href URLs out of an HTML email body."""
    return re.findall(r'href=["\']([^"\']+)["\']', html)


# ── T1: proposal email HTML uses token URL, not dashboard URL ─────────────────

def test_proposal_email_uses_token_url_not_dashboard():
    """Proposal emails must link to /p/<token>, not /dashboard/proposal/<id>."""
    from execution.email_send import _build_proposal_html

    token = "abc12345"
    doc_url = f"https://api.bolts11.com/p/{token}"

    html = _build_proposal_html(
        customer_name="Bob Jones",
        business_name="Tim's Excavation",
        proposal_id="9d6ccc0b-33bf-4821-a5bc-13b12ebdedfb",
        line_items=[{"description": "Camera inspection", "amount": 2000.00}],
        subtotal=2000.00,
        tax_amount=0,
        total=2000.00,
        doc_url=doc_url,
    )

    links = _extract_links(html)

    # Must contain at least one token link
    token_links = [l for l in links if TOKEN_PATTERN.search(l)]
    assert token_links, f"No token URL found in proposal email. Links found: {links}"

    # Must NOT contain any dashboard links
    dashboard_links = [l for l in links if DASHBOARD_PATTERN.search(l)]
    assert not dashboard_links, (
        f"Proposal email contains dashboard URL (requires login — breaks on phone/customer device):\n"
        f"{dashboard_links}"
    )


# ── T2: invoice email HTML uses token URL, not dashboard URL ──────────────────

def test_invoice_email_uses_token_url_not_dashboard():
    """Invoice emails must link to /i/<token> or payment link, not /dashboard/invoice/<id>."""
    from execution.email_send import _build_invoice_html

    token = "xyz98765"
    doc_url = f"https://api.bolts11.com/i/{token}"

    html = _build_invoice_html(
        customer_name="Dave Smith",
        business_name="B&B Septic",
        invoice_id="1407bf1c-85e7-4a4c-b246-f4a799d4467f",
        line_items=[{"description": "Excavation and grading", "amount": 10500.00}],
        subtotal=10500.00,
        tax_amount=0,
        total=10500.00,
        doc_url=doc_url,
    )

    links = _extract_links(html)

    dashboard_links = [l for l in links if DASHBOARD_PATTERN.search(l)]
    assert not dashboard_links, (
        f"Invoice email contains dashboard URL (requires login — breaks on phone/customer device):\n"
        f"{dashboard_links}"
    )


# ── T3: dashboard URL passed to builder would be caught ───────────────────────

def test_dashboard_url_in_proposal_is_caught():
    """If someone passes a /dashboard/ URL as doc_url, the test catches it."""
    from execution.email_send import _build_proposal_html

    bad_url = "https://api.bolts11.com/dashboard/proposal/9d6ccc0b-33bf-4821-a5bc-13b12ebdedfb"

    html = _build_proposal_html(
        customer_name="Bob Jones",
        business_name="Tim's Excavation",
        proposal_id="9d6ccc0b-33bf-4821-a5bc-13b12ebdedfb",
        line_items=[{"description": "Camera inspection", "amount": 2000.00}],
        subtotal=2000.00,
        tax_amount=0,
        total=2000.00,
        doc_url=bad_url,
    )

    links = _extract_links(html)
    dashboard_links = [l for l in links if DASHBOARD_PATTERN.search(l)]
    assert dashboard_links, "Expected to detect a dashboard URL but didn't — regex may be wrong"


# ── T4: URL pattern guard — catch any future regressions ─────────────────────

@pytest.mark.parametrize("bad_url", [
    "https://api.bolts11.com/dashboard/proposal/9d6ccc0b-33bf-4821-a5bc-13b12ebdedfb",
    "https://web-production-043dc.up.railway.app/dashboard/invoice/1407bf1c-85e7-4a4c-b246-f4a799d4467f",
])
def test_dashboard_urls_are_detected_by_pattern(bad_url):
    """Sanity check that the DASHBOARD_PATTERN regex catches bad URLs."""
    assert DASHBOARD_PATTERN.search(bad_url), f"Pattern failed to catch bad URL: {bad_url}"


@pytest.mark.parametrize("good_url", [
    "https://api.bolts11.com/p/abc12345",
    "https://api.bolts11.com/i/xyz98765",
])
def test_token_urls_are_detected_by_pattern(good_url):
    """Sanity check that the TOKEN_PATTERN regex matches good URLs."""
    assert TOKEN_PATTERN.search(good_url), f"Pattern failed to match token URL: {good_url}"
