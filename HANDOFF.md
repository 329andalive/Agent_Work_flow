# HANDOFF.md — Bolts11 Session Log
> Last updated: March 24, 2026 — Backend Engineer
> Read CLAUDE.md first every session before touching any code.

---

## Current Production Status

```
Railway URL:   https://web-production-043dc.up.railway.app
API domain:    https://api.bolts11.com (DNS may not be pointed yet)
GitHub repo:   329andalive/Agent_Work_flow
Python:        3.12.9 (pinned via .python-version — DO NOT remove this file)
Deploy:        Auto-deploy on push to main
Build status:  ✅ GREEN as of March 24, 2026 — gunicorn 25.1.0 booting clean
```

---

## What Works Right Now — Confirmed in Production

### Dashboard & Navigation
- [x] Login with phone + PIN → session → dashboard loads
- [x] Sidebar nav — Wave-style collapsible, navy/amber, persists across all pages
- [x] Control Board `/dashboard/` — jobs today, invoices outstanding, SMS count, team
- [x] Control Board job "View →" links wired to `/dashboard/job/<id>`
- [x] Job detail `/dashboard/job/<id>` — customer, proposals, invoices, activity
- [x] Office page `/dashboard/office.html` — invoices + proposals, clickable rows

### Sales & Payments (3 standalone pages)
- [x] Estimates `/dashboard/estimates/` — proposals list with win rate metrics
- [x] Invoices `/dashboard/invoices/` — invoices list with Export CSV + age pills
- [x] Payments `/dashboard/payments/` — paid invoices only with collection metrics
- [x] New Estimate form `/dashboard/estimates/new` — customer/job dropdown, scope, amount
- [x] New Invoice form `/dashboard/invoices/new` — customer/job dropdown, amount, due date

### Customers
- [x] Customer list `/dashboard/customers/` — metrics, search, data table with SMS dots
- [x] Customer detail `/dashboard/customers/<id>` — profile, jobs, proposals, invoices
- [x] Add Customer form `/dashboard/customers/new` — POSTs to `/api/customers/create`

### Documents & Views
- [x] Proposal document view `/dashboard/proposal/<id>` — line items, accept/lost/send
- [x] Invoice document view `/dashboard/invoice/<id>` — Mark Paid, paid banner

### Forms & APIs
- [x] New Job form `/dashboard/new-job` — customer dropdown, proposal checkbox
- [x] POST `/api/customers/create` — phone normalization, duplicate check, Hard Rules #1 + #2
- [x] POST `/api/jobs/create` — synchronous proposal_agent trigger
- [x] POST `/api/invoices/create` — draft invoice creation
- [x] POST `/api/estimates/create` — draft estimate creation
- [x] GET `/api/invoices/export-csv` — QuickBooks-compatible download

### Command Center & Agent Intelligence
- [x] Command Center `/dashboard/command.html` — direct agent dispatch
- [x] Context loader wired — `load_context()` runs on every command before routing
- [x] Haiku classification uses recent thread + active jobs for context-aware intent
- [x] Fuzzy customer matching — "seekings" → "Seekins", prefix-based fallback
- [x] Soft customer failure — returns clarification message instead of crashing
- [x] Owner phone guard — owner_mobile never creates a customer record
- [x] Stale clarification detection — new job messages kill expired sessions

### Proposal Agent (rebuilt)
- [x] Structured JSON output — line items as `{"description", "amount"}` array
- [x] Job summarization via Haiku — clean 1-line descriptions, raw input preserved
- [x] Explicit pricing rules — pump $275, baffle $175, labor $125/hr, never $0.00
- [x] Owner-as-customer guard — aborts if customer_phone matches owner phones
- [x] Name-based customer search when no phone provided

### Infrastructure
- [x] 25 test customers imported for Holt Sewer & Drain
- [x] `squareup==44.0.1.20260122` installed and pinned
- [x] All 66 dependencies fully pinned in requirements.txt
- [x] Python 3.12.9 pinned via `.python-version`
- [x] Square SDK v44 import fixed (`from square import Client`)
- [x] Debug prints removed from auth_routes.py login handler
- [x] Defensive job_cost_agent — schema-missing errors log actionable message

---

## What Was Built This Session (March 23-24, 2026)

### 20 commits pushed to main — full list:

**Dashboard Templates (7.9 — RESOLVED)**
- `26427f4` — Created all 6 missing templates: customers.html, customer_detail.html,
  job_detail.html, estimates.html, invoices.html, payments.html
- `825c353` — Standalone Estimates, Invoices, Payments pages with routes
- Control Board job "View →" links wired from `href="#"` to `/dashboard/job/{{ j.id }}`

**New Create Forms**
- `4d62d99` — New Invoice page `/dashboard/invoices/new` with customer/job linking
- `8e30ce7` — New Estimate page `/dashboard/estimates/new` with customer/job linking

**Square Payment Pipeline (7.10 — CODE COMPLETE)**
- `a6a3a1f` — Step 8b wired in invoice_agent.py — Square payment link → invoice_links
- `a6a3a1f` — mark_invoice_paid() two-pass fallback in token_generator.py
- `a6a3a1f` — sql/square_payment_writeback.sql created
- `48cb9c3` — Square SDK v44 import fix (`from square import Client`)
- `48cb9c3` — Defensive job_cost_agent (schema-missing → actionable warning)

**Proposal Agent Rebuild (Prompt #8-9)**
- `c2c87be` — Customer resolution: owner phone guard, name-based DB search
- `c2c87be` — Job summarization via Haiku (clean 1-line descriptions)
- `c2c87be` — Structured JSON line items with explicit pricing rules
- `92079c1` — Stale clarification poisoning fix + owner-as-customer guard
- `92079c1` — Explicit pricing in Sonnet prompt ($275 pump, $175 baffle, $125/hr)

**Stateful Context Architecture**
- `5bdcdf6` — Created `execution/context_loader.py` — assembles tech/client/jobs/thread/pending
- `a1ea12d` — Wired context_loader into command_routes.py handle_command()
- `f215f3f` — Context-aware Haiku (recent thread + active jobs in classify prompt)
- `f215f3f` — Fuzzy customer matching (prefix-based fallback for misspellings)
- `f215f3f` — Soft customer failure (clarification message instead of ValueError crash)

**Personality & Auth**
- `cd55828` — personality.md rewritten with document standards and voice rules
- `2208ec4` — Simplified owner name to first name only
- `26427f4` — Removed all DEBUG prints from auth_routes.py (7.11 — RESOLVED)

---

## Remaining Action Items

### Manual — Run SQL Migrations in Supabase
Two SQL files need to be run in Supabase SQL editor:

**1. Square payment columns** (`sql/square_payment_writeback.sql`):
```sql
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS square_payment_id TEXT;
ALTER TABLE invoice_links ADD COLUMN IF NOT EXISTS square_order_id TEXT;
ALTER TABLE invoice_links ADD COLUMN IF NOT EXISTS square_payment_link_id TEXT;
ALTER TABLE invoice_links ADD COLUMN IF NOT EXISTS payment_link_url TEXT;
-- Plus indexes. Full file at sql/square_payment_writeback.sql
```

**2. Job costing table** (`directives/supabase_migration_001.sql`):
The `job_costs` table may not exist yet. Run this migration to enable
job cost tracking on every invoice.

### Square Production Go-Live Checklist
Code is complete. Remaining steps are configuration:
1. Run `sql/square_payment_writeback.sql` in Supabase
2. Set Railway env vars: `SQUARE_ACCESS_TOKEN`, `SQUARE_ENVIRONMENT=production`,
   `SQUARE_LOCATION_ID`, `SQUARE_WEBHOOK_SIGNATURE_KEY`, `SQUARE_WEBHOOK_URL`
3. Register webhook in Square Dashboard: `payment.completed` → `/webhooks/square`
4. Test with sandbox payment first

---

## Architecture Reference

### Stack
```
Backend:     Python 3.12.9 / Flask 3.1.3
Database:    Supabase (PostgreSQL)
SMS:         Telnyx (10DLC pending — all outbound SMS blocked)
AI:          Anthropic Claude — Haiku for classification, Sonnet for generation
Payments:    Square 44.0.1 (sandbox — code complete, needs config)
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
/login                        — Phone + PIN auth
/logout                       — Clear session
/dashboard/                   — Control Board ✅
/dashboard/office.html        — Office summary (keep — do not delete) ✅
/dashboard/estimates/         — Estimates list ✅
/dashboard/estimates/new      — New Estimate form ✅
/dashboard/invoices/          — Invoices list ✅
/dashboard/invoices/new       — New Invoice form ✅
/dashboard/payments/          — Paid invoices list ✅
/dashboard/customers/         — Customer list + search ✅
/dashboard/customers/new      — Add Customer standalone ✅
/dashboard/customers/<id>     — Customer detail ✅
/dashboard/job/<id>           — Job detail ✅
/dashboard/proposal/<id>      — Proposal document view ✅
/dashboard/invoice/<id>       — Invoice document view ✅
/dashboard/command.html       — Command Center ✅
/dashboard/new-job            — New Job form ✅
/dashboard/onboarding.html    — Client onboarding admin ✅
/dashboard/purchases/         — Coming soon (stub)
/dashboard/receipts/          — Coming soon (stub)
/dashboard/accounting/        — Coming soon (stub)
/api/customers/create         — POST: create customer ✅
/api/jobs/create              — POST: create job ✅
/api/invoices/create          — POST: create invoice ✅
/api/estimates/create         — POST: create estimate ✅
/api/invoices/export-csv      — GET: QuickBooks CSV export ✅
/api/command                  — POST: Command Center dispatch ✅
/webhooks/telnyx              — SMS webhook ✅
/webhooks/square              — Square payment webhook ✅
/book                         — Public booking form (no auth) ✅
```

### File Map
```
execution/sms_receive.py         — Flask app entry point, all blueprint registration
execution/proposal_agent.py      — Proposal generation (structured JSON output, Haiku summarization)
execution/invoice_agent.py       — Invoice generation + Square Step 8b
execution/context_loader.py      — Stateful context assembly (NEW — wired into command_routes)
execution/token_generator.py     — Token generation + mark_invoice_paid() (two-pass fallback)
execution/square_agent.py        — Square Payment Links API (v44 import fixed)
execution/job_cost_agent.py      — Job cost tracking (defensive — warns if table missing)
execution/db_clarification.py    — Clarification DB ops + cleanup_expired()
execution/sms_router.py          — SMS routing (stale clarification detection added)
routes/dashboard_routes.py       — All dashboard page routes (17 templates served)
routes/auth_routes.py            — /login, /logout, /set-pin (debug prints removed ✅)
routes/invoice_routes.py         — Square webhook handler
routes/command_routes.py         — /api/command — context loader + fuzzy matching + soft failure
templates/base.html              — Shared sidebar + layout (navy/amber)
templates/dashboard/
  control.html                   — Control Board ✅
  office.html                    — Office summary ✅
  command.html                   — Command Center ✅
  customers.html                 — Customer list + search + metrics ✅
  customer_detail.html           — Customer profile ✅
  job_detail.html                — Job detail ✅
  estimates.html                 — Estimates list + metrics ✅
  invoices.html                  — Invoices list + CSV export ✅
  payments.html                  — Paid invoices ✅
  new_estimate.html              — New Estimate form ✅
  new_invoice.html               — New Invoice form ✅
  new_job.html                   — New Job form ✅
  new_customer.html              — Add Customer standalone ✅
  proposal_view.html             — Proposal document ✅
  invoice_view.html              — Invoice document ✅
  onboarding.html                — Client onboarding ✅
  coming_soon.html               — Stub for unbuilt sections ✅
directives/clients/personality.md — Holt Sewer & Drain voice, pricing, document rules
directives/agents/proposal_agent.md — Proposal agent architecture + line item rules
sql/square_payment_writeback.sql — Square schema migration (run in Supabase)
.python-version                  — Pins Python 3.12.9 — DO NOT DELETE
requirements.txt                 — 66 pinned packages
CLAUDE.md                        — Master architecture doc — read every session
```

### Known Issues — Carry-Forward
1. **10DLC not approved** — outbound SMS blocked. Agents run, SMS fails silently.
2. **Square in sandbox** — code complete, needs config + SQL migration to go live.
3. **Customer SMS opt-in** — all 25 test customers have `sms_consent=false`.
4. **Onboarding wizard** — built but not tested end-to-end in production.
5. **Pricing benchmarks** — `sql/pricing_benchmarks.sql` written, may not be run yet.
6. **Purchases / Bills / Vendors** — stub routes exist, no Supabase schema designed.
7. **Receipts** — stub route exists, depends on Purchases schema.
8. **Accounting / Transactions** — stub route exists, depends on Purchases schema.
9. **job_costs table** — may not exist in Supabase. Run `directives/supabase_migration_001.sql`.
