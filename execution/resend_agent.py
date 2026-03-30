"""
execution/resend_agent.py — Resend email delivery for Bolts11
FIXED: welcome email shows formatted phone (no +1), includes temp PIN 5555 instructions
"""

import os
import json
import requests
from datetime import datetime

RESEND_API_KEY = (
    os.environ.get("RESEND_API_KEY") or
    os.environ.get("re_send") or
    ""
)
RESEND_API_URL = "https://api.resend.com/emails"
FROM_ADDRESS   = "Bolts11 <noreply@bolts11.com>"
SUPPORT_EMAIL  = "support@bolts11.com"


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fmt_phone(phone: str) -> str:
    """Format +12075550100 → (207) 555-0100 for display in emails."""
    digits = ''.join(c for c in (phone or '') if c.isdigit())
    if digits.startswith('1'):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return phone


def _send(to: list, subject: str, html: str, reply_to: str = None) -> dict:
    if not RESEND_API_KEY:
        print(f"[{_ts()}] WARN resend_agent: RESEND_API_KEY not set — email skipped")
        return {"success": False, "error": "RESEND_API_KEY not configured"}

    payload = {"from": FROM_ADDRESS, "to": to, "subject": subject, "html": html}
    if reply_to:
        payload["reply_to"] = reply_to

    try:
        resp = requests.post(
            RESEND_API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            timeout=15,
        )
        data = resp.json()
        if resp.status_code in (200, 201):
            print(f"[{_ts()}] INFO resend_agent: Email sent → {to} | id={data.get('id')}")
            return {"success": True, "id": data.get("id")}
        else:
            print(f"[{_ts()}] ERROR resend_agent: HTTP {resp.status_code} → {data}")
            return {"success": False, "error": f"HTTP {resp.status_code}: {data}"}
    except Exception as e:
        print(f"[{_ts()}] ERROR resend_agent: {e}")
        return {"success": False, "error": str(e)}


def _wrap(body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body{{margin:0;padding:0;background:#f3f4f6;font-family:'Inter',system-ui,Arial,sans-serif;color:#374151}}
  .wrap{{max-width:580px;margin:32px auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08)}}
  .header{{background:#132038;padding:28px 36px}}
  .logo{{font-size:20px;font-weight:800;color:#ffffff}}
  .logo span{{color:#f59e0b}}
  .body{{padding:36px}}
  h1{{font-size:22px;font-weight:700;color:#1a2e4a;margin:0 0 12px}}
  p{{font-size:15px;line-height:1.7;color:#374151;margin:0 0 16px}}
  .btn{{display:inline-block;padding:13px 28px;background:#f59e0b;color:#132038;font-weight:700;font-size:15px;border-radius:8px;text-decoration:none;margin:8px 0 20px}}
  .highlight{{background:#fef3c7;border-left:3px solid #f59e0b;padding:14px 18px;border-radius:0 8px 8px 0;margin:16px 0}}
  .highlight p{{margin:0;font-size:14px;color:#92400e}}
  .steps{{background:#f9fafb;border-radius:8px;padding:16px 20px;margin:16px 0}}
  .steps p{{margin:0;font-size:14px;line-height:2;color:#374151}}
  hr{{border:none;border-top:1px solid #e5e7eb;margin:24px 0}}
  .small{{font-size:13px;color:#9ca3af;line-height:1.6}}
  .footer{{background:#f9fafb;padding:20px 36px;border-top:1px solid #e5e7eb}}
  .footer p{{font-size:12px;color:#9ca3af;margin:0;line-height:1.6}}
  .footer a{{color:#9ca3af}}
</style>
</head>
<body>
<div class="wrap">
  <div class="header"><div class="logo"><span>&#9889;</span> Bolts11</div></div>
  <div class="body">{body_html}</div>
  <div class="footer">
    <p>Bolts11 &mdash; The AI Back Office Suite<br>
    Questions? <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a> &middot;
    <a href="https://bolts11.com">bolts11.com</a></p>
  </div>
</div>
</body>
</html>"""


# ── 1. Access request confirmation ───────────────────────────────────────────

def send_access_request_confirmation(name: str, email: str, business_type: str) -> dict:
    first   = name.split()[0] if name else "there"
    subject = "We got your Bolts11 request — you'll hear from us soon"
    body    = f"""
<h1>Hey {first} — we're on it.</h1>
<p>Thanks for reaching out about Bolts11. We received your request for <strong>{business_type}</strong> and we'll be in touch personally within one business day.</p>
<div class="highlight">
  <p><strong>What happens next:</strong> Someone from our team will reach out to your phone or email to walk you through setup. It takes about 10 minutes and you'll be live the same day.</p>
</div>
<p>In the meantime, if you have any questions just reply to this email or reach us at <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a>.</p>
<p>Talk soon,<br><strong>The Bolts11 Team</strong></p>
<hr>
<p class="small">You're receiving this because you submitted a request at bolts11.com.</p>
"""
    return _send(to=[email], subject=subject, html=_wrap(body), reply_to=SUPPORT_EMAIL)


# ── 2. New lead alert ─────────────────────────────────────────────────────────

def send_access_request_alert(name: str, email: str, phone: str, business_type: str) -> dict:
    subject = f"New access request — {name} ({business_type})"
    body    = f"""
<h1>New access request</h1>
<p>Someone just filled out the early access form on bolts11.com.</p>
<div class="highlight">
  <p>
    <strong>Name:</strong> {name}<br>
    <strong>Business:</strong> {business_type}<br>
    <strong>Phone:</strong> {phone}<br>
    <strong>Email:</strong> {email}
  </p>
</div>
<p>Reply to this email to reach them, or text {phone}.</p>
<p class="small">Submitted at {_ts()} UTC</p>
"""
    return _send(to=[SUPPORT_EMAIL], subject=subject, html=_wrap(body), reply_to=email)


# ── 3. Welcome / onboarding email ─────────────────────────────────────────────

def send_welcome_email(
    name: str,
    email: str,
    business_name: str,
    phone: str,
    dashboard_url: str = "https://web-production-043dc.up.railway.app/dashboard/",
    signin_url: str = "https://bolts11.com/signin.html",
) -> dict:
    first         = name.split()[0] if name else "there"
    display_phone = _fmt_phone(phone)   # (207) 555-0100 — no +1
    subject       = f"Welcome to Bolts11, {first} — you're live"
    body          = f"""
<h1>Welcome aboard, {first}.</h1>
<p><strong>{business_name}</strong> is now live on Bolts11. Your back office is ready to go.</p>

<div class="highlight">
  <p>
    <strong>Sign in with:</strong> {display_phone}<br>
    <strong>First time?</strong> Enter <strong>5555</strong> as your temporary PIN — you'll be prompted to create your own PIN right away.
  </p>
</div>

<p><a href="{signin_url}" class="btn">Sign In to Your Dashboard &rarr;</a></p>

<div class="steps">
  <p>
    1. Go to <a href="{signin_url}">bolts11.com/signin.html</a><br>
    2. Enter your phone number: <strong>{display_phone}</strong><br>
    3. Enter temporary PIN: <strong>5555</strong><br>
    4. You'll be asked to create and confirm your own 4-digit PIN<br>
    5. You're in — your dashboard is ready
  </p>
</div>

<p>Any questions — just reply to this email. We're here.</p>
<p>— The Bolts11 Team</p>
"""
    return _send(to=[email], subject=subject, html=_wrap(body), reply_to=SUPPORT_EMAIL)


# ── 4. Invoice / proposal email delivery ──────────────────────────────────────

def send_document_email(
    to_email: str,
    to_name: str,
    doc_type: str,
    doc_number: str,
    business_name: str,
    amount: float,
    view_url: str,
    pay_url: str = None,
    due_date: str = None,
) -> dict:
    label   = "Invoice" if doc_type == "invoice" else "Estimate"
    subject = f"{label} {doc_number} from {business_name} — ${amount:,.2f}"

    pay_block = ""
    if pay_url and doc_type == "invoice":
        pay_block = f'<p><a href="{pay_url}" class="btn">Pay Now — ${amount:,.2f} &rarr;</a></p>'

    body = f"""
<h1>{label} from {business_name}</h1>
<p>Hi {to_name},</p>
<p>Please find your {label.lower()} below.</p>
<div class="highlight">
  <p>
    <strong>{label} #:</strong> {doc_number}<br>
    <strong>Amount:</strong> ${amount:,.2f}
    {f'<br><strong>Due:</strong> {due_date}' if due_date else ''}
  </p>
</div>
{pay_block}
<p><a href="{view_url}">View full {label.lower()} online &rarr;</a></p>
<hr>
<p class="small">This {label.lower()} was sent on behalf of {business_name} via Bolts11.</p>
"""
    return _send(to=[to_email], subject=subject, html=_wrap(body), reply_to=SUPPORT_EMAIL)
