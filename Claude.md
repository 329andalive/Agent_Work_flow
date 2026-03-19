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
proposal_agent      — generates estimates from job descriptions
invoice_agent       — creates invoices from completed job notes  
followup_agent      — follows up on unanswered estimates
review_agent        — requests Google reviews after job completion
content_agent       — creates social/marketing content
safety_agent        — generates safety checklists and OSHA docs
voice_controller    — routes voice commands to the right agent
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
├── .env                               # All API keys — never commit
├── execution/                         # Python scripts (deterministic)
│   ├── sms_receive.py                 # Inbound SMS webhook handler + Flask routes
│   ├── sms_send.py                    # Outbound SMS via Telnyx
│   ├── call_claude.py                 # Claude API wrapper
│   ├── token_generator.py             # Signed token URLs for proposals/invoices
│   ├── db_get_client.py               # Fetch client from Supabase
│   ├── db_save_job.py                 # Save job record to Supabase
│   └── load_personality.py            # Load client personality doc
├── directives/                        # SOPs and context docs
│   ├── agents/                        # One .md per agent
│   │   ├── proposal_agent.md
│   │   ├── invoice_agent.md
│   │   ├── followup_agent.md
│   │   ├── review_agent.md
│   │   ├── content_agent.md
│   │   ├── safety_agent.md
│   │   └── voice_controller.md
│   └── clients/                       # One folder per client
│       └── {client_phone}/
│           └── personality.md         # The master context document
├── templates/                         # Jinja2 HTML templates
│   ├── proposal.html                  # Mobile-first proposal view
│   ├── invoice.html                   # Mobile-first invoice view (PAY NOW)
│   └── error.html                     # Branded error/expired pages
├── .tmp/                              # Temp files — never commit
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
```

Never hardcode credentials. Always load from `.env` using
`python-dotenv`. Never commit `.env` to git.

---

## Routes (Flask — sms_receive.py)

```
POST /webhooks/telnyx          — Primary Telnyx inbound webhook (Ed25519 verified)
POST /webhooks/telnyx/failover — Telnyx failover webhook
POST /webhook/inbound          — Legacy inbound webhook (deprecated)
POST /book/submit              — Customer booking form submission
GET  /p/<token>                — Serve proposal via signed token (72hr expiry)
GET  /i/<token>                — Serve invoice via signed token (72hr expiry)
GET  /dashboard/               — Dispatch board
GET  /dashboard/office.html    — Office dashboard
GET  /book                     — Customer booking form
GET  /health                   — Health check
```

Token routes (`/p/` and `/i/`) handle:
- Invalid tokens → branded error page
- Expired tokens → branded expiry page with contact link
- Valid tokens → update viewed_at, render Jinja2 template, log to agent_activity

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
customer_phone  text
customer_name   text
address         text
notes           text
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