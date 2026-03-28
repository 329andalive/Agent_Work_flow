"""
routes_debug.py — Internal debug dashboard for development

Shows jobs, proposals, invoices, tokens, agent activity, and messages
directly from Supabase. No SMS needed to verify what happened.

Protected by DEBUG_KEY query parameter. Remove before production.

Register in sms_receive.py:
    from routes.routes_debug import debug_bp
    app.register_blueprint(debug_bp)

Access at:
    http://localhost:8080/debug?key=YOUR_DEBUG_KEY
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, request, abort, render_template_string
from execution.db_connection import get_client as get_supabase

debug_bp = Blueprint("debug", __name__)


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


DEBUG_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Bolts11 Debug Dashboard</title>
  <style>
    :root {
      --bg: #0f1117;
      --card: #1a1d27;
      --border: #2a2d3a;
      --text: #e2e8f0;
      --muted: #64748b;
      --green: #22c55e;
      --amber: #f59e0b;
      --red: #ef4444;
      --blue: #3b82f6;
      --purple: #a855f7;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'DM Mono', 'Courier New', monospace;
      background: var(--bg);
      color: var(--text);
      padding: 24px;
      font-size: 13px;
    }
    h1 { font-size: 1.2rem; color: var(--green); margin-bottom: 4px; }
    .subtitle { color: var(--muted); margin-bottom: 24px; font-size: 11px; }
    .section { margin-bottom: 32px; }
    .section-title {
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--muted);
      border-bottom: 1px solid var(--border);
      padding-bottom: 8px;
      margin-bottom: 12px;
    }
    table { width: 100%; border-collapse: collapse; }
    th {
      text-align: left;
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      padding: 6px 10px;
      border-bottom: 1px solid var(--border);
    }
    td {
      padding: 8px 10px;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
      max-width: 280px;
      word-break: break-word;
    }
    tr:hover td { background: var(--card); }
    .badge {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
    }
    .badge-green  { background: #14532d; color: var(--green); }
    .badge-amber  { background: #451a03; color: var(--amber); }
    .badge-red    { background: #450a0a; color: var(--red); }
    .badge-blue   { background: #1e3a5f; color: var(--blue); }
    .badge-purple { background: #3b0764; color: var(--purple); }
    .badge-muted  { background: var(--border); color: var(--muted); }
    .token-link { color: var(--blue); text-decoration: none; }
    .token-link:hover { text-decoration: underline; }
    .empty { color: var(--muted); padding: 12px 10px; font-style: italic; }
    .refresh {
      display: inline-block;
      margin-bottom: 20px;
      color: var(--muted);
      font-size: 11px;
    }
    .id-short { color: var(--muted); font-size: 11px; }
    .amount { color: var(--green); font-weight: 600; }
  </style>
</head>
<body>
  <h1>Bolts11 Debug Dashboard</h1>
  <div class="subtitle">Live data from Supabase — development only — refreshed on load</div>
  <div class="refresh">Last loaded: {{ ts }} &nbsp;·&nbsp;
    <a href="?key={{ key }}" style="color:var(--blue)">Refresh</a>
  </div>

  <!-- RECENT JOBS -->
  <div class="section">
    <div class="section-title">Recent Jobs (last 10)</div>
    {% if jobs %}
    <table>
      <tr><th>ID</th><th>Type</th><th>Status</th><th>Raw Input</th><th>Created</th></tr>
      {% for j in jobs %}
      <tr>
        <td><span class="id-short">{{ j.id[:8] }}</span></td>
        <td>{{ j.job_type or '—' }}</td>
        <td>
          {% if j.status == 'complete' or j.status == 'invoiced' %}
            <span class="badge badge-green">{{ j.status }}</span>
          {% elif j.status == 'estimated' %}
            <span class="badge badge-blue">{{ j.status }}</span>
          {% elif j.status == 'scheduled' %}
            <span class="badge badge-purple">{{ j.status }}</span>
          {% else %}
            <span class="badge badge-muted">{{ j.status or 'new' }}</span>
          {% endif %}
        </td>
        <td>{{ (j.raw_input or '—')[:60] }}{{ '...' if j.raw_input and j.raw_input|length > 60 else '' }}</td>
        <td>{{ j.created_at[:16].replace('T',' ') if j.created_at else '—' }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">No jobs found.</div>
    {% endif %}
  </div>

  <!-- RECENT PROPOSALS -->
  <div class="section">
    <div class="section-title">Recent Proposals (last 10)</div>
    {% if proposals %}
    <table>
      <tr><th>ID</th><th>Amount</th><th>Status</th><th>Edit Token</th><th>Created</th></tr>
      {% for p in proposals %}
      <tr>
        <td><span class="id-short">{{ p.id[:8] }}</span></td>
        <td class="amount">{{ '$%.2f'|format(p.amount_estimate|float) if p.amount_estimate else '—' }}</td>
        <td>
          {% if p.status == 'accepted' %}
            <span class="badge badge-green">{{ p.status }}</span>
          {% elif p.status == 'sent' %}
            <span class="badge badge-blue">{{ p.status }}</span>
          {% elif p.status == 'declined' %}
            <span class="badge badge-red">{{ p.status }}</span>
          {% else %}
            <span class="badge badge-muted">{{ p.status or 'draft' }}</span>
          {% endif %}
        </td>
        <td>
          {% if p.edit_token %}
          <a class="token-link" href="/doc/edit/{{ p.edit_token }}?type=proposal" target="_blank">edit</a>
          {% else %}—{% endif %}
        </td>
        <td>{{ p.created_at[:16].replace('T',' ') if p.created_at else '—' }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">No proposals found.</div>
    {% endif %}
  </div>

  <!-- RECENT INVOICES -->
  <div class="section">
    <div class="section-title">Recent Invoices (last 10)</div>
    {% if invoices %}
    <table>
      <tr><th>ID</th><th>Amount</th><th>Status</th><th>Paid At</th><th>Edit</th><th>Created</th></tr>
      {% for inv in invoices %}
      <tr>
        <td><span class="id-short">{{ inv.id[:8] }}</span></td>
        <td class="amount">{{ '$%.2f'|format(inv.amount_due|float) if inv.amount_due else '—' }}</td>
        <td>
          {% if inv.paid_at %}
            <span class="badge badge-green">paid</span>
          {% elif inv.status == 'sent' %}
            <span class="badge badge-blue">sent</span>
          {% else %}
            <span class="badge badge-amber">{{ inv.status or 'draft' }}</span>
          {% endif %}
        </td>
        <td>{{ inv.paid_at[:16].replace('T',' ') if inv.paid_at else '—' }}</td>
        <td>
          {% if inv.edit_token %}
          <a class="token-link" href="/doc/edit/{{ inv.edit_token }}?type=invoice" target="_blank">edit</a>
          {% else %}—{% endif %}
        </td>
        <td>{{ inv.created_at[:16].replace('T',' ') if inv.created_at else '—' }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">No invoices found.</div>
    {% endif %}
  </div>

  <!-- INVOICE LINKS / TOKENS -->
  <div class="section">
    <div class="section-title">Invoice Links / Tokens (last 10)</div>
    {% if invoice_links %}
    <table>
      <tr><th>Token</th><th>Type</th><th>Job ID</th><th>View Link</th><th>Expires</th><th>Viewed</th></tr>
      {% for lnk in invoice_links %}
      <tr>
        <td><strong>{{ lnk.token }}</strong></td>
        <td><span class="badge badge-{{ 'blue' if lnk.type == 'proposal' else 'purple' }}">{{ lnk.type or '—' }}</span></td>
        <td><span class="id-short">{{ lnk.job_id[:8] if lnk.job_id else '—' }}</span></td>
        <td>
          {% if lnk.type == 'proposal' %}
          <a class="token-link" href="/p/{{ lnk.token }}" target="_blank">/p/{{ lnk.token }}</a>
          {% else %}
          <a class="token-link" href="/i/{{ lnk.token }}" target="_blank">/i/{{ lnk.token }}</a>
          {% endif %}
        </td>
        <td>
          {% if lnk.is_expired %}
            <span class="badge badge-red">expired</span>
          {% else %}
            <span class="badge badge-green">active</span>
          {% endif %}
        </td>
        <td>{{ lnk.viewed_at[:16].replace('T',' ') if lnk.viewed_at else 'not yet' }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">No invoice links found.</div>
    {% endif %}
  </div>

  <!-- AGENT ACTIVITY -->
  <div class="section">
    <div class="section-title">Agent Activity (last 20)</div>
    {% if activity %}
    <table>
      <tr><th>Agent</th><th>Action</th><th>SMS</th><th>Output</th><th>Time</th></tr>
      {% for a in activity %}
      <tr>
        <td><span class="badge badge-purple">{{ a.agent_name or '—' }}</span></td>
        <td>{{ a.action_taken or '—' }}</td>
        <td>
          {% if a.sms_sent %}
            <span class="badge badge-green">yes</span>
          {% else %}
            <span class="badge badge-muted">no</span>
          {% endif %}
        </td>
        <td style="font-size:11px; color:var(--muted);">
          {{ (a.output_summary or '—')[:80] }}{{ '...' if a.output_summary and a.output_summary|length > 80 else '' }}
        </td>
        <td>{{ a.created_at[:16].replace('T',' ') if a.created_at else '—' }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">No agent activity found.</div>
    {% endif %}
  </div>

  <!-- RECENT MESSAGES -->
  <div class="section">
    <div class="section-title">Recent Messages (last 20)</div>
    {% if messages %}
    <table>
      <tr><th>Dir</th><th>From</th><th>To</th><th>Body</th><th>Delivery</th><th>Time</th></tr>
      {% for m in messages %}
      <tr>
        <td>
          {% if m.direction == 'inbound' %}
            <span class="badge badge-blue">IN</span>
          {% else %}
            <span class="badge badge-purple">OUT</span>
          {% endif %}
        </td>
        <td style="font-size:11px;">{{ m.from_number or '—' }}</td>
        <td style="font-size:11px;">{{ m.to_number or '—' }}</td>
        <td>{{ (m.body or '—')[:60] }}{{ '...' if m.body and m.body|length > 60 else '' }}</td>
        <td>
          {% if m.delivery_status == 'delivered' or m.delivery_status == 'finalized' %}
            <span class="badge badge-green">{{ m.delivery_status }}</span>
          {% elif m.delivery_status == 'sent' %}
            <span class="badge badge-blue">sent</span>
          {% elif m.delivery_status == 'failed' %}
            <span class="badge badge-red">failed</span>
          {% else %}
            <span class="badge badge-muted">{{ m.delivery_status or '—' }}</span>
          {% endif %}
        </td>
        <td>{{ m.created_at[:16].replace('T',' ') if m.created_at else '—' }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">No messages found.</div>
    {% endif %}
  </div>

</body>
</html>
"""


@debug_bp.route("/debug", methods=["GET"])
def debug_dashboard():
    """
    Internal debug dashboard. Protected by DEBUG_KEY query parameter.
    Access: http://localhost:8080/debug?key=holt2026debug
    """
    expected_key = os.environ.get("DEBUG_KEY", "")
    provided_key = request.args.get("key", "")
    if not expected_key or provided_key != expected_key:
        abort(403)

    try:
        supabase = get_supabase()
        now = datetime.now(timezone.utc)

        # Fetch recent data from each table
        jobs = (
            supabase.table("jobs")
            .select("id, job_type, status, raw_input, created_at")
            .order("created_at", desc=True).limit(10).execute()
        ).data or []

        proposals = (
            supabase.table("proposals")
            .select("id, amount_estimate, status, edit_token, created_at")
            .order("created_at", desc=True).limit(10).execute()
        ).data or []

        invoices = (
            supabase.table("invoices")
            .select("id, amount_due, status, paid_at, edit_token, created_at")
            .order("created_at", desc=True).limit(10).execute()
        ).data or []

        raw_links = (
            supabase.table("invoice_links")
            .select("token, type, job_id, expires_at, viewed_at")
            .order("created_at", desc=True).limit(10).execute()
        ).data or []

        # Mark expired tokens
        for lnk in raw_links:
            try:
                exp = datetime.fromisoformat(lnk["expires_at"].replace("Z", "+00:00"))
                lnk["is_expired"] = now > exp
            except Exception:
                lnk["is_expired"] = False

        activity = (
            supabase.table("agent_activity")
            .select("agent_name, action_taken, sms_sent, output_summary, created_at")
            .order("created_at", desc=True).limit(20).execute()
        ).data or []

        messages = (
            supabase.table("messages")
            .select("direction, from_number, to_number, body, delivery_status, created_at")
            .order("created_at", desc=True).limit(20).execute()
        ).data or []

        ts = now.strftime("%Y-%m-%d %H:%M UTC")

        # Check Square SDK availability
        try:
            from execution.square_agent import SQUARE_AVAILABLE
        except ImportError:
            SQUARE_AVAILABLE = False

        return render_template_string(
            DEBUG_PAGE,
            jobs=jobs,
            proposals=proposals,
            invoices=invoices,
            invoice_links=raw_links,
            activity=activity,
            messages=messages,
            ts=ts,
            key=provided_key,
            square_sdk=SQUARE_AVAILABLE,
        )

    except Exception as e:
        print(f"[{timestamp()}] ERROR routes_debug: Dashboard load failed — {e}")
        return f"<pre>Error loading dashboard:\n{str(e)}</pre>", 500
