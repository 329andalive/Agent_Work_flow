# SYSTEM.md — Bolts11 Decision Memory
> This document captures WHY the system works the way it does.
> CLAUDE.md tells you what exists. HANDOFF.md tells you what changed today.
> SYSTEM.md tells you how this system thinks.
>
> Read this before making any architectural decision.
> Update this when a major decision is made — not after the fact.

---

## The One Question Every Decision Gets Measured Against

**Would a 55-year-old rural septic servicer actually use this?**

He runs his business from a truck. He has a phone. He is not going
to learn software. His back office is paper, memory, and his wife
taking calls. He is leaving money on the table every single day.

If the feature requires him to change his behavior significantly —
it's wrong. If it works with how he already operates — it's right.

---

## The Three Document Roles

| Document | Answers | Changes |
|---|---|---|
| CLAUDE.md | What exists — stack, files, schema, hard rules | Rarely |
| HANDOFF.md | What happened today — progress, bugs, pending | Every session |
| SYSTEM.md | Why it works this way — decisions, rules, philosophy | When decisions are made |

Never collapse these into one document. They serve different purposes.

---

## The Whisper Circle Rule

When a decision is made in conversation, it must be written into
a directive or this document BEFORE it is handed to Claude Code.

Claude Code gets the directive. Not the conversation. Not a paste
of a chat thread. A clean, structured, decision-captured document.

If Claude Code is making assumptions to fill gaps — the directive
is incomplete. Stop. Write the decision down. Then proceed.

---

## The Three-Tier Product Model

### Why Three Tiers Exist

10DLC campaign registration is the single biggest friction point
for new clients. It takes weeks, costs fees, and requires business
documentation. A 55-year-old rural operator will not wait 6 weeks
to see value before he decides if this is worth paying for.

The tier model solves this by delivering real value immediately
at every level — including before 10DLC is approved.

### Tier Definitions

**Basic — $49/month**
- Email only. No SMS of any kind.
- Setup: Free
- Who it's for: Operators who want to get off paper today
- What it proves: The system works before asking for more money
- Upsell trigger: System flags → human makes the call (see below)

**Gold — $149/month**
- SMS inbound to system. Email outbound to customers and workers.
- Setup: $149-199 one-time (covers number provisioning)
- Who it's for: Operators whose crew texts from the field
- What it proves: The truck-to-dashboard workflow — the magic moment
- Key constraint: No outbound SMS from Railway without 10DLC.
  Gold outbound to customers and workers is EMAIL ONLY.
  No exceptions. No workarounds built into the system.

**Platinum — $299/month**
- Full SMS inbound and outbound. 10DLC handled by Bolts11.
- Setup: Phased — $200-300 onboarding + $200-300 10DLC registration
- Who it's for: Operators who want customers to receive SMS
- What it proves: Full two-way workflow, highest conversion on estimates
- 10DLC: We file it, we manage it, client pays the carrier fees

**Enterprise — $499/$999+/month**
- Everything in Platinum plus usage-based charges
- Sized by crew: 4-10 people = $499, 10+ people = $999+
- Setup: Custom — scoped per organization
- Usage charges: Per message or per job above baseline volume
- Why crew size: He knows how many guys he has. Revenue is harder
  to verify and fluctuates seasonally. Crew size is a conversation.

### The Land-and-Expand Philosophy

$49/month Basic is acquisition cost disguised as revenue.
The goal is not to make money at Basic. The goal is to get
the operator inside the system, seeing value, building dependency.

The migration trigger: once the client list is imported and
10+ jobs have been processed, the system flags the account.
A human (not an automated email) makes the call to upgrade.

That call converts. An automated email does not.

---

## The Notification System

### The Three Recipient Types

Every outbound notification goes to one of three recipient types.
Each type has different rules. These rules never change based on
who is asking or what seems convenient in the moment.

**Customer** — the end client of the trades business
**Worker** — the crew member assigned to a job
**Owner** — the trades business owner using Bolts11

### Channel Matrix — The Source of Truth

| Recipient | Platinum (10DLC ✅) | Gold (Inbound only) | Basic (Email only) |
|---|---|---|---|
| Customer | SMS cascade → Email | Email cascade only | Email cascade only |
| Worker | SMS only, no cascade | Email only, no cascade | Email only, no cascade |
| Owner | SMS inbound + outbound | SMS inbound only | Email only |

This matrix is the source of truth. When in doubt, check this table.
No agent decides channel logic for itself. All notifications go
through the central notify() function.

### The Central Notify Function

Every agent that sends a notification calls notify() or
notify_worker(). Never implements channel logic directly.

```python
def notify_customer(client, recipient, message, 
                    subject, job_id, cascade=True):
    if client['sms_outbound_enabled']:
        # Platinum — SMS first, email in cascade
        send_sms(recipient['phone'], message)
        schedule_cascade(job_id, recipient, step=1)
    else:
        # Gold and Basic — email only cascade
        send_email(recipient['email'], subject, message)
        schedule_cascade(job_id, recipient, step=1)

def notify_worker(client, worker, message, route_url):
    if client['sms_outbound_enabled']:
        # Platinum only — SMS with route URL
        send_sms(worker['phone'], message + ' ' + route_url)
    else:
        # Gold and Basic — email only, no exceptions
        send_email(worker['email'], 
                   'Job Assignment', message, route_url)
    # No cascade. No retry. One notification. Done.
```

### The Customer Cascade — Full Workflow

```
Day 0:   SMS (Platinum) or Email (Gold/Basic) — estimate sent
           ↓ no response after 48 hours
Day 2:   Email (all tiers) — follow up
           ↓ no response after 48 more hours
Day 4:   SMS + Email (Platinum) or Email (Gold/Basic)
         Message: "Still need this service?
                   We'd like to fit you in."
           ↓ no response
Dead:    Flag in dashboard. Owner notified. Cascade stops.
```

Response detection: SMS reply, email link click, 
dashboard approval action, or YES/NO reply.

### The Worker Rule — Non-Negotiable

Workers are internal. They either show up or they don't.
The system sends one notification. That is all.

**No cascade. No retry. No follow-up. Ever.**

The system's job is clean delivery through the right channel.
Managing worker accountability is the owner's job, not ours.

Dashboard shows delivered/undelivered. Owner sees it. Owner acts.

### The Gold Worker SMS Workaround — By Design, Not a Bug

At Gold tier, the system cannot send SMS to workers — Railway
would send it without 10DLC and it will not go through.

If a Gold owner wants their worker to get the route URL by SMS:
1. System generates the route URL as always
2. Dashboard shows a prominent COPY LINK button on the dispatch card
3. Owner copies the URL manually
4. Owner pastes it into their own messages app and sends it
5. It arrives from the owner's personal number — outside 10DLC entirely

The system makes the manual path easy. The system does not
attempt to automate what it cannot legally send.

---

## The Upsell Trigger System

The system watches for all four conditions simultaneously:

1. Client list imported (50%+ of contacts have a phone number)
2. 10 or more jobs processed
3. 3 or more invoices sent
4. Account age 45-60 days

When all four are true:
- System fires a flag in the admin dashboard
- Note reads: "[Owner name] — [Business] — ready for Gold conversation"
- A human makes a personal call that week
- The call references their actual usage data

Automated emails do not convert this customer. A call from
someone who knows his numbers does.

---

## The Personality Layer — Most Important Concept

Every output from every agent must sound like that specific
business owner wrote it personally. Not Claude. Not software.
Not a robot. That owner, in their voice, for their market.

This is the moat. Any competitor can wire Claude to Telnyx.
Very few will build outputs that sound like Jeremy from B&B Septic.

The personality layer lives at:
directives/clients/{client_phone}/personality.md

It is loaded before any agent does anything. No exceptions.
If it is not loaded, the output will be generic and wrong.

---

## Testing Rules — Non-Negotiable

These rules exist because errors compound. 90% accuracy per step
equals 59% success over 5 steps. Tests are not optional.

**HARD RULE 1:** Every bug fixed gets a test written BEFORE the fix.
**HARD RULE 2:** No push to main without pytest tests/ passing clean.
**HARD RULE 3:** railway.toml build command runs tests before deploy.
**HARD RULE 4:** Never retry paid API calls without checking first.

### Pre-Push Gate — Local

```bash
# .git/hooks/pre-push
#!/bin/bash
pytest tests/ -x -q
if [ $? -ne 0 ]; then
    echo "Tests failed. Push blocked."
    exit 1
fi
```

### Pre-Deploy Gate — Railway

```toml
# railway.toml
[build]
buildCommand = "pip install -r requirements.txt && pytest tests/ -x -q"
startCommand = "gunicorn execution.sms_receive:app --bind 0.0.0.0:$PORT --workers 1 --timeout 60"
```

### What Gets Tested

- Every new agent behavior gets a test
- Every bug fix gets a regression test
- Notification routing logic — channel selection by tier
- Cascade scheduling — correct steps, correct timing
- Multi-tenancy — no data crossing client boundaries
- Worker rule — verify no cascade is ever scheduled for workers
- SMS gate — verify outbound SMS never fires without sms_outbound_enabled

---

## Database Flags That Drive Everything

```sql
-- On clients table
sms_outbound_enabled  boolean DEFAULT false  
-- Flips to true when 10DLC approved (Platinum only)

sms_inbound_enabled   boolean DEFAULT true   
-- True for Gold and above

-- notification_log table
id              uuid
client_phone    text
job_id          uuid
recipient_type  text        -- customer | worker | owner
recipient_phone text
recipient_email text
channel_used    text        -- sms | email
cascade_step    int         -- 0, 1, 2 (null for workers)
sent_at         timestamptz
responded_at    timestamptz -- null until response received
response_type   text        -- replied | clicked | approved | null
next_nudge_at   timestamptz -- null for workers, always
```

---

## What This System Is Not

- Not a CRM that happens to send SMS
- Not a marketing platform
- Not a feature set competing with Jobber or ServiceTitan
- Not software that requires training to use

It is an AI back office that works through a text message.
The interface is the phone he already has.
The training is none.
The value is immediate or it is nothing.

---

*SYSTEM.md — Created March 31, 2026*
*Update this document when decisions are made, not after the fact.*