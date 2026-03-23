# HANDOFF.md — Session Summary
> Last updated: March 23, 2026
> Session: Dashboard redesign, new job page, add customer page, brand identity

---

## 1. What Was Built This Session

### New Files Created
```
templates/base.html                    — Jinja2 base with persistent sidebar nav
templates/dashboard/control.html       — Control Board (jobs today, invoices, SMS, team)
templates/dashboard/command.html       — Command Center (chat + activity sidebar)
templates/dashboard/office.html        — Office/Billing (invoices + proposals tables)
templates/dashboard/onboarding.html    — Client onboarding admin
templates/dashboard/proposal_view.html — Full proposal document view with action buttons
templates/dashboard/invoice_view.html  — Full invoice document view with action buttons
templates/login.html                   — Phone + PIN login form
templates/set_pin.html                 — First-time PIN setup form
routes/dashboard_routes.py             — Blueprint: all dashboard pages with Supabase queries
routes/auth_routes.py                  — Blueprint: /login, /logout, /set-pin
scripts/import_customers.py            — Bulk CSV customer import tool
scripts/test_customers.csv             — 25 Waldo County test customers
```

### Files Modified
```
execution/sms_receive.py      — Registered dashboard_bp + auth_bp, added SECRET_KEY + session lifetime
execution/invoice_agent.py     — 5 fixes: field text rewrite, Haiku line items, clean job notes,
                                 customer resolution without owner_mobile fallback, flat rate detection,
                                 tech confirmation SMS, specific error messages
execution/proposal_agent.py    — Human-readable output_summary
execution/clarification_agent.py — Human-readable output_summary, opt-in check text
execution/clock_agent.py       — Human-readable output_summary
execution/scheduling_agent.py  — Human-readable output_summary
execution/followup_agent.py    — Human-readable output_summary (5 locations)
execution/noshow_agent.py      — Human-readable output_summary
execution/db_customer.py       — _extract_name_from_text helper (unused import cleaned)
routes/command_routes.py       — Complete rewrite: bypasses SMS router, direct agent dispatch,
                                 Haiku intent classification, customer name resolution from DB,
                                 specific result messages
routes/document_routes.py      — Human-readable output_summary
routes/invoice_routes.py       — Human-readable output_summary
routes/dashboard_routes.py     — Schema-verified queries, fmt_date/fmt_phone/fmt_activity_time
                                 helpers, customer name map, document view routes
templates/dashboard/office.html — Clickable rows, customer names, short dates, View → arrows
templates/dashboard/control.html — Redesigned: card-list jobs (no IDs),
                                   summary strip, amber invoice alert, team panel (name + role only)
templates/dashboard/office.html  — Redesigned: flex-list invoices + proposals,
                                   summary strip, Export CSV button wired, age pills via JS
templates/base.html              — Bolts11 brand: navy sidebar, amber active states, navy topbar
routes/dashboard_routes.py       — New Job page, Add Customer page, proposal_agent wiring
templates/dashboard/new_job.html — New Job form with proposal checkbox
templates/dashboard/new_customer.html — Add Customer form with phone normalization
```

---

## 2. Current Architecture

### Stack
- **Backend:** Python 3.12 / Flask
- **Database:** Supabase (PostgreSQL)
- **SMS:** Telnyx (10DLC pending — SMS currently blocked)
- **AI:** Anthropic Claude (Haiku for classification, Sonnet for generation)
- **Payments:** Square (sandbox — not yet wired to production)
- **Deploy:** Railway (auto-deploy from GitHub push)
- **Auth:** Phone + 4-digit PIN → Flask session (30-day lifetime)

### URL Structure
```
Production:  https://web-production-043dc.up.railway.app
Domain:      https://api.bolts11.com (when DNS pointed)

/login                          — Phone + PIN auth
/logout                         — Clear session
/dashboard/                     — Control Board
/dashboard/office.html          — Invoices + Proposals
/dashboard/command.html         — Command Center (chat)
/dashboard/onboarding.html      — Client onboarding
/dashboard/proposal/<id>        — Proposal document view
/dashboard/invoice/<id>         — Invoice document view
/command                        — Command Center (alias)
/book                           — Public booking form (no auth)
/onboard/<token>                — Client setup wizard (no auth)
/api/command                    — Command Center API (direct agent dispatch)
/api/client/config              — Client config for dashboards
/api/activity                   — Agent activity feed
/api/stats                      — Open jobs, SMS status
/webhooks/telnyx                — Primary SMS webhook
/webhooks/square                — Square payment webhook
/p/<token>                      — Public proposal view (72hr expiry)
/i/<token>                      — Public invoice view (72hr expiry)
```

### Supabase Tables in Use
```
clients              — id, business_name, owner_name, phone, owner_mobile, personality, active, pin_hash
jobs                 — id, client_id, customer_id, job_type, status, scheduled_date, raw_input, job_notes
customers            — id, client_id, customer_name, customer_phone, customer_address, sms_consent
invoices             — id, client_id, customer_id, job_id, amount_due, status, invoice_text, line_items, edit_token
proposals            — id, client_id, customer_id, job_id, amount_estimate, status, proposal_text, line_items, edit_token
employees            — id, client_id, name, phone, role, active
messages             — id, client_id, direction, from_number, to_number, body, delivery_status
agent_activity       — id, client_phone, agent_name, action_taken, output_summary, sms_sent
invoice_links        — id, token, job_id, client_phone, type, expires_at, viewed_at
pending_clarifications — id, client_id, employee_phone, stage, collected_intent, expires_at
customer_approvals   — id, client_id, customer_id, job_id, tech_phone, estimate_amount, status
onboarding_sessions  — id, client_id, token, status, step_reached, company_name, personality_md
```

---

## 3. What Works — Confirmed in Production

- [x] Login with phone + PIN → session set → dashboard loads
- [x] Sidebar nav persists across all pages
- [x] Control Board: real jobs, invoices, SMS count, team data
- [x] Office: clickable invoice/proposal rows with customer names
- [x] Proposal document view: full layout with customer info, line items, action buttons
- [x] Invoice document view: same with Mark Paid, paid banner
- [x] Command Center: direct agent dispatch, no clarification loop
- [x] Command Center: Haiku classifies ambiguous commands
- [x] Customer name resolution: extracts name from text, searches DB by ilike
- [x] Invoice agent: flat rate detection for "$275" in natural language
- [x] Invoice agent: Haiku extracts clean line items from Claude output
- [x] Invoice agent: clean job notes (never stores raw field text)
- [x] Invoice agent: tech confirmation SMS when triggered from field
- [x] All output_summary strings are human-readable (activity feed)
- [x] 25 test customers imported for Holt Sewer & Drain
- [x] Logout clears session, redirects to /login
- [x] No auth → /login redirect (production mode)

---

## 4. Known Remaining Issues

1. **10DLC not approved** — all outbound SMS blocked. Agents execute correctly but SMS fails silently. Dashboard shows "SMS BLOCKED" badge.

2. **Debug logging in auth_routes.py** — temporary PIN debug prints still in login route (from Railway PIN mismatch debugging). Should be removed before production.

3. **Square payments in sandbox** — square_agent.py and invoice template PAY NOW button work but point to Square sandbox. Need to switch to production when ready.

4. ~~**No "New Job" button**~~ — RESOLVED. New Job page at /dashboard/new-job with proposal generation checkbox.

5. **Customer opt-in (sms_consent)** — all 25 imported customers have sms_consent=false. Need to opt them in via SET OPTIN command or bulk update before SMS goes live.

6. **Onboarding wizard** — built but not tested end-to-end in production. Personality MD generation via Claude Sonnet not verified.

7. **Pricing benchmarks SQL** — sql/pricing_benchmarks.sql has been written but may not have been run in Supabase yet. 125 benchmark rows across 9 verticals.

8. **Export CSV route not implemented** — Button wired in office.html at /api/invoices/export-csv but route does not exist yet. See section 7.1.

---

## 5. Next Build: New Job Button

User flow for the "New Job" feature on the Control Board:

1. Owner clicks "+ New Job" button on control board
2. Modal or slide-out form appears with fields:
   - Customer (searchable dropdown from customers table)
   - Job Type (select: pump, repair, inspect, etc.)
   - Address (auto-fills from customer record)
   - Scheduled Date + Time
   - Notes
3. On submit: POST to /api/jobs/create
4. Backend creates job record in Supabase
5. If scheduled_date = today → appears immediately in today's table
6. Optionally: trigger proposal_agent to generate an estimate

---

## 6. Key Facts for Next Session

```
Railway URL:     https://web-production-043dc.up.railway.app
API domain:      https://api.bolts11.com (DNS may not be pointed yet)
GitHub repo:     329andalive/Agent_Work_flow
Client ID:       8aafcd73-b41c-4f1a-bd01-3e7955798367
Business:        Holt Sewer & Drain
Owner phone:     +12074190986 (Telnyx number)
Owner mobile:    +12076538819 (Jeremy's cell)
Supabase URL:    https://wczzlvhpryufohjwmxwd.supabase.co
FLASK_ENV:       Set to "development" on Railway for dev bypass

Stack:           Python 3.12, Flask, Supabase, Telnyx, Square, Railway
AI:              Claude Haiku (classification), Claude Sonnet (generation)
Auth:            Phone + 4-digit PIN → Flask session (30 days)
Templates:       Jinja2 extending templates/base.html
Blueprints:      dashboard_bp, auth_bp, command_bp, document_bp,
                 invoice_bp, onboarding_bp, debug_bp
```

### File Map
```
execution/sms_receive.py       — Flask app entry point, all blueprint registration
execution/invoice_agent.py     — Invoice generation (most complex agent)
execution/proposal_agent.py    — Proposal/estimate generation
execution/sms_router.py        — SMS routing (NOT used by Command Center)
routes/dashboard_routes.py     — All dashboard page routes
routes/command_routes.py       — /api/command (direct agent dispatch)
routes/auth_routes.py          — /login, /logout, /set-pin
templates/base.html            — Shared sidebar + layout
CLAUDE.md                      — Master architecture doc (read first)
```

---

## 7. Deferred Backend Tasks

These items were identified during frontend work and need backend implementation.
Each is blocked on a route or data change that does not exist yet.

### 7.1 QuickBooks / CSV Export — PRIORITY
Route needed: GET /api/invoices/export-csv
  - Query: all invoices for client_id (last 90 days or all-time with ?range param)
  - Join: customers table for customer_name, customer_phone, customer_address
  - Join: jobs table for job_type, scheduled_date
  - Output: CSV with columns:
      Invoice Date, Customer Name, Customer Address, Job Type,
      Amount Due, Amount Paid, Status, Paid Date
  - Format: UTF-8 CSV, Content-Disposition: attachment; filename="bolts11-invoices.csv"
  - Auth: session-protected (client_id from session, not query param)
  - QuickBooks compatibility: column names should match QB import format where possible.
    QB expects: Date, Description, Amount, Customer, Memo
    Map: Invoice Date→Date, Job Type→Description, Amount Due→Amount,
         Customer Name→Customer, "Invoice #XXXX"→Memo
  - Future: add /api/proposals/export-csv on same pattern
  Wired in: templates/dashboard/office.html Export CSV button
  Owner: Backend Engineer

### 7.2 Job Detail Page
Route needed: GET /dashboard/job/<job_id>
  - Currently "View →" links on control.html job rows point to "#" (placeholder)
  - Page should show: customer name + phone, job type, address, status,
    scheduled date, notes, linked proposals, linked invoices, agent activity
  - Same base.html sidebar layout
  Owner: Frontend Engineer (once route exists)

### 7.3 Invoice Send Action from Office Page
  - office.html invoice rows link to /dashboard/invoice/<id> (detail view)
  - The invoice detail view has a "Send" button but it shows
    "SMS sending queued. Will send when 10DLC is active."
  - Once 10DLC is approved: wire the send action to actually dispatch
    via Telnyx. Unblock in invoice_routes.py action handler.
  Owner: Backend Engineer — unblock after 10DLC approval

### 7.4 Remove Debug Logging from auth_routes.py
  - Temporary PIN debug print statements still active in login route
  - Remove before any customer demo or production handoff
  Owner: Backend Engineer
