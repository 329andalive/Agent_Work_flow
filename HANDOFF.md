# HANDOFF.md — Session Summary
> Last updated: March 23, 2026
> Session: Dashboard tab wiring, Sales & Payments split, Customers page redesign

---

## 1. What Was Built This Session

### Work Completed — Frontend Engineer

#### Claude Code Prompt Queue — 5 prompts written, ready to run in order

**Prompt #1 — Wire Job "View →" Links on Control Board**
File: `templates/dashboard/control.html`
Fix: Every job row "View →" link currently has `href="#"`. Change to
`href="/dashboard/job/{{ job.id }}"` inside the `{% for job in jobs %}` loop.
Verified live: Three jobs on Control Board all showing `href="#"` — confirmed broken.
Result: Clicking any job row on `/dashboard/` navigates to `/dashboard/job/<uuid>`.

**Prompt #2 — Remove Customers Coming Soon Stub**
File: `routes/dashboard_routes.py`
Fix: `/dashboard/customers/` is rendering `coming_soon.html` instead of `customers.html`.
The full `customers()` route function already exists and is correct. A stub route is
overriding it. Find and remove the stub that calls
`render_template("dashboard/coming_soon.html", ...)` for `/dashboard/customers/`.
Keep only the full `customers()` function.
Verified live: `/dashboard/customers/` shows "This section is being built." — confirmed broken.
Result: `/dashboard/customers/` renders real customer data from `customers.html`.
⚠️  PREREQUISITE: Must be deployed before Prompt #5 will work.

**Prompt #3 — Build Customer Detail Page**
Files: `routes/dashboard_routes.py` (new route) + `templates/dashboard/customer_detail.html` (new)
Route: `GET /dashboard/customers/<customer_id>`
Currently returns 404 — no route or template exists.
Route logic:
- `_resolve_client_id()` — redirect `/login` if missing
- `_base_context("customers", client_id)`
- Query `customers`: single record by `id` AND `client_id` — 404 if not found
- Query `jobs`: all for this customer + client, ordered `scheduled_date` desc
- Query `proposals`: all for this customer + client, ordered `created_at` desc, limit 10
- Query `invoices`: all for this customer + client, ordered `created_at` desc, limit 10
- Pass `fmt_date`, `fmt_phone`, `fmt_short_date` helpers
Template: customer name, phone (formatted), address, SMS consent dot (green/grey),
date added, jobs list, proposals list, invoices list — each row links to its detail page.
Back link → `/dashboard/customers/`. Match `job_detail.html` card/dl/badge pattern exactly.
Multi-tenancy: every query must filter by both `customer_id` AND `client_id`.

**Prompt #4 — Split Sales & Payments into Three Separate Pages**
Currently: Estimates, Invoices, Payments all link to `office.html` or `office.html#anchor`.
Anchors go nowhere. Three real pages needed.
Files:
- `routes/dashboard_routes.py` — add 3 new routes
- `templates/dashboard/estimates.html` — new
- `templates/dashboard/invoices.html` — new
- `templates/dashboard/payments.html` — new
- `templates/base.html` — update 3 sidebar hrefs only

Route 1: `GET /dashboard/estimates/` → `estimates_page()`
- Query `proposals` table, last 90 days, ordered `created_at` desc
- `_base_context("estimates", client_id)`
- Compute: `proposals_sent`, `proposals_won`, `win_rate`
- Summary strip: Win Rate, Sent, Accepted, Outstanding
- List: clickable rows → `/dashboard/proposal/{{ p.id }}`, customer name, date, amount, status badge

Route 2: `GET /dashboard/invoices/` → `invoices_page()`
- Query `invoices` table, last 90 days, ordered `created_at` desc
- `_base_context("invoices", client_id)`
- Compute: `total_billed`, `total_paid`, `total_outstanding`
- Summary strip: Billed, Collected, Outstanding, Count
- List: extract directly from `office.html` — same `.list-row` pattern + age pills JS
- Export CSV button → `/api/invoices/export-csv`

Route 3: `GET /dashboard/payments/` → `payments_page()`
- Query `invoices` WHERE `status='paid'`, last 90 days, ordered `paid_at` desc
- `_base_context("payments", client_id)`
- Compute: `total_collected`, `payment_count`
- Summary strip: Total Collected, Payment Count, Average Payment
- List: paid invoices only — customer name, amount, paid date, green badge

base.html sidebar href changes (3 lines only — do not touch anything else in base.html):
  `/dashboard/office.html#estimates` → `/dashboard/estimates/`
  `/dashboard/office.html`           → `/dashboard/invoices/`
  `/dashboard/office.html#payments`  → `/dashboard/payments/`
Update active_page checks to use `'estimates'`, `'invoices'`, `'payments'`.
Do NOT delete or modify `office.html`.

**Prompt #5 — Customers Page: Inline Add Form + Searchable List**
⚠️  BLOCKED: Requires Prompt #2 deployed first. Also requires Backend Engineer
to fix item 7.9 (stub route removal) before results are visible.
File: `templates/dashboard/customers.html` — full rewrite

Layout: Two-column desktop (stacked mobile ≤768px)
- Left column 300px: "New Customer" card — inline add form
- Right column flex 1: "Customers" card — searchable list

Left column — Add Customer card:
- Fields: Full Name, Phone (required, hint: "Required for SMS"), Address, Notes (textarea 3 rows)
- Submit: POST JSON to `/api/customers/create` (same fetch pattern as `new_customer.html`)
- On success: show green "Customer saved", reload page after 1.2s
- On error: show red error inline, re-enable button
- No `<form>` action — JS fetch only

Right column — Customer list:
- Search input (live filter — searches name, phone, address via `data-search` attribute on rows)
- Table: Name, Phone, Address, Jobs, Last Job, SMS dot, arrow →
- Each row onclick → `/dashboard/customers/{{ c.id }}`
- Empty state: "No customers yet. Add your first customer using the form."
- Shows "No customers match your search." when search has no results

Styling: `base.html` CSS variables only. DM Mono labels, Barlow body, flat cards, no shadows.
Do NOT modify: `dashboard_routes.py`, `base.html`, `new_customer.html`, any other file.

---

## 2. Backend Engineer Action Items

### 7.9 — Customers Route Stub Removal (PRIORITY — BLOCKS FRONTEND)
The `/dashboard/customers/` route serves `coming_soon.html` instead of real data.
The full `customers()` function already exists in `routes/dashboard_routes.py` and is correct.
A stub or duplicate route is overriding it.

Action: Find and remove the stub route for `/dashboard/customers/` that renders
`coming_soon.html`. The real `customers()` function must be the only handler for
`@dashboard_bp.route("/dashboard/customers/")`.

Verify: `/dashboard/customers/` shows the customer table with 25 Holt Sewer & Drain
customers, search bar, and clickable rows. Not the stub.

This unblocks: Frontend Prompts #2 and #5.
Owner: Backend Engineer — PRIORITY this session

### 7.10 — Payments Page: Square Write-Back Verification
The new `/dashboard/payments/` page (Prompt #4) reads from `invoices WHERE status='paid'`.
When Square goes live, confirm `routes/invoice_routes.py` Square webhook writes back
`status='paid'` and `paid_at = now()` on payment confirmation. If not, add the write-back.
Without this, the Payments page will show no data even after real payments are received.
Owner: Backend Engineer — unblock after Square production switch

### 7.11 — Remove Debug Logging from auth_routes.py (carry-forward)
Temporary PIN debug print statements still active in the `/login` route.
Remove before any customer demo or production handoff.
Owner: Backend Engineer

---

## 3. Production Status — What Works Right Now

- [x] Login with phone + PIN → session → dashboard loads
- [x] Sidebar nav persists across all pages
- [x] Control Board: real jobs today, invoices outstanding, SMS count, team panel
- [x] Office page `/dashboard/office.html`: invoices + proposals, clickable rows
- [x] Proposal document view: line items, action buttons (accept/lost/send)
- [x] Invoice document view: Mark Paid, paid banner, line items
- [x] Command Center: direct agent dispatch, Haiku classification
- [x] New Job form `/dashboard/new-job`: customer dropdown, proposal checkbox, creates job
- [x] Add Customer form `/dashboard/customers/new`: POSTs to `/api/customers/create`
- [x] Export CSV `/api/invoices/export-csv`: QuickBooks-compatible, download works
- [x] Job detail route `/dashboard/job/<id>`: customer, proposals, invoices, activity
- [x] 25 test customers imported for Holt Sewer & Drain

- [ ] Control Board job "View →" links — `href="#"` broken (Prompt #1)
- [ ] `/dashboard/customers/` — showing coming_soon stub (Backend 7.9 + Prompt #2)
- [ ] `/dashboard/customers/<id>` — 404 no route (Prompt #3)
- [ ] `/dashboard/estimates/` — doesn't exist yet (Prompt #4)
- [ ] `/dashboard/invoices/` — doesn't exist yet (Prompt #4)
- [ ] `/dashboard/payments/` — doesn't exist yet (Prompt #4)
- [ ] Customers page inline add form — not built (Prompt #5, blocked on #2)

---

## 4. Known Issues — Carry-Forward

1. **10DLC not approved** — outbound SMS blocked. Agents run correctly, SMS fails silently.
2. **Square in sandbox** — PAY NOW works but hits sandbox. Switch to production when ready.
3. **Customer SMS opt-in** — all 25 imported customers have `sms_consent=false`. Need
   SET OPTIN per customer or bulk update before SMS goes live.
4. **Onboarding wizard** — built but not tested end-to-end. Personality MD generation unverified.
5. **Pricing benchmarks** — `sql/pricing_benchmarks.sql` written, may not be run in Supabase yet.
6. **Purchases / Bills / Vendors** — stub routes exist, no Supabase schema. Backend must design
   schema before frontend can build these pages.
7. **Receipts** — stub route exists, depends on Purchases schema (item 6).
8. **Accounting / Transactions** — stub route exists, depends on Purchases schema (item 6).
   CSV export already wired at `/api/invoices/export-csv`.

---

## 5. Architecture Reference

### Stack
```
Backend:   Python 3.12 / Flask
Database:  Supabase (PostgreSQL)
SMS:       Telnyx (10DLC pending — blocked)
AI:        Claude Haiku (classification) + Claude Sonnet (generation)
Payments:  Square (sandbox)
Deploy:    Railway (auto-deploy on GitHub push)
Auth:      Phone + 4-digit PIN → Flask session (30-day lifetime)
```

### Key Credentials
```
Railway URL:   https://web-production-043dc.up.railway.app
API domain:    https://api.bolts11.com (DNS may not be pointed yet)
GitHub repo:   329andalive/Agent_Work_flow
Client ID:     8aafcd73-b41c-4f1a-bd01-3e7955798367
Business:      Holt Sewer & Drain
Owner phone:   +12074190986 (Telnyx number)
Owner mobile:  +12076538819 (Jeremy's cell)
Supabase URL:  https://wczzlvhpryufohjwmxwd.supabase.co
FLASK_ENV:     development (Railway — allows dev bypass)
```

### URL Map
```
/login                      — Phone + PIN auth
/logout                     — Clear session
/dashboard/                 — Control Board
/dashboard/office.html      — Office summary (keep — do not delete)
/dashboard/estimates/       — Estimates list (NEW — Prompt #4)
/dashboard/invoices/        — Invoices list (NEW — Prompt #4)
/dashboard/payments/        — Paid invoices (NEW — Prompt #4)
/dashboard/customers/       — Customer list (broken — Backend 7.9 + Prompts #2 #5)
/dashboard/customers/new    — Add Customer standalone (keep as fallback)
/dashboard/customers/<id>   — Customer detail (NEW — Prompt #3)
/dashboard/job/<id>         — Job detail (links broken — Prompt #1)
/dashboard/proposal/<id>    — Proposal document view
/dashboard/invoice/<id>     — Invoice document view
/dashboard/command.html     — Command Center
/dashboard/new-job          — New Job form
/dashboard/onboarding.html  — Client onboarding admin
/api/customers/create       — POST: create customer
/api/jobs/create            — POST: create job
/api/invoices/export-csv    — GET: QuickBooks CSV export
/api/command                — POST: Command Center agent dispatch
/book                       — Public booking form (no auth)
```

### File Map
```
execution/sms_receive.py        — Flask app entry point, blueprint registration
routes/dashboard_routes.py      — All dashboard page routes (main file this session)
routes/auth_routes.py           — /login, /logout, /set-pin
routes/command_routes.py        — /api/command direct agent dispatch
templates/base.html             — Shared sidebar + layout (navy/amber)
templates/dashboard/
  control.html                  — Control Board
  office.html                   — Office summary (keep)
  command.html                  — Command Center chat
  customers.html                — Customer list (rewrite — Prompt #5)
  customer_detail.html          — Customer detail (NEW — Prompt #3)
  estimates.html                — Estimates (NEW — Prompt #4)
  invoices.html                 — Invoices (NEW — Prompt #4)
  payments.html                 — Payments (NEW — Prompt #4)
  job_detail.html               — Job detail
  proposal_view.html            — Proposal document
  invoice_view.html             — Invoice document
  new_job.html                  — New Job form
  new_customer.html             — Add Customer standalone (keep as fallback)
  onboarding.html               — Client onboarding admin
  coming_soon.html              — Stub for unbuilt sections
CLAUDE.md                       — Master architecture doc (read first every session)
```

---

## 6. Prompt Run Order — Claude Code

| # | Prompt | Files | Status |
|---|--------|-------|--------|
| 1 | Wire job "View →" links | `control.html` | ✅ DONE |
| 2 | Remove customers stub | `dashboard_routes.py` | ✅ DONE |
| 3 | Customer detail page | `dashboard_routes.py` + new `customer_detail.html` | ✅ DONE |
| 4 | Estimates / Invoices / Payments pages | `dashboard_routes.py` + 3 templates + `base.html` | ✅ DONE |
| 5 | Customers page inline add + search | `customers.html` rewrite | ✅ DONE |

---

## Session Update — March 23, 2026

### Templates Created (Backend Engineer)
- templates/dashboard/customers.html — customer list with search, job counts, SMS dots, metrics strip
- templates/dashboard/job_detail.html — job detail with customer, proposals, invoices, activity
- templates/dashboard/estimates.html — proposals list with metrics strip
- templates/dashboard/invoices.html — invoices list with CSV export button
- templates/dashboard/payments.html — paid invoices list
- templates/dashboard/customer_detail.html — customer profile with linked records

### Routes Added/Fixed (Backend Engineer)
- GET /dashboard/job/<job_id> — job_detail() added to dashboard_routes.py
- GET /dashboard/customers/<customer_id> — customer_detail() added to dashboard_routes.py
- templates/dashboard/control.html — job row "View →" href="#" → href="/dashboard/job/{{ j.id }}"
- routes/auth_routes.py — removed all DEBUG print statements from /login handler

### 7.9 Status: RESOLVED
/dashboard/customers/ was failing with TemplateNotFound — template now exists.
All stub tabs are now live with real data. Purchases/Receipts/Accounting remain
on coming_soon.html pending schema design.
# HANDOFF.md — Session Summary
> Last updated: March 23, 2026
> Session: Dashboard tab wiring, Sales & Payments split, Customers page redesign

---

## 1. What Was Built This Session

### Work Completed — Frontend Engineer

#### Claude Code Prompt Queue — 5 prompts written, ready to run in order

**Prompt #1 — Wire Job "View →" Links on Control Board**
File: `templates/dashboard/control.html`
Fix: Every job row "View →" link currently has `href="#"`. Change to
`href="/dashboard/job/{{ job.id }}"` inside the `{% for job in jobs %}` loop.
Verified live: Three jobs on Control Board all showing `href="#"` — confirmed broken.
Result: Clicking any job row on `/dashboard/` navigates to `/dashboard/job/<uuid>`.

**Prompt #2 — Remove Customers Coming Soon Stub**
File: `routes/dashboard_routes.py`
Fix: `/dashboard/customers/` is rendering `coming_soon.html` instead of `customers.html`.
The full `customers()` route function already exists and is correct. A stub route is
overriding it. Find and remove the stub that calls
`render_template("dashboard/coming_soon.html", ...)` for `/dashboard/customers/`.
Keep only the full `customers()` function.
Verified live: `/dashboard/customers/` shows "This section is being built." — confirmed broken.
Result: `/dashboard/customers/` renders real customer data from `customers.html`.
⚠️  PREREQUISITE: Must be deployed before Prompt #5 will work.

**Prompt #3 — Build Customer Detail Page**
Files: `routes/dashboard_routes.py` (new route) + `templates/dashboard/customer_detail.html` (new)
Route: `GET /dashboard/customers/<customer_id>`
Currently returns 404 — no route or template exists.
Route logic:
- `_resolve_client_id()` — redirect `/login` if missing
- `_base_context("customers", client_id)`
- Query `customers`: single record by `id` AND `client_id` — 404 if not found
- Query `jobs`: all for this customer + client, ordered `scheduled_date` desc
- Query `proposals`: all for this customer + client, ordered `created_at` desc, limit 10
- Query `invoices`: all for this customer + client, ordered `created_at` desc, limit 10
- Pass `fmt_date`, `fmt_phone`, `fmt_short_date` helpers
Template: customer name, phone (formatted), address, SMS consent dot (green/grey),
date added, jobs list, proposals list, invoices list — each row links to its detail page.
Back link → `/dashboard/customers/`. Match `job_detail.html` card/dl/badge pattern exactly.
Multi-tenancy: every query must filter by both `customer_id` AND `client_id`.

**Prompt #4 — Split Sales & Payments into Three Separate Pages**
Currently: Estimates, Invoices, Payments all link to `office.html` or `office.html#anchor`.
Anchors go nowhere. Three real pages needed.
Files:
- `routes/dashboard_routes.py` — add 3 new routes
- `templates/dashboard/estimates.html` — new
- `templates/dashboard/invoices.html` — new
- `templates/dashboard/payments.html` — new
- `templates/base.html` — update 3 sidebar hrefs only

Route 1: `GET /dashboard/estimates/` → `estimates_page()`
- Query `proposals` table, last 90 days, ordered `created_at` desc
- `_base_context("estimates", client_id)`
- Compute: `proposals_sent`, `proposals_won`, `win_rate`
- Summary strip: Win Rate, Sent, Accepted, Outstanding
- List: clickable rows → `/dashboard/proposal/{{ p.id }}`, customer name, date, amount, status badge

Route 2: `GET /dashboard/invoices/` → `invoices_page()`
- Query `invoices` table, last 90 days, ordered `created_at` desc
- `_base_context("invoices", client_id)`
- Compute: `total_billed`, `total_paid`, `total_outstanding`
- Summary strip: Billed, Collected, Outstanding, Count
- List: extract directly from `office.html` — same `.list-row` pattern + age pills JS
- Export CSV button → `/api/invoices/export-csv`

Route 3: `GET /dashboard/payments/` → `payments_page()`
- Query `invoices` WHERE `status='paid'`, last 90 days, ordered `paid_at` desc
- `_base_context("payments", client_id)`
- Compute: `total_collected`, `payment_count`
- Summary strip: Total Collected, Payment Count, Average Payment
- List: paid invoices only — customer name, amount, paid date, green badge

base.html sidebar href changes (3 lines only — do not touch anything else in base.html):
  `/dashboard/office.html#estimates` → `/dashboard/estimates/`
  `/dashboard/office.html`           → `/dashboard/invoices/`
  `/dashboard/office.html#payments`  → `/dashboard/payments/`
Update active_page checks to use `'estimates'`, `'invoices'`, `'payments'`.
Do NOT delete or modify `office.html`.

**Prompt #5 — Customers Page: Inline Add Form + Searchable List**
⚠️  BLOCKED: Requires Prompt #2 deployed first. Also requires Backend Engineer
to fix item 7.9 (stub route removal) before results are visible.
File: `templates/dashboard/customers.html` — full rewrite

Layout: Two-column desktop (stacked mobile ≤768px)
- Left column 300px: "New Customer" card — inline add form
- Right column flex 1: "Customers" card — searchable list

Left column — Add Customer card:
- Fields: Full Name, Phone (required, hint: "Required for SMS"), Address, Notes (textarea 3 rows)
- Submit: POST JSON to `/api/customers/create` (same fetch pattern as `new_customer.html`)
- On success: show green "Customer saved", reload page after 1.2s
- On error: show red error inline, re-enable button
- No `<form>` action — JS fetch only

Right column — Customer list:
- Search input (live filter — searches name, phone, address via `data-search` attribute on rows)
- Table: Name, Phone, Address, Jobs, Last Job, SMS dot, arrow →
- Each row onclick → `/dashboard/customers/{{ c.id }}`
- Empty state: "No customers yet. Add your first customer using the form."
- Shows "No customers match your search." when search has no results

Styling: `base.html` CSS variables only. DM Mono labels, Barlow body, flat cards, no shadows.
Do NOT modify: `dashboard_routes.py`, `base.html`, `new_customer.html`, any other file.

---

## 2. Backend Engineer Action Items

### 7.9 — Customers Route Stub Removal (PRIORITY — BLOCKS FRONTEND)
The `/dashboard/customers/` route serves `coming_soon.html` instead of real data.
The full `customers()` function already exists in `routes/dashboard_routes.py` and is correct.
A stub or duplicate route is overriding it.

Action: Find and remove the stub route for `/dashboard/customers/` that renders
`coming_soon.html`. The real `customers()` function must be the only handler for
`@dashboard_bp.route("/dashboard/customers/")`.

Verify: `/dashboard/customers/` shows the customer table with 25 Holt Sewer & Drain
customers, search bar, and clickable rows. Not the stub.

This unblocks: Frontend Prompts #2 and #5.
Owner: Backend Engineer — PRIORITY this session

### 7.10 — Payments Page: Square Write-Back Verification
The new `/dashboard/payments/` page (Prompt #4) reads from `invoices WHERE status='paid'`.
When Square goes live, confirm `routes/invoice_routes.py` Square webhook writes back
`status='paid'` and `paid_at = now()` on payment confirmation. If not, add the write-back.
Without this, the Payments page will show no data even after real payments are received.
Owner: Backend Engineer — unblock after Square production switch

### 7.11 — Remove Debug Logging from auth_routes.py (carry-forward)
Temporary PIN debug print statements still active in the `/login` route.
Remove before any customer demo or production handoff.
Owner: Backend Engineer

---

## 3. Production Status — What Works Right Now

- [x] Login with phone + PIN → session → dashboard loads
- [x] Sidebar nav persists across all pages
- [x] Control Board: real jobs today, invoices outstanding, SMS count, team panel
- [x] Office page `/dashboard/office.html`: invoices + proposals, clickable rows
- [x] Proposal document view: line items, action buttons (accept/lost/send)
- [x] Invoice document view: Mark Paid, paid banner, line items
- [x] Command Center: direct agent dispatch, Haiku classification
- [x] New Job form `/dashboard/new-job`: customer dropdown, proposal checkbox, creates job
- [x] Add Customer form `/dashboard/customers/new`: POSTs to `/api/customers/create`
- [x] Export CSV `/api/invoices/export-csv`: QuickBooks-compatible, download works
- [x] Job detail route `/dashboard/job/<id>`: customer, proposals, invoices, activity
- [x] 25 test customers imported for Holt Sewer & Drain

- [ ] Control Board job "View →" links — `href="#"` broken (Prompt #1)
- [ ] `/dashboard/customers/` — showing coming_soon stub (Backend 7.9 + Prompt #2)
- [ ] `/dashboard/customers/<id>` — 404 no route (Prompt #3)
- [ ] `/dashboard/estimates/` — doesn't exist yet (Prompt #4)
- [ ] `/dashboard/invoices/` — doesn't exist yet (Prompt #4)
- [ ] `/dashboard/payments/` — doesn't exist yet (Prompt #4)
- [ ] Customers page inline add form — not built (Prompt #5, blocked on #2)

---

## 4. Known Issues — Carry-Forward

1. **10DLC not approved** — outbound SMS blocked. Agents run correctly, SMS fails silently.
2. **Square in sandbox** — PAY NOW works but hits sandbox. Switch to production when ready.
3. **Customer SMS opt-in** — all 25 imported customers have `sms_consent=false`. Need
   SET OPTIN per customer or bulk update before SMS goes live.
4. **Onboarding wizard** — built but not tested end-to-end. Personality MD generation unverified.
5. **Pricing benchmarks** — `sql/pricing_benchmarks.sql` written, may not be run in Supabase yet.
6. **Purchases / Bills / Vendors** — stub routes exist, no Supabase schema. Backend must design
   schema before frontend can build these pages.
7. **Receipts** — stub route exists, depends on Purchases schema (item 6).
8. **Accounting / Transactions** — stub route exists, depends on Purchases schema (item 6).
   CSV export already wired at `/api/invoices/export-csv`.

---

## 5. Architecture Reference

### Stack
```
Backend:   Python 3.12 / Flask
Database:  Supabase (PostgreSQL)
SMS:       Telnyx (10DLC pending — blocked)
AI:        Claude Haiku (classification) + Claude Sonnet (generation)
Payments:  Square (sandbox)
Deploy:    Railway (auto-deploy on GitHub push)
Auth:      Phone + 4-digit PIN → Flask session (30-day lifetime)
```

### Key Credentials
```
Railway URL:   https://web-production-043dc.up.railway.app
API domain:    https://api.bolts11.com (DNS may not be pointed yet)
GitHub repo:   329andalive/Agent_Work_flow
Client ID:     8aafcd73-b41c-4f1a-bd01-3e7955798367
Business:      Holt Sewer & Drain
Owner phone:   +12074190986 (Telnyx number)
Owner mobile:  +12076538819 (Jeremy's cell)
Supabase URL:  https://wczzlvhpryufohjwmxwd.supabase.co
FLASK_ENV:     development (Railway — allows dev bypass)
```

### URL Map
```
/login                      — Phone + PIN auth
/logout                     — Clear session
/dashboard/                 — Control Board
/dashboard/office.html      — Office summary (keep — do not delete)
/dashboard/estimates/       — Estimates list (NEW — Prompt #4)
/dashboard/invoices/        — Invoices list (NEW — Prompt #4)
/dashboard/payments/        — Paid invoices (NEW — Prompt #4)
/dashboard/customers/       — Customer list (broken — Backend 7.9 + Prompts #2 #5)
/dashboard/customers/new    — Add Customer standalone (keep as fallback)
/dashboard/customers/<id>   — Customer detail (NEW — Prompt #3)
/dashboard/job/<id>         — Job detail (links broken — Prompt #1)
/dashboard/proposal/<id>    — Proposal document view
/dashboard/invoice/<id>     — Invoice document view
/dashboard/command.html     — Command Center
/dashboard/new-job          — New Job form
/dashboard/onboarding.html  — Client onboarding admin
/api/customers/create       — POST: create customer
/api/jobs/create            — POST: create job
/api/invoices/export-csv    — GET: QuickBooks CSV export
/api/command                — POST: Command Center agent dispatch
/book                       — Public booking form (no auth)
```

### File Map
```
execution/sms_receive.py        — Flask app entry point, blueprint registration
routes/dashboard_routes.py      — All dashboard page routes (main file this session)
routes/auth_routes.py           — /login, /logout, /set-pin
routes/command_routes.py        — /api/command direct agent dispatch
templates/base.html             — Shared sidebar + layout (navy/amber)
templates/dashboard/
  control.html                  — Control Board
  office.html                   — Office summary (keep)
  command.html                  — Command Center chat
  customers.html                — Customer list (rewrite — Prompt #5)
  customer_detail.html          — Customer detail (NEW — Prompt #3)
  estimates.html                — Estimates (NEW — Prompt #4)
  invoices.html                 — Invoices (NEW — Prompt #4)
  payments.html                 — Payments (NEW — Prompt #4)
  job_detail.html               — Job detail
  proposal_view.html            — Proposal document
  invoice_view.html             — Invoice document
  new_job.html                  — New Job form
  new_customer.html             — Add Customer standalone (keep as fallback)
  onboarding.html               — Client onboarding admin
  coming_soon.html              — Stub for unbuilt sections
CLAUDE.md                       — Master architecture doc (read first every session)
```

---

## 6. Prompt Run Order — Claude Code

| # | Prompt | Files | Status |
|---|--------|-------|--------|
| 1 | Wire job "View →" links | `control.html` | Ready — run now |
| 2 | Remove customers stub | `dashboard_routes.py` | Ready — run now |
| 3 | Customer detail page | `dashboard_routes.py` + new `customer_detail.html` | Ready — run now |
| 4 | Estimates / Invoices / Payments pages | `dashboard_routes.py` + 3 templates + `base.html` | Ready — run now |
| 5 | Customers page inline add + search | `customers.html` rewrite | Blocked — needs #2 deployed + Backend 7.9 |

---

## Session Update — March 23, 2026 — Backend Engineer

### 7.10 — Square Payment Write-Back Verification

**Audit result:** Write-back logic in invoice_routes.py and token_generator.py
was structurally correct. Three gaps identified and fixed:

**Gap 1 — Schema:** `square_payment_id` column missing from `invoices` table.
  - Created `sql/square_payment_writeback.sql` — run this in Supabase SQL editor
    before switching Square to production.
  - Added two-pass fallback to `mark_invoice_paid()` so a real payment is never
    silently dropped if the column is missing.

**Gap 2 (CRITICAL) — Missing wire:** `attach_payment_link()` was never called
after `square_agent.create_payment_link()`. The `invoice_links.square_order_id`
column was never populated, so the webhook reverse-lookup always returned None.
  - Fixed in `execution/invoice_agent.py` — Step 8b now creates a Square payment
    link and calls `attach_payment_link()` to wire `square_order_id` to the
    `invoice_links` row. Non-fatal if Square is not configured.

**Gap 3 — Multi-tenancy:** Confirmed safe. No change needed.

**To go live with Square production:**
1. Run `sql/square_payment_writeback.sql` in Supabase SQL editor
2. Set Railway env vars: SQUARE_ACCESS_TOKEN, SQUARE_ENVIRONMENT=production,
   SQUARE_LOCATION_ID, SQUARE_WEBHOOK_SIGNATURE_KEY, SQUARE_WEBHOOK_URL
3. Register webhook in Square Dashboard: POST /webhooks/square, event: payment.completed
4. Test with a sandbox payment — confirm invoice status flips to 'paid' in Supabase
   and appears in /dashboard/payments/