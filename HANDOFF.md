# HANDOFF.md — Bolts11 Session Log
> Last updated: March 26, 2026 — Backend Engineer
> Read CLAUDE.md first every session before touching any code.

---

## Session — March 26, 2026

### Fixed
- route_assignments: dropped FK constraints, added client_id, dispatch_date columns
- dispatch_log: dropped NOT NULL on client_phone, dispatch_date
- Worker route page: jobs now load correctly from route_assignments
- UUID bug: get_suggestions() now receives client_id not client_phone
- Route expiry: uses expires_at not calendar date
- Workers UI: /dashboard/workers — add/edit/deactivate
- DONE/BACK/PARTS/NOSHOW/SCOPE SMS handler fully wired
- DONE auto-creates draft invoice when estimated_amount exists
- Dispatch board: state persists after Send Routes (no snap-back)

### Next session priorities
1. Add estimated_amount to test jobs so auto-invoice fires end-to-end
2. Dashboard — surface draft invoices with Review & Send button
3. Control board — completed jobs count today
4. Dispatch board patches from dispatch_patches.py still need applying

### call_claude.py — Technical Debt Notes

**Risk 1 — Model IDs will go stale (medium)**
MODEL_MAP in call_claude.py has hardcoded model IDs (claude-haiku-4-5-20251001,
claude-sonnet-4-6, claude-opus-4-6). When Anthropic releases new versions, agents
silently keep calling old models. A client onboarding in 6 months gets the March
2026 model unless someone manually updates this file.
Fix: Move MODEL_MAP to .env or a config table in Supabase so updates don't need a deploy.

**Risk 2 — 1024 tokens will truncate invoices (high)**
DEFAULT_MAX_TOKENS = 1024. Invoice prompts generate itemized invoices with addresses,
line items, payment terms — pushing 800-900 tokens. A large job with 6+ line items
will get cut off mid-sentence. parse_invoice_total() returns 0.0 and final_amount
falls back to actual_amount — math survives but the invoice text sent to the owner
is broken.
Fix: Invoices and proposals should call with max_tokens=2048. Haiku classification
calls can stay at 512. Pass explicitly at call site, not as global default.

**Risk 3 — No cost logging (low now, high at scale)**
Token usage is printed to logs but never written to the database. One client is fine.
At 10 clients running 50 jobs/day, no way to know which client costs most or catch
a runaway agent burning tokens in a loop.
Fix: Add claude_usage_log table — client_id, agent_name, model, input_tokens,
output_tokens, created_at. Write on every call.

**Risk 4 — Single-turn only (low now)**
Every call is stateless — one system + one user message. Correct for current agents.
Risk is if voice controller or multi-turn clarification gets built, someone will
stuff conversation history into the user prompt as a wall of text.
Fix: Nothing now. Know that call_claude.py will need a messages list variant for
voice/multi-turn. Don't hack around it with string concatenation.

---

## Current Production Status

```
Railway URL:   https://web-production-043dc.up.railway.app
API domain:    https://api.bolts11.com (DNS may not be pointed yet)
GitHub repo:   329andalive/Agent_Work_flow
Python:        3.12.9 (pinned via .python-version — DO NOT remove this file)
Deploy:        Auto-deploy on push to main
Build status:  ✅ GREEN as of March 25, 2026 — gunicorn 25.1.0 booting clean
```

---

## What Works Right Now

### Dashboard & Navigation
- [x] Login with phone + PIN → session → dashboard loads
- [x] Sidebar nav — Wave-style collapsible, navy/amber
- [x] Control Board `/dashboard/` — jobs today, invoices, SMS, team
- [x] Job detail `/dashboard/job/<id>` — customer, proposals, invoices, activity
- [x] Office page `/dashboard/office.html` — invoices + proposals
- [x] Super admin heartbeat `/dashboard/admin` — all clients, activity, SMS stats

### Sales & Payments
- [x] Estimates `/dashboard/estimates/` — proposals list with win rate metrics
- [x] Invoices `/dashboard/invoices/` — invoices list with Export CSV + age pills
- [x] Payments `/dashboard/payments/` — paid invoices with collection metrics
- [x] New Estimate form `/dashboard/estimates/new`
- [x] New Invoice form `/dashboard/invoices/new`

### Customers
- [x] Customer list `/dashboard/customers/` — metrics, search, SMS dots
- [x] Customer detail `/dashboard/customers/<id>` — profile, jobs, proposals, invoices
- [x] Add Customer `/dashboard/customers/new`

### Scheduling & Dispatch (NEW — Prompts 1-8)
- [x] Geocoding — `execution/geocode.py` — address → lat/lng + Somerset County zone
- [x] SMS message logging — `sms_send.py` logs all outbound to `sms_message_log`
- [x] Scheduling DB helpers — `db_scheduling.py` — 7 multi-tenant functions
- [x] Dispatch board `/dashboard/dispatch` — drag-drop UI with geo-sorted queue
- [x] Dispatch API — `POST /api/dispatch/assign` + `POST /api/dispatch/send`
- [x] Worker route page `/r/<token>` — mobile job sheet with Maps tap
- [x] Worker SMS replies — DONE/BACK/PARTS/NOSHOW/SCOPE status updates
- [x] AI dispatch suggestions — `dispatch_suggestion.py` Phase 2 (30+ sessions)

### Classes & Booking (NEW — Prompts 9-14)
- [x] Classes dashboard `/dashboard/classes` — slot management, capacity badges
- [x] Public booking page `/book/<board_token>` — returning customer one-tap
- [x] Slot cancellation — notifies enrolled + promotes waitlist
- [x] Scheduled SMS — nudges, appointment reminders, no-show marking
- [x] Appointment schedule `/dashboard/schedule` — vertical timeline, 25-min slots
- [x] Slot generation `POST /api/slots/generate` — idempotent day creation

### Command Center & Agent Intelligence
- [x] Command Center `/dashboard/command.html` — direct agent dispatch
- [x] Context loader — `load_context()` runs before every command
- [x] Context-aware Haiku — recent thread + active jobs in classify prompt
- [x] Fuzzy customer matching — prefix-based fallback for misspellings
- [x] Soft customer failure — clarification message instead of crashing
- [x] Owner phone guard — never creates customer from owner phone
- [x] Stale clarification detection — kills expired sessions

### Proposal Agent
- [x] Structured JSON output — `{"description", "amount"}` line items
- [x] Job summarization via Haiku — clean 1-line descriptions
- [x] Explicit pricing rules — pump $275, baffle $175, labor $125/hr
- [x] Owner-as-customer guard

### Square Payment Pipeline (7.10 — CODE COMPLETE)
- [x] Step 8b wired in invoice_agent.py
- [x] mark_invoice_paid() two-pass fallback
- [x] Square SDK v44 import fixed
- [x] sql/square_payment_writeback.sql ready

### Infrastructure
- [x] 25 test customers imported
- [x] All 66 dependencies pinned in requirements.txt
- [x] Python 3.12.9 via `.python-version`
- [x] Debug prints removed from auth_routes.py
- [x] Super admin flag (`is_super_admin`) on session

---

## Build Log — March 25, 2026 (17 Prompts)

| # | Prompt | Type | Files |
|---|--------|------|-------|
| 1 | Geocode address → lat/lng + zone | execution | `execution/geocode.py` |
| 2 | SMS message_type logging | execution | `execution/sms_send.py` |
| 3 | Scheduling DB helpers | execution | `execution/db_scheduling.py` |
| 4 | Dispatch board UI | Flask + template | `routes/dashboard_routes.py`, `dispatch.html` |
| 5 | Dispatch send API + SMS blast | Flask + SMS | `routes/dispatch_routes.py` |
| 6 | Worker route page /r/\<token\> | Flask + template | `templates/worker_route.html` |
| 7 | Worker SMS reply handler | execution | `execution/sms_router.py` |
| 8 | AI dispatch suggestions Phase 2 | AI | `execution/dispatch_suggestion.py` |
| 9 | Classes dashboard | Flask + template | `templates/dashboard/classes.html` |
| 10 | Public booking page | Flask + template | `routes/booking_routes.py`, `templates/book.html` |
| 11 | Slot cancellation + waitlist | Flask + SMS | `routes/booking_routes.py` |
| 12 | Scheduled SMS jobs | execution | `execution/scheduled_sms.py` |
| 13 | Appointment schedule view | Flask + template | `templates/dashboard/schedule.html` |
| 14 | Slot generation API | Flask | `routes/booking_routes.py` |
| 15 | Carry-forward + held jobs | execution | `execution/db_scheduling.py` |
| 16 | Super admin heartbeat | Flask + template | `templates/dashboard/admin.html` |
| 17 | Square Step 8b + env docs | execution | `CLAUDE.md` env vars |

---

## Remaining Action Items

### Manual — Run SQL Migrations in Supabase

**1. Square payment columns** (`sql/square_payment_writeback.sql`)
**2. Job costing table** (`directives/supabase_migration_001.sql`)
**3. Scheduling tables** — `scheduled_jobs`, `workers`, `route_assignments`,
   `dispatch_log`, `route_tokens` (schema TBD — create from db_scheduling.py usage)
**4. Class/booking tables** — `class_boards`, `class_slots`, `class_enrollments`,
   `class_waitlist` (schema TBD — create from booking_routes.py usage)
**5. SMS message log** (`sql/scheduling_migration.sql`)
**6. Super admin flag** — `ALTER TABLE clients ADD COLUMN IF NOT EXISTS is_super_admin boolean DEFAULT false;`

### Square Production Go-Live
1. Run `sql/square_payment_writeback.sql` in Supabase
2. Set Railway env vars: `SQUARE_ACCESS_TOKEN`, `SQUARE_ENVIRONMENT=production`,
   `SQUARE_LOCATION_ID`, `SQUARE_WEBHOOK_SIGNATURE_KEY`, `SQUARE_WEBHOOK_URL`
3. Register webhook: `payment.completed` → `/webhooks/square`
4. Test with sandbox first

---

## Architecture Reference

### Stack
```
Backend:     Python 3.12.9 / Flask 3.1.3
Database:    Supabase (PostgreSQL)
SMS:         Telnyx (10DLC pending)
AI:          Claude Haiku (classification) + Sonnet (generation)
Payments:    Square 44.0.1 (sandbox — code complete)
Geocoding:   Google Maps API
Deploy:      Railway — auto-deploy on push to main
Auth:        Phone + 4-digit PIN → Flask session (30-day lifetime)
```

### Key Credentials
```
Railway URL:    https://web-production-043dc.up.railway.app
GitHub repo:    329andalive/Agent_Work_flow
Client ID:      8aafcd73-b41c-4f1a-bd01-3e7955798367
Business:       Holt Sewer & Drain
Owner phone:    +12074190986 (Telnyx)
Owner mobile:   +12076538819 (Jeremy's cell)
Supabase URL:   https://wczzlvhpryufohjwmxwd.supabase.co
FLASK_ENV:      development
```

### URL Map
```
— Auth —
/login                        — Phone + PIN auth ✅
/logout                       — Clear session ✅

— Dashboard —
/dashboard/                   — Control Board ✅
/dashboard/office.html        — Office summary ✅
/dashboard/estimates/         — Estimates list ✅
/dashboard/estimates/new      — New Estimate form ✅
/dashboard/invoices/          — Invoices list ✅
/dashboard/invoices/new       — New Invoice form ✅
/dashboard/payments/          — Paid invoices list ✅
/dashboard/customers/         — Customer list ✅
/dashboard/customers/new      — Add Customer ✅
/dashboard/customers/<id>     — Customer detail ✅
/dashboard/job/<id>           — Job detail ✅
/dashboard/proposal/<id>      — Proposal document ✅
/dashboard/invoice/<id>       — Invoice document ✅
/dashboard/command.html       — Command Center ✅
/dashboard/new-job            — New Job form ✅
/dashboard/onboarding.html    — Onboarding admin ✅
/dashboard/dispatch           — Dispatch board (drag-drop) ✅
/dashboard/classes            — Class slot management ✅
/dashboard/schedule           — Appointment timeline ✅
/dashboard/admin              — Super admin heartbeat ✅
/dashboard/purchases/         — Coming soon (stub)
/dashboard/receipts/          — Coming soon (stub)
/dashboard/accounting/        — Coming soon (stub)

— APIs —
/api/customers/create         — POST: create customer ✅
/api/jobs/create              — POST: create job ✅
/api/invoices/create          — POST: create invoice ✅
/api/estimates/create         — POST: create estimate ✅
/api/invoices/export-csv      — GET: QuickBooks CSV export ✅
/api/command                  — POST: Command Center dispatch ✅
/api/dispatch/assign          — POST: assign job to worker ✅
/api/dispatch/send            — POST: send routes SMS blast ✅
/api/slots/create             — POST: create class slot ✅
/api/slots/cancel             — POST: cancel slot + notify ✅
/api/slots/generate           — POST: auto-create day slots ✅
/api/book/lookup-customer     — POST: returning customer check ✅
/api/book/create              — POST: book a slot ✅
/api/book/waitlist            — POST: join waitlist ✅
/api/book/cancel              — POST: customer cancel booking ✅
/api/admin/run-scheduled-sms  — POST: trigger scheduled jobs ✅

— Public (no auth) —
/book/<board_token>           — Public booking page ✅
/r/<token>                    — Worker route page (mobile) ✅
/book                         — Legacy booking form ✅
/webhooks/telnyx              — SMS webhook ✅
/webhooks/square              — Square payment webhook ✅
```

### File Map
```
execution/
  sms_receive.py              — Flask app entry point, blueprint registration
  sms_send.py                 — Outbound SMS + sms_message_log logging
  sms_router.py               — SMS routing + worker reply handler (DONE/BACK/etc)
  proposal_agent.py           — Structured JSON proposals, Haiku summarization
  invoice_agent.py            — Invoice generation + Square Step 8b
  context_loader.py           — Stateful context assembly for commands
  call_claude.py              — Claude API wrapper (Haiku/Sonnet/Opus)
  geocode.py                  — Google Maps geocoding + zone clustering
  db_scheduling.py            — Scheduling DB helpers (7 functions, multi-tenant)
  db_clarification.py         — Clarification DB ops + cleanup_expired()
  dispatch_suggestion.py      — AI dispatch suggestions (Phase 2, 30+ sessions)
  scheduled_sms.py            — Nudges, reminders, no-show marking
  token_generator.py          — Token generation + mark_invoice_paid() fallback
  square_agent.py             — Square Payment Links API (v44)
  job_cost_agent.py           — Job cost tracking (defensive)

routes/
  dashboard_routes.py         — All dashboard pages (20+ templates)
  dispatch_routes.py          — /api/dispatch/* + /r/<token> worker route
  booking_routes.py           — /book/<token> + /api/book/* + /api/slots/*
  command_routes.py           — /api/command + context loader wiring
  auth_routes.py              — /login, /logout, /set-pin + super admin flag
  invoice_routes.py           — Square webhook handler
  onboarding_routes.py        — Client onboarding wizard
  document_routes.py          — /doc/edit, /doc/save, /doc/send

templates/
  base.html                   — Shared sidebar + layout (navy/amber)
  book.html                   — Public booking page (mobile-first)
  worker_route.html           — Worker route page (mobile, no login)
  proposal.html               — Public proposal view
  invoice.html                — Public invoice view (PAY NOW)
  error.html                  — Branded error pages
  dashboard/
    control.html              — Control Board
    office.html               — Office summary
    command.html              — Command Center
    customers.html            — Customer list + search
    customer_detail.html      — Customer profile
    job_detail.html           — Job detail
    estimates.html            — Estimates list
    invoices.html             — Invoices list + CSV export
    payments.html             — Paid invoices
    new_estimate.html         — New Estimate form
    new_invoice.html          — New Invoice form
    new_job.html              — New Job form
    new_customer.html         — Add Customer form
    proposal_view.html        — Proposal document view
    invoice_view.html         — Invoice document view
    dispatch.html             — Dispatch board (drag-drop)
    classes.html              — Class slot management
    schedule.html             — Appointment timeline
    admin.html                — Super admin heartbeat
    onboarding.html           — Client onboarding
    coming_soon.html          — Stub for unbuilt sections

sql/
  square_payment_writeback.sql — Square schema migration
  scheduling_migration.sql     — sms_message_log table
  pricing_benchmarks.sql       — 125 benchmark rows

directives/
  clients/personality.md       — Holt Sewer & Drain voice + pricing
  agents/proposal_agent.md     — Proposal architecture + line item rules
```

### Known Issues — Carry-Forward
1. **10DLC not approved** — outbound SMS blocked
2. **Square in sandbox** — code complete, needs config
3. **Customer SMS opt-in** — test customers have `sms_consent=false`
4. **Onboarding wizard** — not tested end-to-end
5. **Scheduling tables** — need to be created in Supabase
6. **Class/booking tables** — need to be created in Supabase
7. **Purchases / Receipts / Accounting** — stubs only, no schema
8. **job_costs table** — run `directives/supabase_migration_001.sql`
