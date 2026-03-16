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
│   ├── sms_receive.py                 # Inbound SMS webhook handler
│   ├── sms_send.py                    # Outbound SMS via Telnyx
│   ├── call_claude.py                 # Claude API wrapper
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
```

Never hardcode credentials. Always load from `.env` using 
`python-dotenv`. Never commit `.env` to git.

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

**clients table**
```
id            uuid (auto)
business_name text
owner_name    text  
phone         text (used as lookup key)
personality   text (full personality layer doc)
created_at    timestamp (auto)
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