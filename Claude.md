# CLAUDE.md — Trades AI Agent Stack
> Mirrored across CLAUDE.md, AGENTS.md, and GEMINI.md so the same 
> instructions load in any AI environment.

You are building and operating an AI-powered back office system for 
small trades businesses. The first client vertical is Sewer and Drain. 
Every decision you make should be evaluated against one question: 
would a 55-year-old rural Septic servicer actually use this?

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

## Active Agents
```
proposal_agent        — generates estimates from job descriptions
invoice_agent         — creates invoices from completed job notes
clarification_agent   — intercepts ambiguous SMS, asks follow-ups, routes
followup_agent        — follows up on unanswered estimates
review_agent          — requests Google reviews after job completion
content_agent         — creates social/marketing content
safety_agent          — generates safety checklists and OSHA docs
voice_controller      — routes voice commands to the right agent
```

Each agent has a directive in `directives/agents/{agent_name}.md`

---

## Communication Layer

**Primary interface: SMS via Telnyx**
- Inbound: plumber texts job description to dedicated number
- Outbound: system texts back proposals, invoices, confirmations
- No app required. Works on every phone. Rural friendly.
- When service resumes after dead zones, SMS queues deliver

**Phase 2: ElevenLabs voice interface**
- Owner speaks commands hands-free from truck
- Voice Controller agent routes to appropriate agent
- Transcription via Whisper, response via ElevenLabs TTS

---

## File Organization
```
/
├── CLAUDE.md                          # This file
├── HANDOFF.md                         # Session log — read for current status
├── .env                               # All API keys — never commit
├── .python-version                    # Pins Python 3.12.9 — DO NOT DELETE
├── requirements.txt                   # 66 pinned packages — regenerate with pip freeze
├── execution/                         # Python scripts (deterministic)
│   ├── sms_receive.py                 # Flask app entry point + blueprint registration
│   ├── sms_send.py                    # Outbound SMS via Telnyx + sms_message_log
│   ├── sms_router.py                  # SMS routing + worker reply handler (DONE/BACK/etc)
│   ├── call_claude.py                 # Claude API wrapper (Haiku/Sonnet/Opus)
│   ├── context_loader.py             # Stateful context assembly for commands
│   ├── proposal_agent.py             # Structured JSON proposals + Haiku summarization
│   ├── invoice_agent.py              # Invoice generation + Square Step 8b
│   ├── geocode.py                    # Google Maps geocoding + zone clustering
│   ├── db_scheduling.py             # Scheduling DB helpers (7 functions, multi-tenant)
│   ├── dispatch_suggestion.py       # AI dispatch suggestions (Phase 2, 30+ sessions)
│   ├── scheduled_sms.py            # Nudges, reminders, no-show marking
│   ├── token_generator.py           # Signed token URLs + mark_invoice_paid() fallback
│   ├── square_agent.py              # Square Payment Links API (v44)
│   ├── job_cost_agent.py            # Job cost tracking (defensive)
│   ├── clarification_agent.py       # Claude-powered intent classification
│   ├── db_clarification.py          # DB ops for clarifications + approvals
│   ├── db_customer.py               # Customer table queries
│   ├── db_client.py                 # Client table queries
│   ├── db_jobs.py                   # Job table queries
│   ├── db_proposals.py              # Proposal table queries
│   ├── db_messages.py               # Message logging
│   ├── document_html.py             # Build edit/view HTML for documents
│   ├── db_document.py               # DB ops for edit/learn system
│   └── db_pricing.py                # Pricing benchmarks + adjustment logging
├── routes/                            # Flask Blueprints
│   ├── dashboard_routes.py           # All dashboard pages (20+ templates)
│   ├── dispatch_routes.py           # /api/dispatch/* + /r/<token> worker route
│   ├── booking_routes.py            # /book/<token> + /api/book/* + /api/slots/*
│   ├── command_routes.py            # /api/command + context loader wiring
│   ├── auth_routes.py               # /login, /logout, /set-pin + super admin
│   ├── invoice_routes.py            # /webhooks/square payment webhook
│   ├── document_routes.py           # /doc/edit, /doc/save, /doc/send
│   ├── onboarding_routes.py         # /api/onboarding/*, /onboard/<token>
│   └── routes_debug.py              # /debug (dev-only)
├── templates/                         # Jinja2 HTML templates
│   ├── base.html                      # Shared sidebar + layout (navy/amber)
│   ├── book.html                      # Public booking page (mobile-first)
│   ├── worker_route.html             # Worker route page (mobile, no login)
│   ├── proposal.html                  # Mobile-first proposal view
│   ├── invoice.html                   # Mobile-first invoice view (PAY NOW)
│   ├── error.html                     # Branded error/expired pages
│   └── dashboard/                     # Dashboard templates (extend base.html)
│       ├── control.html              # Control Board
│       ├── dispatch.html             # Dispatch board (drag-drop)
│       ├── classes.html              # Class slot management
│       ├── schedule.html             # Appointment timeline
│       ├── admin.html                # Super admin heartbeat
│       └── ... (20+ templates)       # See HANDOFF.md for full list
├── sql/                               # SQL migrations (run in Supabase)
│   ├── square_payment_writeback.sql
│   └── scheduling_migration.sql
├── directives/                        # SOPs and context docs
│   ├── agents/proposal_agent.md      # Proposal architecture + line item rules
│   └── clients/personality.md        # B&B Septic voice + pricing
└── tests/                             # Test scripts
```

---

## API Credentials (.env)
```
ANTHROPIC_API_KEY=
TELNYX_API_KEY=
TELNYX_PHONE_NUMBER=+12074190986
SUPABASE_URL=https://wczzlvhpryufohjwmxwd.supabase.co
SUPABASE_SERVICE_KEY=
BOLTS11_BASE_URL=https://bolts11.com
SQUARE_ACCESS_TOKEN=
SQUARE_ENVIRONMENT=sandbox
SQUARE_LOCATION_ID=
SQUARE_WEBHOOK_SIGNATURE_KEY=
GOOGLE_MAPS_API_KEY=
```

Never hardcode credentials. Always load from `.env` using
`python-dotenv`. Never commit `.env` to git.

---

## Routes (Flask Blueprints)

See HANDOFF.md URL Map for the complete list (50+ routes).
Key route groups:

**dashboard_bp** — All dashboard pages (20+ templates)
**dispatch_bp** — `/api/dispatch/*` + `/r/<token>` worker route
**booking_bp** — `/book/<token>` + `/api/book/*` + `/api/slots/*`
**command_bp** — `/api/command` + context loader wiring
**auth_bp** — `/login`, `/logout`, `/set-pin` + super admin flag
**invoice_bp** — `/webhooks/square` payment webhook
**document_bp** — `/doc/edit`, `/doc/save`, `/doc/send`
**onboarding_bp** — `/api/onboarding/*`, `/onboard/<token>`

Token routes (`/p/` and `/i/`) handle:
- Invalid tokens → branded error page
- Expired tokens → branded expiry page with contact link
- Valid tokens → update viewed_at, render Jinja2 template, log to agent_activity

Document routes (`/doc/`) handle:
- edit_token-based auth (secret URL, no login)
- Line item editing with auto-recalculate
- Edit diffing logged to estimate_edits table
- Learning loop: after 2+ edits, Claude analyzes patterns → client_prompt_overrides

---

## Platform Hard Rules

**HARD RULE #1 — Phone number required on every customer**
Every customer record must have a phone number. No exceptions.
- `db_customer.create_customer()` raises `ValueError` if phone is missing
- Any agent that creates a customer without a phone fails loudly
- SQL: `ALTER TABLE customers ALTER COLUMN customer_phone SET NOT NULL`

**HARD RULE #2 — SMS opt-in required before texting customers**
Every customer must have explicit SMS opt-in before the platform
texts them. This is a legal requirement (10DLC/CTIA).
- Columns: `sms_consent boolean DEFAULT false`, `sms_consent_at timestamptz`, `sms_consent_src text`
- Every agent that sends SMS to a CUSTOMER (not employee) must check
  `customer.sms_consent` first
- If false: block the SMS, log `sms_blocked_no_optin` to agent_activity
- Opt-in is set via: `SET OPTIN +1XXXXXXXXXX` from owner's phone,
  or customer replies START to the business number

## SMS Routing Order (sms_router.py)

All inbound SMS is processed in this exact sequence, first match wins:
```
1. STOP / YES / START / UNSTOP — opt-in/opt-out (always first)
2. Priority intents — loss_reason, accepted, declined, lost_report
3. No-show response — owner/foreman with open alert
4. Pending clarification — employee has active clarification session
5. Customer approval reply — YES/NO/STOP from customer
6. Explicit keywords — ESTIMATE, SCHEDULE, DONE, CLOCK IN/OUT, SET OPTIN
7. High-confidence keywords — invoice/clock/job_list/noshow phrases
8. Everything else → clarification_agent (Claude classifies intent)
```

---

## Operating Principles

**1. Check for existing scripts first**
Before writing a new execution script, check `execution/`. 
Only create new scripts if none exist for the task.

**2. Self-anneal when things break**
- Read the full error message and stack trace
- Fix the script and test it
- Update the relevant directive with what you learned
- Do not retry API calls that cost money without checking first

**3. Directives are living documents**
Update directives when you discover API limits, better approaches, 
or common errors. Do not overwrite directives without asking. 
They are the institutional memory of this system.

**4. The self-annealing loop**
1. Error occurs
2. Fix the script
3. Test the fix
4. Update the directive
5. System is now stronger

**5. Webhook payload rule — non-negotiable**
Save the raw inbound webhook payload to the database 
BEFORE any processing begins. This is the first line 
of every webhook handler, no exceptions. If downstream 
processing fails, the raw data must still exist for 
recovery and debugging.

**6. Multi-tenancy is sacred**
Every single database query must filter by client_phone 
or tenant identifier. No exceptions. Never return data 
from one client to another. When in doubt, add the filter.

---

## Claude API Call Structure

Every call to Claude must follow this pattern:
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
- Haiku: SMS parsing, simple classifications, data extraction
- Sonnet: Proposals, invoices, follow-ups, review requests
- Opus: Training documents, safety docs, complex reasoning

---

## Supabase Schema

**The cards/needs_attention table**
```
needs_attention table
id                uuid (auto)
client_phone      text
card_type         text  
priority          text (low, medium, high)
related_record    text (job_id, customer_id, message_id)
raw_context       text
claude_suggestion text
status            text (open, resolved, dismissed)
created_at        timestamp (auto)
resolved_by       text
resolved_at       timestamp
```

**The agent_activity log table**
```
agent_activity table
id            uuid (auto)
client_phone  text
agent_name    text
action_taken  text
input_summary text
output_summary text
sms_sent      boolean
created_at    timestamp (auto)
```

**clients table**
```
id            uuid (auto)
business_name text
owner_name    text  
phone         text (used as lookup key)
personality   text (full personality layer doc)
created_at    timestamp (auto)
```
**The customers table**
```
customers table
id              uuid (auto)
client_phone    text (which trade business they belong to)
customer_phone  text NOT NULL (HARD RULE #1)
customer_name   text
address         text
notes           text
sms_consent     boolean NOT NULL DEFAULT false (HARD RULE #2)
sms_consent_at  timestamptz
sms_consent_src text (web_form / owner_command / customer_reply)
last_contact    timestamp
created_at      timestamp (auto)
```

**jobs table**
```
id            uuid (auto)
client_phone  text
agent_used    text
raw_input     text
output        text
created_at    timestamp (auto)
```

**invoice_links table (signed token URLs)**
```
invoice_links table
id            uuid PRIMARY KEY DEFAULT gen_random_uuid()
token         text UNIQUE NOT NULL (8 char alphanumeric)
job_id        text
client_phone  text
type          text (proposal or invoice)
created_at    timestamptz DEFAULT now()
expires_at    timestamptz NOT NULL (72 hours from creation)
viewed_at     timestamptz
expired       boolean DEFAULT false
```

**pending_clarifications table (multi-step intent gathering)**
```
pending_clarifications table
id                      uuid PRIMARY KEY DEFAULT gen_random_uuid()
client_id               uuid REFERENCES clients(id)
employee_phone          text
original_message        text
stage                   integer DEFAULT 1 (1=asked intent, 2=asked address)
collected_intent        text (estimate/schedule/completion/both)
collected_address       text
collected_customer_name text
collected_scope         text
expires_at              timestamptz DEFAULT now() + interval '30 minutes'
created_at              timestamptz DEFAULT now()
```

**customer_approvals table (on-site estimate approval)**
```
customer_approvals table
id                  uuid PRIMARY KEY DEFAULT gen_random_uuid()
client_id           uuid REFERENCES clients(id)
customer_id         uuid REFERENCES customers(id)
job_id              uuid REFERENCES jobs(id)
proposal_id         uuid REFERENCES proposals(id)
tech_phone          text
customer_phone      text
estimate_amount     numeric(10,2)
sent_at             timestamptz DEFAULT now()
expires_at          timestamptz DEFAULT now() + interval '10 minutes'
status              text DEFAULT 'pending' (pending/approved/declined/expired)
followup_1_sent_at  timestamptz
followup_2_sent_at  timestamptz
approved_at         timestamptz
created_at          timestamptz DEFAULT now()
```

**onboarding_sessions table (client setup wizard)**
```
onboarding_sessions table
id                    uuid PRIMARY KEY DEFAULT gen_random_uuid()
client_id             uuid REFERENCES clients(id)
token                 text UNIQUE NOT NULL
status                text DEFAULT 'pending' (pending/in_progress/completed/approved)
created_at            timestamptz DEFAULT now()
expires_at            timestamptz DEFAULT now() + interval '7 days'
completed_at          timestamptz
last_activity_at      timestamptz DEFAULT now()
step_reached          integer DEFAULT 1
company_name          text
owner_name            text
owner_email           text
owner_mobile          text
company_address       text
company_city          text
company_state         text
company_zip           text
company_phone         text
years_in_business     text
trade_vertical        text
trade_specialties     text[]
service_radius_miles  integer
service_area_desc     text
tone_preference       text
customer_type         text
pricing_style         text
tagline               text
how_they_found_us     text
employees_json        jsonb
pricing_json          jsonb
logo_url              text
personality_md        text
personality_md_approved boolean DEFAULT false
```

**trade_verticals table (registry of supported trades)**
```
trade_verticals table
id              uuid PRIMARY KEY DEFAULT gen_random_uuid()
vertical_key    text UNIQUE NOT NULL
vertical_label  text NOT NULL
icon            text
sort_order      integer DEFAULT 0
active          boolean DEFAULT true
specialties     text[]
created_at      timestamptz DEFAULT now()
```

**pricing_benchmarks table (researched service pricing)**
```
pricing_benchmarks table
id              uuid PRIMARY KEY DEFAULT gen_random_uuid()
vertical_key    text NOT NULL
vertical_label  text NOT NULL
service_name    text NOT NULL
price_low       numeric(10,2)
price_typical   numeric(10,2)
price_high      numeric(10,2)
price_unit      text DEFAULT 'per job'
sort_order      integer DEFAULT 0
notes           text
region          text DEFAULT 'northeast_us'
active          boolean DEFAULT true
created_at      timestamptz DEFAULT now()
updated_at      timestamptz DEFAULT now()
```

**pricing_adjustments table (learning foundation)**
```
pricing_adjustments table
id              uuid PRIMARY KEY DEFAULT gen_random_uuid()
client_id       uuid REFERENCES clients(id)
vertical_key    text
service_name    text
original_price  numeric(10,2)
adjusted_price  numeric(10,2)
delta           numeric(10,2)
direction       text (up / down / same)
context         text (proposal_edit / invoice_edit / onboarding_setup / manual_override)
created_at      timestamptz DEFAULT now()
```

**estimate_edits table (edit diff log)**
```
estimate_edits table
id              uuid (auto)
document_type   text (proposal or invoice)
document_id     text
client_id       text
field_changed   text
original_value  text
new_value       text
created_at      timestamptz DEFAULT now()
```

**client_prompt_overrides table (learning loop)**
```
client_prompt_overrides table
id                    uuid (auto)
client_id             text UNIQUE
estimate_style_notes  text
invoice_style_notes   text
updated_at            timestamptz
```

**proposals table (updated columns)**
```
+ line_items    jsonb (array of {description, qty, unit_price, total})
+ edit_token    uuid DEFAULT gen_random_uuid()
+ html_url      text
+ subtotal      numeric
+ tax_rate      numeric
+ tax_amount    numeric
```

**invoices table (updated columns)**
```
+ line_items    jsonb (array of {description, qty, unit_price, total})
+ edit_token    uuid DEFAULT gen_random_uuid()
+ html_url      text
+ subtotal      numeric
+ tax_rate      numeric
+ tax_amount    numeric
```

---

## Testing

Before marking any script complete:
1. Run it with test data
2. Confirm the output is correct
3. Confirm error handling works
4. Update the directive if behavior differed from expected

Test SMS number for dev: use your personal cell
Test client record: create a record in Supabase for your own number

---

## What NOT to Do

- Never hardcode API keys
- Never call paid APIs in a loop without a circuit breaker
- Never overwrite a client's personality.md without confirmation
- Never send SMS to real customers during testing
- Never skip loading the Personality Layer — ever
- Never make the output sound generic — that defeats the purpose

## Current Build Status
Phase 1 — In Progress

Working:
- Inbound SMS webhook routing
- Role-based permissions (owner, foreman, field_tech, office)
- Proposal generation via Claude → styled HTML → SMS link
- Proposal follow-up tracking (accepted/declined/lost)
- Clock in/out stub
- Scheduling agent (SMS parse → job + schedule → confirm)
- Job list query by day

Pending:
- 10DLC registration (SMS sending blocked until approved)
- GUI dashboard (not started)
- needs_attention card system (not started)
- Customer matching logic (not started)

Do not suggest features beyond Phase 1 and Phase 2 
until explicitly asked.