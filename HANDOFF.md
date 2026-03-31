Response

# HANDOFF.md — Bolts11 Session Log
> Last updated: March 30, 2026 — Backend Engineer
> Read CLAUDE.md first every session before touching any code.

---

## Session — March 30, 2026

### Admin Dashboard — Built and Deployed (Separate Railway Service)
- Created `admin_app.py` — standalone Flask app at admin.bolts11.com
- Runs as a separate Railway service with its own start command:
  `gunicorn admin_app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 60`
- Shares the same Supabase DB as the main app
- Auth: single 6-digit ADMIN_PIN (env var), not per-user
- Pages:
  - `/requests` — view/approve/reject/contact access requests from bolts11.com
  - `/clients` — all active clients, job counts, status
  - `/clients/<id>` — client detail, activity log, API cost estimate, resend welcome email
  - `/costs` — API cost tracking per client (Haiku/Sonnet call estimates)
- Approving a request provisions a new client in Supabase + sends welcome email via Resend
- Dark theme (navy/amber), sidebar nav with pending request badge count
- Deploy guide saved in `deploy.md`

### Bolts11.com Website — Rebuilt and Wired to Backend
- bolts11.com is served from a **separate repo**: `329andalive/bolts10` (Cloudflare Pages)
- Updated `index.html` — dual-audience site (trades + wellness + shops), early access form
- Created `signin.html` — client portal login (phone + PIN)
- Form submits POST to `https://api.bolts11.com/api/access-request`
- Signin submits POST to `https://api.bolts11.com/api/auth/portal-login`
- Key fix: forms were pointing to Railway internal URL (`web-production-043dc.up.railway.app`)
  instead of `api.bolts11.com` — corrected in both files
- Added `wrangler.jsonc` for Cloudflare Worker deployment

### Portal Login — Temp PIN Flow for New Clients
- New clients (no `pin_hash` set) use temp PIN `5555` for first login
- Temp PIN accepted → redirect to `/set-pin` to create real 4-digit PIN
- Existing clients validate against hashed PIN as before
- Welcome email now shows formatted phone `(207) 555-0100` and temp PIN instructions

### Resend Email Agent — Rebuilt
- Replaced `urllib` with `requests` library in `execution/resend_agent.py`
- Added `_fmt_phone()` helper for display-friendly phone formatting in emails
- Four email functions: access request confirmation, lead alert, welcome, document delivery
- `RESEND_API_KEY` env var confirmed set in Railway

### Access Request Pipeline — End to End
- `routes/access_request_routes.py` — handles form submissions + portal login
- Saves lead to `access_requests` table in Supabase
- Sends confirmation email to requester + alert email to support@bolts11.com
- Portal login consolidated here (removed duplicate from `auth_routes.py`)
- Blueprint registered in `sms_receive.py` as `access_bp`
- Cross-origin session cookies configured: `SameSite=None`, `Secure=True`

### Files Changed This Session
```
NEW   admin_app.py                             — admin dashboard Flask app
NEW   deploy.md                                — admin deployment guide
NEW   routes/admin_routes.py                   — admin routes (requests, clients, costs)
NEW   routes/access_request_routes.py          — access request + portal login
NEW   execution/resend_agent.py                — Resend email delivery (rebuilt)
NEW   templates/admin_base.html                — admin dark theme base layout
NEW   templates/admin_login.html               — 6-digit PIN login
NEW   templates/admin_requests.html            — access request management
NEW   templates/admin_clients.html             — client list
NEW   templates/admin_client_detail.html       — client detail + activity
NEW   templates/admin_costs.html               — API cost tracking
NEW   bolts11-site/wrangler.jsonc              — Cloudflare Worker config
NEW   bolts11-site/signin.html                 — client portal sign-in page
MOD   bolts11-site/index.html                  — rebuilt with access form + audience tabs
MOD   routes/auth_routes.py                    — removed duplicate portal-login route
MOD   execution/sms_receive.py                 — registered access_bp, session cookie config
```

### Repos
```
329andalive/Agent_Work_flow  — Flask backend (Railway)
329andalive/bolts10           — bolts11.com static site (Cloudflare Pages)
```

### Still Pending — Carry Forward
- **Railway redeploy needed** — latest commit needs to be deployed (was on old commit)
- **admin.bolts11.com DNS** — CNAME record needed in Cloudflare pointing to new Railway service
- **ADMIN_PIN env var** — set in new Railway admin service
- **`access_requests` table** — verify exists in Supabase with correct columns
- **10DLC approval** — outbound SMS still blocked
- **Square production credentials** — still on sandbox
- **SQL migration** — `ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_proposal_id uuid;`
- **Rule added 2026-03-30:** All outbound email links must use token URLs (`/p/<token>`, `/i/<token>`). Never `/dashboard/` URLs. Enforced by `tests/test_email_links.py`.

---

## Session — March 29, 2026

### Testing Infrastructure — Built From Zero (T1-T6)
- Installed pytest 8.3.4 + pytest-mock, created tests/ scaffold (T1)
- 36 unit tests + 6 integration tests, 0 failures
- Covers: invoice math, SMS routing, multi-tenancy safety, vertical config,
  agent wiring, email bridge, schema contracts
- T3 caught live bug: "EST " prefix was falling through to clarification_agent
  instead of proposal_agent — fixed in sms_router.py
- T5 schema contracts verify DB columns match code expectations against real Supabase
- Rule going forward: every bug fixed gets a test added before the fix

### Vertical Config System — Built and Wired (V1-V4)
- Created execution/vertical_loader.py — cached config loader with graceful fallback
- Extracted all sewer-specific hardcoding from invoice_agent.py, proposal_agent.py,
  sms_router.py, document_html.py into directives/verticals/sewer_drain/
- Agents are now vertical-agnostic — they load config at runtime from client.trade_vertical
- Added landscaping vertical — 3 JSON files, zero code changes (V3)
- Added gravel_pit vertical — includes self-load workflow config (V4)
  Self-load: contractor texts "got 10 yards 3/4 minus" → draft invoice created,
  office notified, held for approval. Replaces the forgotten Friday notebook problem.

### Verticals Now Available
```
directives/verticals/
├── sewer_drain/     — config.json, prices.json, prompts.md (V1)
├── landscaping/     — config.json, prices.json, prompts.md (V3)
└── gravel_pit/      — config.json, prices.json, prompts.md (V4)
```

Each vertical contains:
- `config.json` — job types, SMS keywords, tax rules, field keywords, job_type_map
- `prices.json` — regional service pricing (low/typical/high)
- `prompts.md` — agent prompt language for invoice, proposal, scheduling

### Schedule Planner — Built (SP1)
- New page at /dashboard/planner — weekly grid with backlog column
- Drag-and-drop jobs from backlog to Mon-Fri day columns
- POST /api/jobs/reschedule moves jobs in Supabase on drop
- Capacity bars per day (green/amber/red) with configurable daily cap (8)
- Zone consolidation hints — amber banner when same zone appears on 2+ days
- "Open dispatch →" link under each day column
- Added Planner to sidebar nav (before Dispatch)

### Email Bridge — Built (EB1)
- Created execution/email_send.py using Resend (replaced SendGrid before first deploy)
- Navy/amber branded HTML emails with line items, totals, PAY NOW button
- API routes: /api/invoices/<id>/send-email, /api/proposals/<id>/send-email
- Invoice view: "Email Invoice" button POSTs to API (was mailto: link)
- Proposal view: Added "Email Estimate" amber button with same flow
- Both prompt for email if not on file, save to customer record on send
- Requires RESEND_API_KEY in env vars to activate

### Dispatch Board — Address Display
- Job cards now show customer address with pin icon on both slip cards and worker tabs
- Address sourced: job_address → customer_address → fallback empty
- CSS: .slip-card__addr and .slip-tab__addr with DM Mono, ellipsis truncation

### Dashboard Items — All Previously Broken Items Fixed
- Control Board job "View →" links — already wired
- job_detail.html — already exists with full implementation
- customer_detail.html — already deployed
- customers.html — already deployed
- estimates.html, invoices.html, payments.html — already deployed
- Square Step 8b — already wired in invoice_agent.py
- auth_routes.py debug prints — already clean

### Real-World Test Setup — 40 Jobs Seeded
- Created scripts/seed_week.py — seeds a full week of realistic dispatch data
- DRY RUN: python scripts/seed_week.py --dry-run
- SEED: python scripts/seed_week.py
- 40 jobs seeded across 5 days (March 30 - April 3, 2026)
- Uses 23 real customer IDs from B&B Septic's customer list
- All 40 jobs confirmed inserted: status=scheduled, unassigned

### bolts11-site — Recovered
- bolts11-site/ files were never committed to GitHub (lost when local
  repo was deleted earlier in session)
- Site confirmed live at https://bolts11.com (Netlify + Cloudflare)
- Recreated bolts11-site/index.html, privacy.html, terms.html from
  live site content
- All files now committed to repo

### Files Changed This Session
```
NEW   execution/vertical_loader.py          — cached config loader
NEW   execution/email_send.py               — Resend email bridge
NEW   directives/verticals/sewer_drain/     — config.json, prices.json, prompts.md
NEW   directives/verticals/landscaping/     — config.json, prices.json, prompts.md
NEW   directives/verticals/gravel_pit/      — config.json, prices.json, prompts.md
NEW   templates/dashboard/planner.html      — weekly schedule planner
NEW   scripts/seed_week.py                  — 40-job test data seeder
NEW   bolts11-site/index.html               — marketing site
NEW   bolts11-site/privacy.html             — privacy policy
NEW   bolts11-site/terms.html               — terms and conditions
NEW   tests/test_vertical_loader.py         — 14 tests (sewer + landscaping + gravel)
NEW   tests/test_vertical_wiring.py         — 4 tests (agent wiring verification)
NEW   tests/test_email_send.py              — 5 tests (email bridge)
NEW   tests/test_schema_contracts.py        — 6 integration tests (DB schema)
MOD   execution/proposal_agent.py           — loads job_type_keywords from vertical config
MOD   execution/invoice_agent.py            — loads field_keywords + prompts from vertical
MOD   execution/sms_router.py               — builds routing table from vertical keywords
MOD   execution/document_html.py            — tax rate/label from vertical config
MOD   routes/dashboard_routes.py            — planner route, reschedule API, email APIs
MOD   templates/base.html                   — planner nav item added
MOD   templates/dashboard/dispatch.html     — address on cards + tabs
MOD   templates/dashboard/invoice_view.html — email button (Resend API)
MOD   templates/dashboard/proposal_view.html — email estimate button
MOD   requirements.txt                      — resend==2.4.0
```

### Still Pending — Carry Forward
- **Prompt #5** — Run square_payment_writeback.sql in Supabase (manual)
- **10DLC approval** — outbound SMS still blocked
- **Square production credentials** — still on sandbox
- **Supabase rebrand UPDATE** — title still shows "Holt Sewer & Drain"
  Run: UPDATE clients SET business_name = 'B&B Septic'
       WHERE id = '8aafcd73-b41c-4f1a-bd01-3e7955798367';
- **RESEND_API_KEY** — add to Railway env vars (resend.com free tier)
- **Netlify deploy** — drag bolts11-site/ folder to Netlify to restore live site

### Monday Real-World Test Plan
1. Open /dashboard/planner — drag 40 seeded jobs onto days
2. Open /dashboard/dispatch?date=2026-03-30 — assign jobs to Austin/Jesse/Jeremy
3. Text completions from your phone (and wife's phone for second tech):
   "DONE pumped 1000 gal tank Arthur Crockett 310 Northport Ave $325"
4. Watch dashboard update — jobs flip to invoiced, invoices appear
5. Log every break — each one becomes a bug fix + test

### Next Session Priorities
1. Geo-clustering — wire geocode.py to set zone_cluster on job create,
   color-code dispatch board cards automatically
2. Customer CSV import — self-serve bulk import for onboarding
3. Client config inheritance — client overrides vertical defaults
   (B&B Septic starts from sewer_drain template, overrides what's different)
4. Self-serve onboarding wizard — after 5 manual client setups

---

## Session — March 28, 2026

### Rebrand
- Holt Sewer & Drain → B&B Septic across entire codebase (24 occurrences, 13 files)
- NOTE: Also run in Supabase: `UPDATE clients SET business_name = 'B&B Septic' WHERE id = '8aafcd73-...'`

### Square Payment Links — Fully Wired
- Rewrote square_agent.py for v44 SDK: `Square()` not `Client()`, `token` not `access_token`
- Correct API path: `client.checkout.payment_links.create()`
- PAY NOW button wired on invoice_view.html and invoice.html (public)
- Square link regenerates on invoice edit when total changes
- Payment link uses line_items sum + tax, not raw parsed amount
- SQUARE_AVAILABLE flag exposed on /debug page

### Invoice Agent — Cents Preservation
- Multi-amount parser sums ALL dollar amounts: `$350 + $400 + $175.25 = $925.25`
- Regex updated: `\$(\d+(?:,\d{3})*(?:\.\d{1,2})?)` handles commas + cents
- Fixed double-counting: findall runs FIRST, skip parse_flat_rate for multi-amount
- All `%.0f` replaced with `%.2f` across 7 files (cents everywhere)
- Haiku line item extraction forces `round(float(), 2)` on all numeric fields
- final_amount uses actual_amount from SMS parser, not Claude's rounded text
- Safety: if Haiku line items sum ≠ actual_amount, falls back to actual_amount

### Tax System — Per-Line-Item
- Removed auto-tax detection from invoice_agent (was taxing subtotal wrong)
- Tax is now set by owner on invoice edit page — per line item TAX toggle button
- Each line item has a TAX button: click to mark taxable (amber "TAX ✓")
- 5.5% Maine tax calculated only on marked items, labor stays exempt
- document_routes.py calculates taxable_subtotal from items with taxable=true
- Tax dropdown replaced: was 5%/8%/10%, now "0% — No tax" / "5.5% — Maine"

### Customer System
- "ADD CUSTOMER" SMS now routes to customer_create (was hitting clarification_agent)
- Customer edit modal on /dashboard/customers/ — pre-populated, saves via API
- Missing-data amber dots on customer rows, estimates, and invoices
- Email field added to customer form, list table, and detail view
- Phone normalization to E.164 in db_customer.py, db_client.py, and sms_send.py
- Hard stop: customer not found → helpful SMS with correct shorthand format

### Command Center Fixes
- Strip command prefix (EST/INV/DONE) before customer name resolution
- "EST " and "INV " recognized as short-form triggers
- Name resolver reads end of string first: "INV job desc Beverly Whitaker" works
- Job description words filtered: "Pumped", "tank", "gallon" never tried as names
- Clock agent receives actual employee UUID, not string "owner"

### SMS & Logging
- All outbound SMS normalized to E.164 in sms_send.py (fixes Telnyx 40310)
- All outbound SMS logged to both sms_message_log AND messages table
- Delivery webhook no longer warns "No message row for telnyx_id"

### SQL Migrations Needed
```
ALTER TABLE customers ADD COLUMN IF NOT EXISTS customer_email TEXT;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS notes TEXT;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS payment_link_url TEXT;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS square_payment_link_id TEXT;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS square_order_id TEXT;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS tax_rate numeric(5,4) DEFAULT 0.0;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS tax_amount numeric(10,2) DEFAULT 0.0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS scope_hold boolean DEFAULT false;
UPDATE clients SET business_name = 'B&B Septic' WHERE id = '8aafcd73-b41c-4f1a-bd01-3e7955798367';
```

### End-of-Day Status — Full Invoice Pipeline Working

Confirmed end-to-end at 6:26 PM:
```
1. SMS: "inv (207) 555-6161 pump out $350 and tank repair $400 and baffle replacement $175.25"
2. Dollar amounts found: [350.0, 400.0, 175.25] ✅
3. Multi-amount sum = $925.25 ✅
4. Square payment link created at $925.25 (no tax yet) ✅
5. Jeremy opens edit URL, marks baffle as taxable, selects 5.5% ✅
6. Tax calculated on taxable items only: $925.25 → $934.62 total ✅
7. Document saved, Square link regenerated at $934.62 ✅
8. Square regen confirmed: $934.62 → 93462¢ ✅
9. New Square link live: https://sandbox.square.link/u/OP82geIO ✅
10. Invoice HTML uploaded to Supabase storage ✅
11. Style notes learned from edit (Haiku learning loop) ✅
12. All delivery webhooks confirmed — sent and finalized ✅
```

### What's Working Now — Full Scorecard
- [x] SMS command parsing — EST, INV, JOBS, CLOCK IN/OUT, ADD CUSTOMER
- [x] Customer lookup by phone number or name (end-of-string resolver)
- [x] Phone normalization — any format resolves to correct DB record
- [x] Multi-amount invoices — $350 + $400 + $175.25 = $925.25
- [x] Cents preserved throughout — $175.25 not $175.00
- [x] Square SDK v44 — payment links generating correctly in sandbox
- [x] Per-line-item tax — TAX button per row, 5.5% Maine on marked items only
- [x] Square link regenerates on edit with tax-inclusive total
- [x] Invoice HTML saved to Supabase storage
- [x] Customer SMS confirmation — normalized E.164 format
- [x] Payment follow-up scheduled automatically
- [x] Job cost tracking saving to DB
- [x] Style learning loop — Haiku analyzes edits, updates client preferences
- [x] All delivery webhooks confirmed — sent and finalized
- [x] Dispatch board with drag-drop, worker columns, state persistence
- [x] Worker route page with SMS status buttons (DONE/BACK/PARTS/NOSHOW/SCOPE)
- [x] Auto-invoice on DONE — creates draft from estimated_amount
- [x] Scope hold system — worker flags, owner reviews before invoice sends
- [x] End-of-day carry-forward sweep for unfinished jobs
- [x] AI dispatch apprentice logging (dispatch_decisions table)

### Still Pending — Not Code, Just Waiting
- [ ] **10DLC approval from Telnyx** — outbound SMS to real customers blocked until approved
- [ ] **Square production credentials** — flip env vars when ready for real money

### Next Session Priority
See March 29 session above — all items completed.

---

## Current Production Status

```
Railway URL:   https://web-production-043dc.up.railway.app
API domain:    https://api.bolts11.com (DNS may not be pointed yet)
GitHub repo:   329andalive/Agent_Work_flow
Python:        3.12.9 (pinned via .python-version — DO NOT remove this file)
Deploy:        Auto-deploy on push to main
Build status:  ✅ GREEN as of March 29, 2026 — 36 tests passing
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
- [x] 25 test customers imported for B&B Septic
- [x] `squareup==44.0.1.20260122` installed and pinned — Square SDK available on Railway
- [x] All 66 dependencies fully pinned in requirements.txt — no floating versions

## Still Broken / Not Yet Built — Updated March 29

- [x] Control Board job "View →" links — fixed
- [x] `/dashboard/customers/` — working
- [x] `/dashboard/customers/<id>` — working
- [x] `/dashboard/estimates/` — working
- [x] `/dashboard/invoices/` — working
- [x] `/dashboard/payments/` — working
- [x] `customers.html` — deployed
- [x] `customer_detail.html` — deployed
- [x] `job_detail.html` — deployed
- [x] Square payment link wired in invoice_agent.py (7.10 done)
- [x] Debug prints removed from auth_routes.py (7.11 done)

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
Business:       B&B Septic
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
  customers.html                 — done ✅
  customer_detail.html           — Done ✅
  estimates.html                 — Done ✅
  invoices.html                  — Done ✅
  payments.html                  — Done ✅ 
  job_detail.html                — Done ✅
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