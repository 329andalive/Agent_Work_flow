# CLAUDE.md — Trades AI Agent Stack
> Mirrored across CLAUDE.md, AGENTS.md, and GEMINI.md so the same
> instructions load in any AI environment.

You are building and operating an AI-powered back office system for
small trades businesses. The first client vertical is Sewer and Drain.
Every decision you make should be evaluated against one question:
would a 55-year-old rural tradesman actually use this?

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

## SMS — Retained for Notifications Only

SMS via Telnyx brand number is kept for:
- Clock in / clock out confirmations (one-way echo back to tech)
- Dispatch alerts ("Next job: 123 Main St, Bob Jones, AC unit")
- "Your review link is ready" with URL (tech taps → opens PWA)
- Owner/foreman internal alerts

SMS is **not** used for:
- Two-way AI conversation with techs (→ PWA chat instead)
- Sending proposals or invoices to customers (→ email via Resend)
- Customer approval flows (→ email link or PWA)
- Any flow that requires 10DLC campaign registration

**SMS routing order (sms_router.py) — retained for legacy/fallback:**
```
1. STOP / YES / START / UNSTOP — opt-in/opt-out (always first)
2. CLOCK IN / CLOCK OUT — echo confirmation, log to jobs table
3. Everything else → return PWA link with instructions
```

---

## Communication Layer Summary

| Channel | Direction | Used For | Provider |
|---|---|---|---|
| SMS (brand number) | Inbound + limited outbound | Clock in/out, dispatch pings, review link delivery | Telnyx |
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

## File Organization

```
/
├── CLAUDE.md                          # This file
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
│   ├── sms_receive.py                 # Flask app entry point + blueprint registration
│   ├── sms_send.py                    # Outbound SMS via Telnyx (notifications only)
│   ├── sms_router.py                  # SMS routing (clock in/out + PWA redirect)
│   ├── call_claude.py                 # Claude API wrapper (Haiku/Sonnet/Opus)
│   ├── context_loader.py             # Stateful context assembly for commands
│   ├── proposal_agent.py             # Draft-first estimates — always returns review link
│   ├── invoice_agent.py              # Draft-first invoices — always returns review link
│   ├── self_learning_agent.py        # NULL field prompts, confidence scoring (NEW)
│   ├── geocode.py                    # Google Maps geocoding + zone clustering
│   ├── db_scheduling.py             # Scheduling DB helpers (multi-tenant)
│   ├── dispatch_suggestion.py       # AI dispatch suggestions
│   ├── scheduled_sms.py            # Nudges, reminders, no-show marking
│   ├── token_generator.py           # Signed token URLs + mark_invoice_paid() fallback
│   ├── square_agent.py              # Square Payment Links API
│   ├── job_cost_agent.py            # Job cost tracking
│   ├── clarification_agent.py       # Claude-powered intent classification
│   ├── db_clarification.py          # DB ops for clarifications + approvals
│   ├── db_customer.py               # Customer table queries
│   ├── db_client.py                 # Client table queries
│   ├── db_jobs.py                   # Job table queries
│   ├── db_proposals.py              # Proposal table queries
│   ├── db_messages.py               # Message logging
│   ├── document_html.py             # Build edit/view HTML for documents
│   ├── db_document.py               # DB ops for edit/learn system
│   ├── db_pricing.py                # Pricing benchmarks + adjustment logging
│   └── resend_agent.py              # Email delivery via Resend (primary outbound)
├── routes/                            # Flask Blueprints
│   ├── pwa_routes.py                 # /pwa/* — Tech PWA shell + screens (NEW)
│   ├── dashboard_routes.py           # All dashboard pages (read-only, owner/office)
│   ├── dispatch_routes.py           # /api/dispatch/* + /r/<token> worker route
│   ├── booking_routes.py            # /book/<token> + /api/book/* + /api/slots/*
│   ├── command_routes.py            # /api/command + context loader wiring
│   ├── auth_routes.py               # /login, /logout, /set-pin + super admin
│   ├── invoice_routes.py            # /webhooks/square payment webhook
│   ├── document_routes.py           # /doc/edit, /doc/save, /doc/send
│   ├── onboarding_routes.py         # /api/onboarding/*, /onboard/<token>
│   └── routes_debug.py              # /debug (dev-only)
├── templates/
│   ├── pwa/                           # PWA tech-facing screens (NEW)
│   │   ├── shell.html                 # PWA shell (manifest + SW registration)
│   │   ├── dashboard.html             # Job dashboard (today's jobs, status)
│   │   ├── clock.html                 # Clock in/out screen
│   │   ├── job_input.html             # New job description input
│   │   └── chat.html                  # AI chat window
│   ├── base.html                      # Shared sidebar + layout (navy/amber)
│   ├── book.html                      # Public booking page (mobile-first)
│   ├── worker_route.html             # Worker route page (mobile, no login)
│   ├── proposal.html                  # Mobile-first proposal view
│   ├── invoice.html                   # Mobile-first invoice view (PAY NOW)
│   ├── error.html                     # Branded error/expired pages
│   └── dashboard/                     # Dashboard templates (owner/office only)
│       ├── control.html              # Control Board
│       ├── dispatch.html             # Dispatch board (drag-drop)
│       ├── schedule.html             # Appointment timeline
│       └── ... (read-only reporting pages)
├── sql/                               # SQL migrations
│   ├── draft_corrections.sql          # NEW: Training loop table
│   ├── job_photos.sql                 # NEW: MMS photo storage
│   ├── invoice_drafts.sql             # NEW: Multi-day job partials
│   ├── job_extended_data.sql          # NEW: Trade-specific fields
│   ├── square_payment_writeback.sql
│   └── scheduling_migration.sql
├── directives/                        # SOPs and context docs
│   ├── agents/proposal_agent.md
│   └── clients/personality.md
└── tests/
```

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

**draft_corrections (NEW — training loop)**
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

**job_photos (NEW — MMS ingestion)**
```
id              uuid PRIMARY KEY
job_id          uuid REFERENCES jobs(id)
client_id       uuid REFERENCES clients(id) NOT NULL
storage_path    text NOT NULL
thumbnail_path  text
caption         text
sort_order      integer DEFAULT 0
source          text DEFAULT 'tech_mms'  -- tech_mms | review_ui | owner_upload
created_at      timestamptz DEFAULT now()
```

**invoice_drafts (NEW — multi-day jobs)**
```
id                  uuid PRIMARY KEY
job_id              uuid REFERENCES jobs(id)
client_id           uuid REFERENCES clients(id) NOT NULL
customer_id         uuid REFERENCES customers(id)
draft_date          date NOT NULL
status              text DEFAULT 'draft'  -- draft | saved | compiled | sent
line_items          jsonb DEFAULT '[]'
labor_hours         numeric(5,2)
material_entries    jsonb DEFAULT '[]'
photos              jsonb DEFAULT '[]'
tech_id             uuid
corrections_applied boolean DEFAULT false
compiled_into       uuid  -- FK to invoices.id when compiled
created_at          timestamptz DEFAULT now()
```

**job_extended_data (NEW — trade-specific fields)**
```
id          uuid PRIMARY KEY
job_id      uuid REFERENCES jobs(id) NOT NULL
client_id   uuid REFERENCES clients(id) NOT NULL
field_name  text NOT NULL
field_value text
field_type  text DEFAULT 'text'  -- number | text | boolean
source      text DEFAULT 'tech_pwa'  -- tech_pwa | tech_sms | ai_inferred | manual
confidence  numeric(3,2) DEFAULT 0.0
created_at  timestamptz DEFAULT now()
```

**needs_attention table**
```
id                uuid (auto)
client_phone      text
card_type         text
priority          text  -- low | medium | high
related_record    text
raw_context       text
claude_suggestion text
status            text  -- open | resolved | dismissed
created_at        timestamp (auto)
resolved_by       text
resolved_at       timestamp
```

**agent_activity log**
```
id              uuid (auto)
client_phone    text
agent_name      text
action_taken    text
input_summary   text
output_summary  text
sms_sent        boolean
created_at      timestamp (auto)
```

**clients table**
```
id            uuid (auto)
business_name text
owner_name    text
phone         text (lookup key)
personality   text (full personality layer doc)
created_at    timestamp (auto)
```

**customers table**
```
id              uuid (auto)
client_phone    text
customer_phone  text NOT NULL (HARD RULE #1)
customer_name   text
address         text
notes           text
sms_consent     boolean NOT NULL DEFAULT false
sms_consent_at  timestamptz
sms_consent_src text
last_contact    timestamp
created_at      timestamp (auto)
```

**proposals / invoices tables (updated columns)**
```
+ line_items    jsonb
+ edit_token    uuid DEFAULT gen_random_uuid()
+ html_url      text
+ subtotal      numeric
+ tax_rate      numeric
+ tax_amount    numeric
+ status        text DEFAULT 'draft'
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

---

## Current Build Status

**Phase 1 — PWA Foundation (Active Sprint)**

Completed (pre-pivot):
- Inbound SMS webhook routing (brand number)
- Role-based permissions (owner, foreman, field_tech, office)
- Proposal + invoice generation via Claude → styled HTML → email
- Proposal follow-up tracking (accepted/declined/lost)
- Clock in/out stub
- Scheduling agent
- Email delivery via Resend
- Dashboard (20+ pages, dispatch board, planner, control board)
- Square payment links (sandbox)
- Document edit + diff tracking (/doc/edit/<token>)

PWA Sprint (current):
- [x] static/manifest.json
- [x] static/sw.js
- [x] PWA manifest link in /doc/edit/<token> template
- [x] /pwa/ shell route + template
- [x] /pwa/login — magic link auth (phone entry + email/SMS delivery)
- [x] /pwa/auth/<token> — token verification + session creation
- [x] @require_pwa_auth decorator — protects all /pwa/ routes
- [x] pwa_tokens table — single-use, 15-min expiry, audit trail
- [x] /pwa/clock — clock in/out screen with live elapsed timer,
- [x] /pwa/route — today's route, per-job actions, auto-advance on done
- [x] /pwa/job — new job input, customer resolver, proposal_agent
      pipeline, deep link to review screen on success
- [x] /pwa/chat — conversational AI, action chips, voice input,
      persistent history, fuzzy job matching, 6a+6b complete
- [x] SMS router simplified to notifications + PWA redirect —
      80 lines, 3-step flow (opt-in/out, clock punch, PWA redirect)

Pending (post-PWA):
- Self-learning agent (null field prompts)
- MMS photo ingestion
- Multi-day invoice drafts (Save & Add)
- Square production credentials
- Customer email collection workflow
- 10DLC — deferred indefinitely

Do not suggest features beyond the PWA sprint
until explicitly asked.