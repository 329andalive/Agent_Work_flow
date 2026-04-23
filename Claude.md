# CLAUDE.md — Trades AI Agent Stack
> Mirrored across CLAUDE.md, AGENTS.md, and GEMINI.md so the same
> instructions load in any AI environment.

You are building and operating an AI-powered back office system for
small trades businesses. The first client vertical is Sewer and Drain.
Every decision you make should be evaluated against one question:
would a 55-year-old rural tradesman actually use this?

---

## Read these before writing any database code

Two short files at the repo root encode the rules we keep re-learning
the hard way. Read both before touching DB code, agents, or routes:

- **[CONVENTIONS.md](CONVENTIONS.md)** — naming rules, the "DO NOT"
  list of patterns we've shipped fixes for at least once, and the
  table-to-helper-file map for the lite repository pattern.
- **[execution/schema.py](execution/schema.py)** — single source of
  truth for every Supabase table + column name. Every new query
  should `from execution.schema import <Table> as T` and use
  `T.COLUMN_NAME` instead of magic strings. Typos become
  `AttributeError` at import time instead of PostgREST errors in
  production logs.

If you're about to write `sb.table("...")` with literal column
strings, stop and check if `schema.py` already has a class for that
table. If it does, use it. If it doesn't, add one and document any
gotchas in a comment next to the new constants.

---

## Architecture Overview — PWA Pivot (April 2026)

**The core communication pivot:**

SMS as a two-way conversational AI interface is no longer viable.
10DLC registration, carrier filtering, throughput limits, and cost
make it a poor foundation for what Bolts11 needs to do at scale.

**New model:**
- **SMS = one-way notification only.** Clock in, clock out, dispatch
  alerts, "your review link is ready." Low-volume, transactional,
  carrier-safe.
- **PWA = the tech's primary interface.** Installed on their phone
  home screen. Replaces SMS replies for all AI interaction —
  estimates, invoices, job notes, clarifications, approvals.
- **Email = primary outbound to customers.** Proposals, invoices,
  confirmations, follow-ups. HTML documents delivered instantly
  via Resend. No 10DLC required.
- **Dashboard = owner/office read-only surface.** Reporting,
  dispatch board, KPI visibility. Not a document creation tool.

**What this means for every build decision:**
Before adding an SMS reply flow, ask: should this be a PWA screen
instead? Before building a dashboard form, ask: should the AI draft
this from a tech's PWA input instead?

---

## The 3-Layer Architecture

**Layer 1: Directive (What to do)**
- SOPs written in Markdown, live in `directives/`
- Define goals, inputs, tools to use, outputs, and edge cases
- Natural language instructions, like you'd give a capable employee
- The Personality Layer lives here per client

**Layer 2: Orchestration (Decision making)**
- This is you. Your job: intelligent routing.
- Read directives, call execution scripts in the right order
- Handle errors, ask for clarification, update directives with learnings
- You do not make API calls directly — you call execution scripts
- Example: don't call Claude API yourself, run `execution/call_claude.py`

**Layer 3: Execution (Doing the work)**
- Deterministic Python scripts in `execution/`
- All API keys and secrets live in `.env` only — never hardcoded
- Handle API calls, data processing, SMS sending, database operations
- Every script must be commented, testable, and handle errors cleanly

**Why this works:** errors compound fast. 90% accuracy per step =
59% success over 5 steps. Push complexity into deterministic code
so the orchestration layer only makes decisions.

---

## The Personality Layer — Most Important Concept

Before any agent does anything, it must load the client's
Personality Layer from:
```
directives/clients/{client_phone}/personality.md
```

This document contains everything about that business — their voice,
their pricing language, their service area, their values, their
customer base. Every single output must sound like that owner wrote
it personally. Never generic. Never robotic.

**Loading sequence for every agent:**
1. Identify client by phone number
2. Load `directives/clients/{client_phone}/personality.md`
3. Load the relevant agent directive from `directives/agents/`
4. Execute with personality context injected into every Claude call

---

## The PWA — Tech Interface

The PWA is the tech's primary tool on the road. It installs to their
home screen via the browser install prompt. No app store. No download.
No SMS conversation required.

**PWA entry points:**
- `static/manifest.json` — Install metadata (name, icons, start_url)
- `static/sw.js` — Service worker (offline fallback, caching)
- `/pwa/` — PWA shell (authenticated, tech-facing)
- `/doc/edit/<token>` — Document review page (PWA-optimized)

**PWA manifest (static/manifest.json):**
```json
{
  "name": "Bolts11",
  "short_name": "Bolts11",
  "description": "Job Dispatch & Invoicing",
  "start_url": "/pwa/",
  "display": "standalone",
  "background_color": "#ffffff",
  "theme_color": "#000000",
  "icons": [
    { "src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

**Service worker (static/sw.js) — basic offline fallback:**
```javascript
self.addEventListener('fetch', event => {
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});
```

**Every PWA-facing template must include in `<head>`:**
```html
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#000000">
<script>
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js');
  }
</script>
```

**PWA screens (build in this order):**

| Screen | Route | Replaces |
|---|---|---|
| Job dashboard | `/pwa/` | SMS job list queries |
| Clock in/out | `/pwa/clock` | SMS CLOCK IN / CLOCK OUT |
| New job input | `/pwa/job` | SMS estimate/invoice triggers |
| Document review | `/doc/edit/<token>` | SMS review link (already exists) |
| AI chat | `/pwa/chat` | SMS conversational replies |

**PWA design rules:**
- Mobile-first, thumb-friendly tap targets (min 44px)
- No browser chrome in standalone mode — design for full screen
- Offline-aware: show cached data when no signal, sync on resume
- Never require a password to view a document — token auth only
- Rural signal friendly: minimize payload size, lazy-load non-critical assets

---

## SMS — Inbound Webhook Only (Outbound Dead at Carrier)

**Critical constraint discovered April 9, 2026:**

Telnyx outbound to *anyone* (workers, owners, customers) is blocked
at the carrier level without 10DLC registration. The only working
SMS path is **inbound** — workers texting the Telnyx brand number
fires a webhook we can act on.

**What inbound SMS is still used for:**
- TCPA opt-in/opt-out (STOP / YES / START / UNSTOP) — DB state only,
  no outbound confirmation
- Nothing else. Workers clock in via the PWA (`/pwa/clock`).

**Outbound channels — what to use instead:**
- **Worker notifications** → notify() router → email (Resend) or PWA
  push. Never `send_sms()` directly. The notify() module enforces a
  3-layer permission check (client switch, recipient type, consent)
  and falls back to email when SMS is blocked.
- **Customer notifications** → email via Resend (Hard Rule #2)
- **Tech feedback after action** → PWA UI updates, no out-of-band ping

**Kill switch:** `clients.sms_outbound_enabled` is `false` for every
client by default. Even if a code path bypasses notify() and calls
`send_sms()` directly, notify() blocks at Layer 1. To re-enable SMS
for one client, flip the column manually (only after 10DLC).

**SMS routing order (sms_router.py) — minimal:**
```
1. STOP / YES / START / UNSTOP — opt-in/opt-out (DB state only)
2. Everything else → log and ignore (PWA owns the interaction)
```

`sms_router.py` does not import `send_sms` and never will.

---

## Communication Layer Summary

| Channel | Direction | Used For | Provider |
|---|---|---|---|
| SMS (brand number) | Inbound ONLY | Clock in/out from worker, webhook trigger | Telnyx |
| Email | Outbound to customers | Proposals, invoices, follow-ups, review requests | Resend |
| PWA | Tech input + AI output | Job input, estimates, invoices, AI chat, approvals | Flask + Jinja2 |
| Dashboard | Owner/office read | Reporting, dispatch board, KPI | Flask + Jinja2 |
| Voice (Phase 3) | Owner input | Hands-free commands from truck | ElevenLabs + Whisper |

**Why email-first for customer outbound:**
- No 10DLC registration required
- Instant delivery, free on Resend free tier
- HTML documents render properly — line items, PAY NOW buttons
- Trades customers expect emailed estimates

---

## Active Agents

```
proposal_agent        — generates estimates from PWA job input or SMS description
invoice_agent         — creates invoices from completed job notes
clarification_agent   — intercepts ambiguous input, asks follow-ups, routes
followup_agent        — follows up on unanswered estimates
review_agent          — requests Google reviews after job completion
content_agent         — creates social/marketing content
safety_agent          — generates safety checklists and OSHA docs
self_learning_agent   — prompts for NULL pricebook fields, updates confidence scores
```

Each agent has a directive in `directives/agents/{agent_name}.md`

**Agent input surfaces (post-PWA pivot):**
- Primary: PWA job input form (`/pwa/job`) or PWA chat (`/pwa/chat`)
- Secondary: SMS description (legacy, still supported)
- Never: Dashboard creation forms (removed — new_estimate.html, new_invoice.html)

---

## Document Flow (AI-First, Draft-Always)

Every estimate and invoice follows this path — no exceptions:

```
1. Tech inputs job description (PWA form or SMS)
2. Agent drafts document (proposal_agent or invoice_agent)
3. Draft saved with status='draft'
4. Review link generated and sent to tech (SMS ping or PWA notification)
5. Tech reviews on PWA (/doc/edit/<token>) — tap-to-edit cards
6. Every edit diffed and logged to draft_corrections (training signal)
7. Tech taps Approve & Send → document delivered to customer via email
8. Customer receives branded HTML email with PAY NOW link (Square)
```

**Hard rule:** The AI is the sole author of all estimates and invoices.
No dashboard form, no manual entry. If a tech needs a document,
they describe the job — to the PWA or via SMS — and the agent drafts it.

---

## Guided Estimate Flow

The guided estimate flow replaces free-form AI pricing with a
deterministic state machine. The AI never invents a price. Every
dollar amount comes from the tech.

**Entry point:** Tech types "create estimate" (or similar) in `/pwa/chat`.

**How it works:**
```
pwa_chat.py detects intent
  → guided_estimate.start() creates estimate_session
  → handle_input() routes each message to the right state handler
  → state machine walks: customer → job type → price → line items → review
  → final chip fires /pwa/api/job/new with explicit_amount
  → proposal_agent bypasses Claude pricing entirely
  → /doc/send writes a row to job_pricing_history
```

**State machine (execution/guided_estimate.py):**

| Step | State | What happens |
|---|---|---|
| 1 | `ask_customer` | Fuzzy DB lookup, shows match or list |
| 2 | `confirm_customer` | Tech confirms yes/no |
| 3 | `ask_job_type` | Keyword classification, shows history reference |
| 4 | `ask_price` | Tech enters price — never pre-filled |
| 5 | `ask_line_items` | Additional items loop until "done" |
| 6 | `ask_notes` | Optional notes |
| 7 | Review chip | `create_proposal` action with full amount |

**Pricing history reference (not pre-fill):**
When the tech reaches the price step, the state machine queries
`job_pricing_history` and surfaces text like:
  "Last 3 pump outs for this customer averaged $285."
This is display text only. The price field is always blank.
The tech types their own number.

**Files:**
- `execution/guided_estimate.py` — state machine, no Claude pricing calls
- `execution/db_pricing_history.py` — writes sent prices to history
- `execution/schema.py` — `EstimateSessions`, `JobPricingHistory` classes
- `sql/guided_estimate_tables.sql` — DB migration (already run)

**Hard rules for this flow:**
- The AI never generates or suggests a price. Ever.
- Pricing history is reference text only — never pre-filled.
- All state lives in `estimate_sessions` table — not in memory.
- The flow returns the same `{reply, action}` shape as pwa_chat.py.
- If the guided flow errors, pwa_chat.py falls through to Claude
  for regular chat — the chat never breaks.

**Claude calls in this flow:** exactly one — Haiku for job type
classification when keyword matching fails. Zero Claude calls for
pricing.

**What NOT to do in this flow:**
- Do NOT add a "suggested price" field — even greyed out.
- Do NOT pre-fill the price from the pricebook or history.
- Do NOT call Claude to generate line item amounts.
- Do NOT skip the customer confirmation step.

---

## File Organization

```
/
├── CLAUDE.md                          # This file
├── CONVENTIONS.md                     # Naming rules + DO NOT list
├── HANDOFF.md                         # Session log — read for current status
├── .env                               # All API keys — never commit
├── .python-version                    # Pins Python 3.12.9 — DO NOT DELETE
├── requirements.txt                   # Pinned packages — regenerate with pip freeze
├── static/
│   ├── manifest.json                  # PWA install manifest
│   ├── sw.js                          # Service worker (offline fallback)
│   ├── icon-192.png                   # PWA icon (lightning bolt, 192x192)
│   └── icon-512.png                   # PWA icon (lightning bolt, 512x512)
├── execution/                         # Python scripts (deterministic)
│   ├── schema.py                      # SINGLE SOURCE OF TRUTH for all column names
│   ├── guided_estimate.py             # Guided estimate state machine (NEW)
│   ├── db_pricing_history.py          # job_pricing_history write helper (NEW)
│   ├── proposal_agent.py             # Draft-first estimates — always returns review link
│   ├── invoice_agent.py              # Draft-first invoices — always returns review link
│   ├── pwa_chat.py                   # Chat router — intercepts estimate intent (UPDATED)
│   ├── pwa_chat_actions.py           # Action chip decorator — amount flows through (UPDATED)
│   ├── db_pricebook.py               # Pricebook CRUD — standard price only to Claude (UPDATED)
│   └── ... (other execution scripts)
├── routes/
│   ├── pwa_routes.py                 # Passes session_id to chat() (UPDATED)
│   ├── document_routes.py            # Writes pricing history on /doc/send (UPDATED)
│   └── ... (other blueprints)
├── sql/
│   ├── guided_estimate_tables.sql     # estimate_sessions + job_pricing_history (NEW)
│   └── ... (other migrations)
└── tests/
    ├── test_guided_estimate.py        # State machine + pricing history tests (NEW)
    └── ... (other test files)
```

---

## Database hosting

- **Bolts11 (this project):** Supabase Pro. Runs as-is on the existing
  `wczzlvhpryufohjwmxwd.supabase.co` project. Do not move — the coupling
  audit is documented in `plan.md` decisions log.
- **New projects going forward:** default to Neon Postgres. Free tier
  auto-pauses when idle (wakes on query) so dormant side-projects don't
  accrue charges. Up to 10 free projects per Neon org; scale to paid per
  project if one goes live. File uploads can still use Supabase Storage
  free tier (separate from the DB), or Cloudflare R2 if egress matters.
- **Do not mix:** pick one host per project. Cross-host queries add
  latency and break multi-tenant guarantees.

---

## API Credentials (.env)

```
ANTHROPIC_API_KEY=
TELNYX_API_KEY=
TELNYX_PHONE_NUMBER=
SUPABASE_URL=https://wczzlvhpryufohjwmxwd.supabase.co
SUPABASE_SERVICE_KEY=
BOLTS11_BASE_URL=https://bolts11.com
SQUARE_ACCESS_TOKEN=
SQUARE_ENVIRONMENT=sandbox
SQUARE_LOCATION_ID=
SQUARE_WEBHOOK_SIGNATURE_KEY=
GOOGLE_MAPS_API_KEY=
RESEND_API_KEY=
```

Never hardcode credentials. Always load from `.env` using
`python-dotenv`. Never commit `.env` to git.

---

## Routes (Flask Blueprints)

**pwa_bp** — `/pwa/*` Tech PWA screens (job input, clock, chat, dashboard)
**dashboard_bp** — All dashboard pages (owner/office, read-only reporting)
**dispatch_bp** — `/api/dispatch/*` + `/r/<token>` worker route
**booking_bp** — `/book/<token>` + `/api/book/*` + `/api/slots/*`
**command_bp** — `/api/command` + context loader wiring
**auth_bp** — `/login`, `/logout`, `/set-pin` + super admin flag
**invoice_bp** — `/webhooks/square` payment webhook
**document_bp** — `/doc/edit`, `/doc/save`, `/doc/send`
**onboarding_bp** — `/api/onboarding/*`, `/onboard/<token>`

---

## Platform Hard Rules

**HARD RULE #1 — Phone number required on every customer**
Every customer record must have a phone number. No exceptions.
`db_customer.create_customer()` raises `ValueError` if phone is missing.

**HARD RULE #2 — Customer-facing outbound goes via email only**
All customer-facing outbound (proposals, invoices, confirmations,
follow-ups) must be delivered via email. No SMS to customers.
10DLC registration is deferred indefinitely.
If no customer email on file: log `delivery_blocked_no_email` to
agent_activity and surface a needs_attention card.

**HARD RULE #3 — AI drafts every document. No exceptions.**
Estimates and invoices are never created through dashboard forms.
Dashboard is read-only for owners and office staff.
Techs input jobs via PWA. Agents draft. Techs review. Agents send.

**HARD RULE #4 — Multi-tenancy is sacred**
Every single database query must filter by `client_phone` or tenant
identifier. No exceptions. Never return data from one client to another.

**HARD RULE #5 — Webhook payloads saved before processing**
Save the raw inbound webhook payload to the database BEFORE any
processing begins. No exceptions. If downstream processing fails,
the raw data must still exist for recovery.

**HARD RULE #8 — The AI never generates a price**
Every dollar amount on every estimate comes from one of:
(a) the tech typed it, (b) a pricebook standard price used as fallback,
(c) a historical average shown as reference text only.
Claude never invents, suggests, or pre-fills a price. If you see
Claude outputting a price the tech didn't enter, that is a bug.
The fix is in db_pricebook.get_pricebook_for_prompt() — standard
price only, never the range.

---

## Claude API Call Structure

```python
system_prompt = f"""
You are the AI back office assistant for {business_name}.

Read this Personality Layer completely before doing anything:

{personality_doc}

Every response must sound exactly like this business owner
wrote it. Their tone, their pricing language, their market.
Never sound like a robot. Never use corporate filler.
"""

user_prompt = f"""
{agent_specific_instruction}

Input: {raw_user_input}
"""
```

**Model selection:**
- Haiku: SMS parsing, simple classifications, data extraction, PWA quick replies
- Sonnet: Proposals, invoices, follow-ups, review requests, PWA chat
- Opus: Training documents, safety docs, complex reasoning

---

## Supabase Schema

*(Existing tables unchanged — see previous schema entries below)*

**estimate_sessions (NEW — guided estimate flow)**
```
id                  uuid PRIMARY KEY
client_id           uuid REFERENCES clients(id) NOT NULL
employee_id         uuid REFERENCES employees(id) NOT NULL
session_id          uuid NOT NULL  -- links to pwa_chat_messages.session_id
status              text DEFAULT 'gathering'
customer_id         uuid REFERENCES customers(id)
customer_confirmed  boolean DEFAULT false
job_type            text
job_type_confirmed  boolean DEFAULT false
primary_price       numeric(10,2)  -- tech-entered, NEVER AI-generated
line_items          jsonb DEFAULT '[]'
notes               text
current_step        text
created_at          timestamptz DEFAULT now()
updated_at          timestamptz DEFAULT now()
```

**job_pricing_history (NEW — guided estimate "last 3" reference)**
```
id              uuid PRIMARY KEY
client_id       uuid REFERENCES clients(id) NOT NULL
customer_id     uuid REFERENCES customers(id)
job_id          uuid REFERENCES jobs(id)
proposal_id     uuid REFERENCES proposals(id)
job_type        text NOT NULL
description     text
amount          numeric(10,2) NOT NULL  -- tech-entered, NEVER AI-generated
employee_id     uuid REFERENCES employees(id)
completed_at    timestamptz DEFAULT now()
```
Written by /doc/send ONLY. Never by an agent.

**draft_corrections (training loop)**
```
id              uuid PRIMARY KEY
job_id          uuid REFERENCES jobs(id)
client_id       uuid REFERENCES clients(id) NOT NULL
document_type   text NOT NULL  -- estimate | invoice
field_name      text NOT NULL
original_value  text
corrected_value text
correction_type text NOT NULL  -- edit | add_item | remove_item | reject
tech_id         uuid
created_at      timestamptz DEFAULT now()
```

---

## Operating Principles

**1. Check for existing scripts first**
Before writing a new execution script, check `execution/`.
Only create new scripts if none exist for the task.

**2. Self-anneal when things break**
Read the full error. Fix the script. Test it. Update the directive.
Do not retry paid API calls without checking first.

**3. Directives are living documents**
Update when you discover API limits, better approaches, or errors.
Do not overwrite without asking. They are institutional memory.

**4. PWA before SMS**
When designing a new tech-facing interaction, default to a PWA
screen. Only use SMS for notification pings that prompt the tech
to open the PWA.

**5. Rural signal first**
Design every PWA screen to work on a 3G connection with intermittent
signal. Minimize JS payload. Cache aggressively. Sync on resume.
A tech in a basement should still be able to clock out.

**HARD RULE #6 — Chat agent is a router, never an executor**
pwa_chat.py returns action JSON. It never calls proposal_agent.run(),
never writes to the database directly. The chip carries the existing
endpoint. The endpoint owns the side effects and multi-tenant guards.
No exceptions.

**HARD RULE #7 — Railway never sends outbound SMS**
Telnyx is inbound only. No sms_send.py call should ever target
a worker or customer phone number. Any outbound SMS code path
is a bug. Worker notifications go through the PWA. Customer
documents go through email via Resend.

---

## What NOT to Do

- Never hardcode API keys
- Never call paid APIs in a loop without a circuit breaker
- Never overwrite a client's personality.md without confirmation
- Never send SMS to real customers during testing
- Never skip loading the Personality Layer — ever
- Never make output sound generic — that defeats the purpose
- Never create dashboard forms for estimates or invoices
- Never design a two-way SMS AI conversation flow — that's a PWA screen
- Never let Claude generate a price — see HARD RULE #8

---

## Current Build Status

**Phase 1 — PWA Foundation (Complete)**

- [x] Inbound SMS webhook routing (brand number)
- [x] Role-based permissions (owner, foreman, field_tech, office)
- [x] Proposal + invoice generation via Claude → styled HTML → email
- [x] Proposal follow-up tracking (accepted/declined/lost)
- [x] Clock in/out
- [x] Scheduling agent
- [x] Email delivery via Resend
- [x] Dashboard (20+ pages, dispatch board, planner, control board)
- [x] Square payment links (sandbox)
- [x] Document edit + diff tracking (/doc/edit/<token>)
- [x] static/manifest.json + sw.js
- [x] /pwa/ shell, login, auth, clock, route, job, chat
- [x] SMS router stripped to inbound-webhook-only

**Pricing fixes (April 2026):**
- [x] db_pricebook: standard price only shown to Claude, never range
- [x] proposal_agent: summarize_job strips prices from notes field
- [x] proposal_agent: no-pricebook fallback uses 0, not hardcoded low prices
- [x] pwa_chat_actions: amount flows through chip params to explicit_amount

**Guided estimate flow (April 2026):**
- [x] sql/guided_estimate_tables.sql — run in Supabase
- [x] execution/schema.py — EstimateSessions + JobPricingHistory
- [x] execution/guided_estimate.py — state machine, zero Claude pricing
- [x] execution/db_pricing_history.py — records sent prices
- [x] execution/pwa_chat.py — intercepts estimate intent before Claude
- [x] routes/pwa_routes.py — passes session_id to chat()
- [x] routes/document_routes.py — writes pricing history on /doc/send
- [x] tests/test_guided_estimate.py

**Pending (next sprints):**
- Self-learning agent (null field prompts)
- MMS photo ingestion
- Multi-day invoice drafts (Save & Add)
- Square production credentials
- Customer email collection workflow
- 10DLC — deferred indefinitely
- Guided estimate: voice price input ("three twenty five" → 325)
- Guided estimate: new customer creation sub-flow (S4)
- Guided estimate: outlier price warning (>±50% of history)

Do not suggest features beyond what is explicitly requested.
