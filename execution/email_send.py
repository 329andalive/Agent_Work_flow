"""
email_send.py — Send invoices and proposals by email via SendGrid.

Used as a bridge while 10DLC SMS approval is pending.
Falls back gracefully if SENDGRID_API_KEY is not configured.

Usage:
    from execution.email_send import send_invoice_email, send_proposal_email
    result = send_invoice_email(
        to_email="customer@example.com",
        to_name="Beverly Whitaker",
        from_name="B&B Septic",
        invoice_id="abc123",
        customer_name="Beverly Whitaker",
        business_name="B&B Septic",
        line_items=[{"description": "Pump-out", "amount": 325.00}],
        subtotal=325.00,
        tax_amount=0.0,
        total=325.00,
        payment_link_url="https://sandbox.square.link/u/abc123",
        doc_url="https://api.bolts11.com/dashboard/invoice/abc123",
    )
"""

import os
from datetime import datetime


def _sendgrid_available() -> bool:
    return bool(os.environ.get("SENDGRID_API_KEY"))


def _timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _build_invoice_html(
    customer_name: str,
    business_name: str,
    invoice_id: str,
    line_items: list,
    subtotal: float,
    tax_amount: float,
    total: float,
    payment_link_url: str = None,
    doc_url: str = None,
) -> str:
    """Build a clean HTML email for an invoice."""

    items_html = ""
    for li in line_items:
        desc = li.get("description") or li.get("name") or "Service"
        amt = float(li.get("total") or li.get("amount") or 0)
        items_html += (
            '<tr>'
            f'<td style="padding:8px 0;font-size:14px;color:#1a2e4a;border-bottom:1px solid #f3f4f6">{desc}</td>'
            f'<td style="padding:8px 0;font-size:14px;color:#1a2e4a;text-align:right;font-family:monospace;border-bottom:1px solid #f3f4f6">${amt:.2f}</td>'
            '</tr>'
        )

    tax_row = ""
    if tax_amount > 0:
        tax_row = (
            '<tr>'
            '<td style="padding:4px 0;font-size:13px;color:#6b7280">Tax</td>'
            f'<td style="padding:4px 0;font-size:13px;color:#6b7280;text-align:right;font-family:monospace">${tax_amount:.2f}</td>'
            '</tr>'
        )

    pay_button = ""
    if payment_link_url:
        pay_button = (
            '<div style="text-align:center;margin:24px 0">'
            f'<a href="{payment_link_url}" '
            'style="background:#f59e0b;color:#1a2e4a;font-weight:700;font-size:16px;'
            'padding:14px 32px;border-radius:8px;text-decoration:none;'
            f'display:inline-block;letter-spacing:0.03em">PAY NOW &mdash; ${total:.2f}</a>'
            '</div>'
        )

    view_link = ""
    if doc_url:
        view_link = (
            '<p style="text-align:center;font-size:12px;color:#9ca3af;margin-top:16px">'
            f'<a href="{doc_url}" style="color:#2563eb">View invoice online</a></p>'
        )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
  <div style="max-width:560px;margin:32px auto;background:#ffffff;border-radius:12px;border:1px solid #e5e7eb;overflow:hidden">
    <div style="background:#1a2e4a;padding:24px;text-align:center">
      <div style="color:#f59e0b;font-size:11px;font-family:monospace;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">Invoice</div>
      <div style="color:#ffffff;font-size:22px;font-weight:700">{business_name}</div>
      <div style="color:#94a3b8;font-size:11px;font-family:monospace;margin-top:4px">#{invoice_id[:8].upper()}</div>
    </div>
    <div style="padding:24px">
      <p style="font-size:14px;color:#374151;margin:0 0 20px 0">
        Hi {customer_name},<br><br>Here is your invoice from {business_name}.</p>
      <table style="width:100%;border-collapse:collapse">
        {items_html}
        <tr>
          <td style="padding:4px 0;font-size:13px;color:#6b7280">Subtotal</td>
          <td style="padding:4px 0;font-size:13px;color:#6b7280;text-align:right;font-family:monospace">${subtotal:.2f}</td>
        </tr>
        {tax_row}
        <tr style="border-top:2px solid #1a2e4a">
          <td style="padding:10px 0 4px;font-size:16px;font-weight:700;color:#1a2e4a">Total Due</td>
          <td style="padding:10px 0 4px;font-size:16px;font-weight:700;color:#1a2e4a;text-align:right;font-family:monospace">${total:.2f}</td>
        </tr>
      </table>
      {pay_button}
      {view_link}
    </div>
    <div style="background:#f9fafb;padding:16px 24px;text-align:center;border-top:1px solid #e5e7eb">
      <p style="font-size:11px;color:#9ca3af;margin:0">{business_name} &middot; Sent via Bolts11</p>
    </div>
  </div>
</body>
</html>"""


def _build_proposal_html(
    customer_name: str,
    business_name: str,
    proposal_id: str,
    line_items: list,
    subtotal: float,
    tax_amount: float,
    total: float,
    doc_url: str = None,
) -> str:
    """Build a clean HTML email for a proposal/estimate."""

    items_html = ""
    for li in line_items:
        desc = li.get("description") or li.get("name") or "Service"
        amt = float(li.get("total") or li.get("amount") or 0)
        items_html += (
            '<tr>'
            f'<td style="padding:8px 0;font-size:14px;color:#1a2e4a;border-bottom:1px solid #f3f4f6">{desc}</td>'
            f'<td style="padding:8px 0;font-size:14px;color:#1a2e4a;text-align:right;font-family:monospace;border-bottom:1px solid #f3f4f6">${amt:.2f}</td>'
            '</tr>'
        )

    tax_row = ""
    if tax_amount > 0:
        tax_row = (
            '<tr>'
            '<td style="padding:4px 0;font-size:13px;color:#6b7280">Tax</td>'
            f'<td style="padding:4px 0;font-size:13px;color:#6b7280;text-align:right;font-family:monospace">${tax_amount:.2f}</td>'
            '</tr>'
        )

    accept_button = ""
    if doc_url:
        accept_button = (
            '<div style="text-align:center;margin:24px 0">'
            f'<a href="{doc_url}" '
            'style="background:#2563eb;color:#ffffff;font-weight:700;font-size:16px;'
            'padding:14px 32px;border-radius:8px;text-decoration:none;'
            'display:inline-block">View &amp; Accept Estimate</a>'
            '</div>'
        )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
  <div style="max-width:560px;margin:32px auto;background:#ffffff;border-radius:12px;border:1px solid #e5e7eb;overflow:hidden">
    <div style="background:#1a2e4a;padding:24px;text-align:center">
      <div style="color:#f59e0b;font-size:11px;font-family:monospace;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">Estimate</div>
      <div style="color:#ffffff;font-size:22px;font-weight:700">{business_name}</div>
      <div style="color:#94a3b8;font-size:11px;font-family:monospace;margin-top:4px">#{proposal_id[:8].upper()}</div>
    </div>
    <div style="padding:24px">
      <p style="font-size:14px;color:#374151;margin:0 0 20px 0">
        Hi {customer_name},<br><br>Here is your estimate from {business_name}.
        Please review and let us know if you'd like to move forward.</p>
      <table style="width:100%;border-collapse:collapse">
        {items_html}
        <tr>
          <td style="padding:4px 0;font-size:13px;color:#6b7280">Subtotal</td>
          <td style="padding:4px 0;font-size:13px;color:#6b7280;text-align:right;font-family:monospace">${subtotal:.2f}</td>
        </tr>
        {tax_row}
        <tr style="border-top:2px solid #1a2e4a">
          <td style="padding:10px 0 4px;font-size:16px;font-weight:700;color:#1a2e4a">Estimate Total</td>
          <td style="padding:10px 0 4px;font-size:16px;font-weight:700;color:#1a2e4a;text-align:right;font-family:monospace">${total:.2f}</td>
        </tr>
      </table>
      {accept_button}
    </div>
    <div style="background:#f9fafb;padding:16px 24px;text-align:center;border-top:1px solid #e5e7eb">
      <p style="font-size:11px;color:#9ca3af;margin:0">{business_name} &middot; Sent via Bolts11</p>
    </div>
  </div>
</body>
</html>"""


def send_invoice_email(
    to_email: str,
    to_name: str,
    from_name: str,
    invoice_id: str,
    customer_name: str,
    business_name: str,
    line_items: list,
    subtotal: float,
    tax_amount: float,
    total: float,
    payment_link_url: str = None,
    doc_url: str = None,
    from_email: str = None,
) -> dict:
    """Send an invoice by email via SendGrid."""
    if not _sendgrid_available():
        print(f"[{_timestamp()}] WARN email_send: SENDGRID_API_KEY not set — email not sent")
        return {"success": False, "error": "Email not configured — set SENDGRID_API_KEY"}

    if not to_email:
        return {"success": False, "error": "No customer email address on file"}

    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, To, From, Subject, HtmlContent

        sg = sendgrid.SendGridAPIClient(api_key=os.environ["SENDGRID_API_KEY"])

        sender_email = from_email or os.environ.get("SENDGRID_FROM_EMAIL", "noreply@bolts11.com")

        html_body = _build_invoice_html(
            customer_name=customer_name,
            business_name=business_name,
            invoice_id=invoice_id,
            line_items=line_items,
            subtotal=subtotal,
            tax_amount=tax_amount,
            total=total,
            payment_link_url=payment_link_url,
            doc_url=doc_url,
        )

        message = Mail(
            from_email=From(sender_email, from_name),
            to_emails=To(to_email, to_name),
            subject=Subject(f"Invoice from {business_name} — ${total:.2f}"),
            html_content=HtmlContent(html_body),
        )

        response = sg.client.mail.send.post(request_body=message.get())

        if response.status_code in (200, 202):
            print(f"[{_timestamp()}] INFO email_send: Invoice email sent to {to_email} — ${total:.2f}")
            return {"success": True}
        else:
            print(f"[{_timestamp()}] WARN email_send: SendGrid returned {response.status_code}")
            return {"success": False, "error": f"SendGrid error {response.status_code}"}

    except Exception as e:
        print(f"[{_timestamp()}] ERROR email_send: Invoice email failed — {e}")
        return {"success": False, "error": str(e)}


def send_proposal_email(
    to_email: str,
    to_name: str,
    from_name: str,
    proposal_id: str,
    customer_name: str,
    business_name: str,
    line_items: list,
    subtotal: float,
    tax_amount: float,
    total: float,
    doc_url: str = None,
    from_email: str = None,
) -> dict:
    """Send a proposal/estimate by email via SendGrid."""
    if not _sendgrid_available():
        return {"success": False, "error": "Email not configured — set SENDGRID_API_KEY"}

    if not to_email:
        return {"success": False, "error": "No customer email address on file"}

    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, To, From, Subject, HtmlContent

        sg = sendgrid.SendGridAPIClient(api_key=os.environ["SENDGRID_API_KEY"])

        sender_email = from_email or os.environ.get("SENDGRID_FROM_EMAIL", "noreply@bolts11.com")

        html_body = _build_proposal_html(
            customer_name=customer_name,
            business_name=business_name,
            proposal_id=proposal_id,
            line_items=line_items,
            subtotal=subtotal,
            tax_amount=tax_amount,
            total=total,
            doc_url=doc_url,
        )

        message = Mail(
            from_email=From(sender_email, from_name),
            to_emails=To(to_email, to_name),
            subject=Subject(f"Estimate from {business_name} — ${total:.2f}"),
            html_content=HtmlContent(html_body),
        )

        response = sg.client.mail.send.post(request_body=message.get())

        if response.status_code in (200, 202):
            print(f"[{_timestamp()}] INFO email_send: Proposal email sent to {to_email} — ${total:.2f}")
            return {"success": True}
        else:
            return {"success": False, "error": f"SendGrid error {response.status_code}"}

    except Exception as e:
        print(f"[{_timestamp()}] ERROR email_send: Proposal email failed — {e}")
        return {"success": False, "error": str(e)}
