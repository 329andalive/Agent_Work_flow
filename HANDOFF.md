# HANDOFF.md — Bolts11 Session Log
> Last updated: March 23, 2026 — Backend Engineer
> Read CLAUDE.md first every session before touching any code.

---

## Current Production Status

```
Railway URL:   https://web-production-043dc.up.railway.app
API domain:    https://api.bolts11.com (DNS may not be pointed yet)
GitHub repo:   329andalive/Agent_Work_flow
Python:        3.12.9 (pinned via .python-version — DO NOT remove this file)
Deploy:        Auto-deploy on push to main
Build status:  ✅ GREEN as of March 23, 2026 — gunicorn 25.1.0 booting clean
```

---

## What Works Right Now — Confirmed in Production

- [x] Login with phone + PIN → session → dashboard loads
- [x] Sidebar nav — wave-style collapsible, navy/amber, persists across all pages
- [x] Control Board `/dashboard/` — jobs today, invoices outstanding, SMS count, team
- [x] Office page `/dashboard/office.html` — invoices + proposals, clickable rows
- [x] Proposal document view `/dashboard/proposal/<id>` — line items, accept/lost/send
- [x] Invoice document view `/dashboard/invoice/<id>` — Mark Paid, paid banner
- [x] Command Center `/dashboard/command.html` — direct agent dispatch, Haiku classification
- [x] New Job form `/dashboard/new-job` — customer dropdown, proposal checkbox
- [x] Add Customer form `/dashboard/customers/new` — POSTs to `/api/customers/create`
- [x] Export CSV `/api/invoices/export-csv` — QuickBooks-compatible download
- [x] Job detail route `/dashboard/job/<id>` — customer, proposals, invoices, activity
- [x] 25 test customers imported for Holt Sewer & Drain
- [x] `squareup==44.0.1.20260122` installed and pinned — Square SDK available on Railway
- [x] All 66 dependencies fully pinned in requirements.txt — no floating versions

## Still Broken / Not Yet Built

- [ ] Control Board job "View →" links — `href="#"` (Claude Code Prompt #1)
- [ ] `/dashboard/customers/` — TemplateNotFound, `customers.html` missing (Prompt #2 + 7.9)
- [ ] `/dashboard/customers/<id>` — 404, no route or template (Prompt #3)
- [ ] `/dashboard/estimates/` — route exists, `estimates.html` template missing (Prompt #4)
- [ ] `/dashboard/invoices/` — route exists, `invoices.html` template missing (Prompt #4)
- [ ] `/dashboard/payments/` — route exists, `payments.html` template missing (Prompt #4)
- [ ] Customers page inline add form — `customers.html` not yet built (Prompt #5, blocked on #2)
- [ ] `customer_detail.html` — template missing (Prompt #3)
- [ ] `job_detail.html` — template missing (needs to be created)
- [ ] Square payment link not wired in invoice_agent.py (7.10 — see below)
- [ ] Debug prints still in auth_routes.py login handler (7.11)

---

## Backend Action Items — This Session

### 7.9 — PRIORITY: Create Missing Dashboard Templates
**Root cause confirmed:** Routes are correct. Templates are missing. Flask throws
`TemplateNotFound` for every new tab. The fix is creating the template files.

Templates needed (all extend `base.html`, use existing CSS classes):
```
templates/dashboard/customers.html       — customer list + inline add form
templates/dashboard/customer_detail.html — single customer profile
templates/dashboard/job_detail.html      — job detail with customer/proposals/invoices
templates/dashboard/estimates.html       — proposals list with metrics strip
templates/dashboard/invoices.html        — invoices list with CSV export button
templates/dashboard/payments.html        — paid invoices only
```

Full specs for each template are in the Claude Code Prompt Queue below (Section 4).
Also fix `control.html` job row `href="#"` → `href="/dashboard/job/{{ j.id }}"`.

Owner: Backend Engineer (or run Claude Code prompts in order)

### 7.10 — Square Write-Back: Three Gaps Found and Fixed (Partially)

Full audit completed. The `status='paid'` and `paid_at` write-back in
`token_generator.mark_invoice_paid()` is structurally correct. Three gaps found:

**Gap 1 — Schema (BLOCKING):** `square_payment_id` column missing from `invoices` table.
`mark_invoice_paid()` tries to write it — Supabase will throw if column doesn't exist.

ACTION REQUIRED — Run this SQL in Supabase SQL editor before Square goes live:
```sql
ALTER TABLE invoices
  ADD COLUMN IF NOT EXISTS square_payment_id TEXT;

CREATE INDEX IF NOT EXISTS idx_invoices_square_payment_id
  ON invoices (square_payment_id)
  WHERE square_payment_id IS NOT NULL;

ALTER TABLE invoice_links
  ADD COLUMN IF NOT EXISTS square_order_id TEXT;

ALTER TABLE invoice_links
  ADD COLUMN IF NOT EXISTS square_payment_link_id TEXT;

ALTER TABLE invoice_links
  ADD COLUMN IF NOT EXISTS payment_link_url TEXT;

CREATE INDEX IF NOT EXISTS idx_invoice_links_square_order_id
  ON invoice_links (square_order_id)
  WHERE square_order_id IS NOT NULL;
```
This SQL is also saved at `sql/square_payment_writeback.sql`.

**Gap 2 — Missing wire (CRITICAL):** `attach_payment_link()` is never called after
`square_agent.create_payment_link()`. The `invoice_links.square_order_id` column is
never populated, so the Square webhook reverse-lookup (`get_link_by_square_order`)
always returns None — payment received but invoice never marked paid.

Fix needed in `execution/invoice_agent.py` — add Step 8b after the edit_url block:
```python
        # Step 8b: Create Square payment link + wire square_order_id to invoice_links
        # This is what allows the /webhooks/square handler to find the invoice on payment.
        # Non-fatal — if Square is not configured, invoice still saves and SMS still sends.
        if invoice_id and os.environ.get("SQUARE_ACCESS_TOKEN"):
            try:
                from execution.token_generator import generate_token, attach_payment_link
                invoice_token = generate_token(
                    job_id=job_id,
                    client_phone=client_phone,
                    link_type="invoice",
                )
                if invoice_token:
                    from execution.square_agent import create_payment_link
                    amount_cents = int(round(final_amount * 100))
                    square_result = create_payment_link(
                        invoice_id=invoice_id,
                        amount_cents=amount_cents,
                        description=f"{clean_job_desc} — {client['business_name']}",
                        customer_name=customer_name,
                    )
                    if square_result.get("success"):
                        attach_payment_link(
                            token=invoice_token,
                            payment_link_url=square_result["payment_link_url"],
                            square_order_id=square_result.get("square_order_id"),
                            square_payment_link_id=square_result.get("square_payment_link_id"),
                        )
                        print(f"[{timestamp()}] INFO invoice_agent: Square payment link wired → {square_result['payment_link_url']}")
                    else:
                        print(f"[{timestamp()}] WARN invoice_agent: Square link failed — {square_result.get('error')}")
            except Exception as e:
                print(f"[{timestamp()}] WARN invoice_agent: Square link creation error — {e} (non-fatal)")
```

**Gap 3 — Multi-tenancy:** Confirmed safe. No code change needed.

**To go live with Square production:**
1. Run `sql/square_payment_writeback.sql` in Supabase SQL editor
2. Add Step 8b to `execution/invoice_agent.py`
3. Add `mark_invoice_paid()` two-pass fallback to `execution/token_generator.py`
   (retry without `square_payment_id` if column error — never drop a real payment)
4. Set Railway env vars:
   - `SQUARE_ACCESS_TOKEN` — production token
   - `SQUARE_ENVIRONMENT` — `production`
   - `SQUARE_LOCATION_ID` — production location ID
   - `SQUARE_WEBHOOK_SIGNATURE_KEY` — from Square dashboard
   - `SQUARE_WEBHOOK_URL` — `https://api.bolts11.com/webhooks/square`
5. Register webhook in Square Dashboard: event `payment.completed`, URL above
6. Test: send a sandbox payment, verify invoice flips to `status='paid'` in Supabase
   and appears in `/dashboard/payments/`

Owner: Backend Engineer

### 7.11 — Remove Debug Prints from auth_routes.py
Remove all `print(f"[...] DEBUG ...")` lines from the `/login` POST handler.
Keep only `INFO`, `WARN`, `ERROR` level prints. Must be done before any customer demo.
Owner: Backend Engineer

---

## Build Infrastructure — Fixed This Session

**Problem:** Railway was auto-resolving Python to `3.13.12` (freethreaded build),
which failed with `mise ERROR: Python installation is missing a lib directory`.
The broken build was triggered by adding `squareup` to `requirements.txt`.

**Fix applied:**
1. Added `.python-version` to repo root containing `3.12.9` — pins Railway/mise
   to a stable build. **DO NOT delete this file.**
2. Ran `pip freeze` inside a clean Python 3.12 venv — `requirements.txt` now has
   66 fully pinned packages instead of 9 unpinned names.
3. `squareup==44.0.1.20260122` is now a proper pinned dependency.

**Build confirmed green:** `gunicorn 25.1.0` starting, worker booting, no import errors.

---

## Claude Code Prompt Queue — Run in Order

| # | Prompt | Files | Status |
|---|--------|-------|--------|
| 1 | Wire job "View →" links on Control Board | `control.html` | Ready |
| 2 | Create missing dashboard templates (7.9) | 6 new templates | Ready — PRIORITY |
| 3 | Add Step 8b to invoice_agent.py (7.10 Gap 2) | `invoice_agent.py` | Ready |
| 4 | Remove debug prints from auth_routes.py (7.11) | `auth_routes.py` | Ready |
| 5 | Run square_payment_writeback.sql in Supabase | SQL editor | Manual step |

### Prompt #1 — Wire Job "View →" Links
File: `templates/dashboard/control.html`
Inside the `{% for j in jobs %}` loop, find: `<a href="#" class="job-card-row__link">`
Change to: `<a href="/dashboard/job/{{ j.id }}" class="job-card-row__link">`
Do not change anything else in this file.

### Prompt #2 — Create All Six Missing Templates (7.9)
This is the big one. Read the full spec below before starting.

Read first: `templates/base.html`, `templates/dashboard/office.html`,
`templates/dashboard/proposal_view.html`, `routes/dashboard_routes.py`

All templates must:
- `{% extends "base.html" %}`
- Use only CSS variables and classes already defined in `base.html`
- Match the visual style of `office.html` exactly — no new CSS frameworks

**customers.html** — context: `customers` (list), `fmt_phone`, `fmt_short_date`
Each customer dict has: `id`, `customer_name`, `customer_phone`, `customer_address`,
`sms_consent` (bool), `job_count` (int), `last_job` (ISO string or empty string).
Layout: two-column desktop (≤768px stacked).
- Left col (300px): "New Customer" card with inline add form
  - Fields: Full Name, Phone (required), Address, Notes (textarea)
  - JS fetch POST to `/api/customers/create`, no `<form>` action attribute
  - On success: green "Customer saved" message, reload after 1.2s
  - On error: red inline error, re-enable submit button
- Right col (flex 1): "Customers" card
  - Search input — live JS filter on `data-search` attribute
  - `.data-table`: Name, Phone, Address, Jobs (badge-blue if > 0), Last Job,
    SMS dot (green=#639922 if consent, grey=#d1d5db if not), arrow →
  - Each `<tr>` has `onclick="window.location='/dashboard/customers/{{ c.id }}'"` 
  - Empty state if no customers. "No results" div when search yields nothing.
- Metrics strip above: Total Customers, SMS Opted In, With Jobs

**customer_detail.html** — context: `customer`, `jobs`, `proposals`, `invoices`,
`fmt_date`, `fmt_phone`, `fmt_short_date`
Layout:
- Back link: `← Customers` → `/dashboard/customers/`
- Header card: customer name (large), phone formatted, address, SMS consent dot + label,
  date added via `fmt_date(customer.created_at)`
- Three cards: Jobs list (rows link to `/dashboard/job/{{ j.id }}`),
  Proposals list (→ `/dashboard/proposal/{{ p.id }}`),
  Invoices list (→ `/dashboard/invoice/{{ inv.id }}`)
- Each list uses `.data-table` with status badge and short date
- Empty states on all three lists

**job_detail.html** — context: `job`, `customer`, `proposals`, `invoices`, `activity`,
`fmt_date`, `fmt_phone`, `fmt_short_date`, `fmt_activity_time`
Layout:
- Back link: `← Control Board` → `/dashboard/`
- Status banner: colored by status (scheduled=blue, in_progress=amber,
  completed=green, cancelled=red)
- `.grid-2`: left col (Job Details card + Customer card), right col (Proposals + Invoices)
- Job Details: job_type, scheduled_date via fmt_date, address from job.job_description
  or customer.customer_address, job_notes
- Customer card: name, phone, address, SMS dot. "View →" links to `/dashboard/customers/{{ customer.id }}`
- Proposals: rows link to `/dashboard/proposal/{{ p.id }}`, show amount/date/badge
- Invoices: rows link to `/dashboard/invoice/{{ inv.id }}`, show amount/date/badge
- Activity card below grid: `activity[:10]`, agent name, time, output_summary

**estimates.html** — context: `proposals`, `cust_map`, `proposals_sent`,
`proposals_won`, `proposals_outstanding`, `win_rate`, `fmt_short_date`
Layout:
- `{% block page_title %}Estimates{% endblock %}`
- Metrics strip: Win Rate (`{{ win_rate }}%`), Sent, Accepted, Open
- Card "Estimates — Last 90 Days"
- `.data-table`: Customer name (from `cust_map[p.customer_id].customer_name` or `—`),
  Date (`fmt_short_date(p.created_at)`), Amount (`${{ "%.0f"|format(p.amount_estimate|float) }}`),
  Status badge. Each row `onclick` → `/dashboard/proposal/{{ p.id }}`
- Empty state: "No estimates in the last 90 days."

**invoices.html** — context: `invoices`, `cust_map`, `total_billed`, `total_paid`,
`total_outstanding`, `fmt_short_date`
Layout:
- `{% block page_title %}Invoices{% endblock %}`
- Metrics strip: Billed, Collected, Outstanding (amber if > 0), Count
- Card header: "Invoices — Last 90 Days" + Export CSV button → `/api/invoices/export-csv`
- `.data-table`: Customer name, Date, Amount, Status badge. Each row → `/dashboard/invoice/{{ inv.id }}`
- Age pill JS: if `inv.status != 'paid'` and age > 30 days, show `Xd` badge in amber
- Empty state: "No invoices in the last 90 days."

**payments.html** — context: `payments`, `cust_map`, `total_collected`,
`payment_count`, `avg_payment`, `fmt_short_date`, `fmt_date`
Layout:
- `{% block page_title %}Payments{% endblock %}`
- Metrics strip: Total Collected (`${{ "%.2f"|format(total_collected) }}`),
  Count, Average (`${{ "%.2f"|format(avg_payment) }}`)
- Card "Payments Received — Last 90 Days"
- `.data-table`: Customer name, Amount (green), Paid Date (`fmt_short_date(p.paid_at)`),
  green paid badge. Each row → `/dashboard/invoice/{{ p.id }}`
- Empty state: "No payments recorded in the last 90 days."

After creating all 6 templates, commit with message:
`feat: add 6 missing dashboard templates — fixes TemplateNotFound on all new tabs (7.9)`

### Prompt #3 — Wire Step 8b in invoice_agent.py (7.10 Gap 2)
See the full code block in section 7.10 above. Insert it in
`execution/invoice_agent.py` immediately after the `edit_url` block, before Step 9.
Commit: `fix: wire Square payment link to invoice_links in invoice_agent Step 8b (7.10)`

### Prompt #4 — Remove debug prints from auth_routes.py (7.11)
Remove every line containing `print(f"[{_ts()}] DEBUG` from the login POST handler.
Keep all INFO/WARN/ERROR prints. Commit: `fix: remove debug prints from auth login handler (7.11)`

### Prompt #5 — Run SQL migration (manual)
Open Supabase dashboard → SQL editor → paste and run the SQL from section 7.10 Gap 1.
This is a manual step, not a code change.

---

## Architecture Reference

### Stack
```
Backend:     Python 3.12.9 / Flask 3.1.3
Database:    Supabase (PostgreSQL)
SMS:         Telnyx (10DLC pending — all outbound SMS blocked)
AI:          Anthropic Claude — Haiku for classification, Sonnet for generation
Payments:    Square 44.0.1 (sandbox — not yet wired to production)
Deploy:      Railway — auto-deploy on push to main
Auth:        Phone + 4-digit PIN → Flask session (30-day lifetime)
```

### Key Credentials
```
Railway URL:    https://web-production-043dc.up.railway.app
API domain:     https://api.bolts11.com (DNS may not be pointed yet)
GitHub repo:    329andalive/Agent_Work_flow
Client ID:      8aafcd73-b41c-4f1a-bd01-3e7955798367
Business:       Holt Sewer & Drain
Owner phone:    +12074190986 (Telnyx number)
Owner mobile:   +12076538819 (Jeremy's cell)
Supabase URL:   https://wczzlvhpryufohjwmxwd.supabase.co
FLASK_ENV:      development (Railway — allows ?client_id= dev bypass)
```

### URL Map
```
/login                       — Phone + PIN auth
/logout                      — Clear session
/dashboard/                  — Control Board
/dashboard/office.html       — Office summary (keep — do not delete)
/dashboard/estimates/        — Estimates/proposals list
/dashboard/invoices/         — Invoices list
/dashboard/payments/         — Paid invoices list
/dashboard/customers/        — Customer list (broken — missing template)
/dashboard/customers/new     — Add Customer standalone form (keep as fallback)
/dashboard/customers/<id>    — Customer detail (missing route + template)
/dashboard/job/<id>          — Job detail (missing template)
/dashboard/proposal/<id>     — Proposal document view ✅
/dashboard/invoice/<id>      — Invoice document view ✅
/dashboard/command.html      — Command Center ✅
/dashboard/new-job           — New Job form ✅
/dashboard/onboarding.html   — Client onboarding admin ✅
/api/customers/create        — POST: create customer ✅
/api/jobs/create             — POST: create job ✅
/api/invoices/export-csv     — GET: QuickBooks CSV export ✅
/api/command                 — POST: Command Center agent dispatch ✅
/webhooks/telnyx             — SMS webhook ✅
/webhooks/square             — Square payment webhook ✅
/book                        — Public booking form (no auth) ✅
```

### File Map
```
execution/sms_receive.py         — Flask app entry point, all blueprint registration
execution/invoice_agent.py       — Invoice generation (needs Step 8b for Square)
execution/token_generator.py     — Token generation + mark_invoice_paid() (needs fallback)
execution/square_agent.py        — Square Payment Links API
routes/dashboard_routes.py       — All dashboard page routes
routes/auth_routes.py            — /login, /logout, /set-pin (needs debug print cleanup)
routes/invoice_routes.py         — Square webhook handler
routes/command_routes.py         — /api/command direct agent dispatch
templates/base.html              — Shared sidebar + layout (navy/amber) — read before any template work
templates/dashboard/
  control.html                   — Control Board (job links broken)
  office.html                    — Office summary (keep, do not delete)
  command.html                   — Command Center chat ✅
  customers.html                 — MISSING — needs to be created
  customer_detail.html           — MISSING — needs to be created
  estimates.html                 — MISSING — needs to be created
  invoices.html                  — MISSING — needs to be created
  payments.html                  — MISSING — needs to be created
  job_detail.html                — MISSING — needs to be created
  proposal_view.html             — Proposal document ✅
  invoice_view.html              — Invoice document ✅
  new_job.html                   — New Job form ✅
  new_customer.html              — Add Customer standalone ✅
  onboarding.html                — Client onboarding ✅
  coming_soon.html               — Stub for unbuilt sections ✅
sql/square_payment_writeback.sql — Schema migration for Square columns (run in Supabase)
.python-version                  — Pins Python 3.12.9 for Railway/mise — DO NOT DELETE
requirements.txt                 — 66 pinned packages — regenerate with pip freeze if adding deps
CLAUDE.md                        — Master architecture doc — read every session
```

### Known Issues — Carry-Forward
1. **10DLC not approved** — outbound SMS blocked. Agents run, SMS fails silently.
2. **Square in sandbox** — PAY NOW works but hits sandbox. Full go-live checklist in 7.10.
3. **Customer SMS opt-in** — all 25 imported test customers have `sms_consent=false`.
   Bulk update needed before SMS goes live.
4. **Onboarding wizard** — built but not tested end-to-end in production.
5. **Pricing benchmarks** — `sql/pricing_benchmarks.sql` written, may not be run yet.
6. **Purchases / Bills / Vendors** — stub routes exist, no Supabase schema designed.
7. **Receipts** — stub route exists, depends on Purchases schema.
8. **Accounting / Transactions** — stub route exists, depends on Purchases schema.