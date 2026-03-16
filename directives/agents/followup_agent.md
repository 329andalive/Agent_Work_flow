# Follow-Up Agent — Directive

## Purpose

The follow-up agent keeps proposals alive, collects acceptance or rejection signals,
and tracks *why* jobs are lost so the owner can improve over time.
It never bothers a customer more than 3 times. It always sounds like the owner wrote it.

---

## Who Triggers This Agent

| Trigger | Who sends | What happens |
|---------|-----------|--------------|
| Cron every 30 min | System | Sends due follow-ups, marks cold proposals |
| Inbound SMS: customer accepts | Customer | Marks accepted, confirms, notifies owner |
| Inbound SMS: customer declines | Customer | Marks declined, asks owner why |
| Inbound SMS: owner loss reply | Owner | Records loss reason, updates monthly outcomes |
| Inbound SMS: owner reports loss | Owner | Same as customer decline path |

---

## Follow-Up Touch Schedule

After a proposal is sent, the follow-up queue is populated automatically:

| Touch | Type | When | Message style |
|-------|------|------|---------------|
| 1st | estimate_followup | Day 3 | Casual check-in, any questions? |
| 2nd | estimate_followup | Day 7 | Brief, acknowledge it's been a week |
| 3rd | estimate_followup | Day 14 | Final check before going cold |

If no response after 14 days:
- Proposal is marked **cold**
- Owner is asked why they think they lost it
- Job is moved to **lost** status
- Monthly outcomes table is updated

Maximum of 3 touches per proposal. `count_followups_sent_for_proposal()` enforces this.

---

## Message Rules

1. **Under 160 characters** for estimate follow-ups
2. **Under 200 characters** for the cold/final message
3. **Never say "just checking in"** — too weak
4. **Never use exclamation points** — not Jeremy's style
5. **Sound like a real person texting** — short, direct, no corporate speak
6. Mention the job type or customer name when possible
7. The cold message should be warm and gracious — don't burn the relationship

---

## Loss Reason Codes

Owners reply to the "why did you lose it" question with either a number or written text:

| Code | Number shortcut | Meaning |
|------|-----------------|---------|
| price | 1 | Customer said it was too expensive or found cheaper |
| timing | 2 | Owner couldn't schedule fast enough, or was too far out |
| competition | 3 | Customer went with another contractor |
| relationship | 4 | Customer knew someone (neighbor, family, friend) |
| unknown | — | Owner isn't sure |

These are recorded in both `lost_jobs.lost_reason` and `proposals.lost_reason`.

---

## Monthly Outcomes

`proposal_outcomes` table stores one row per client per month.

Updated after every:
- Proposal accepted
- Proposal declined
- Proposal marked cold
- Loss reason recorded

The monthly report SMS is sent on the 1st of each month by `cron_runner.py`.

**Format:** `{month} recap: {accepted}/{sent} quotes won ({rate}%) | Revenue won: ${won} | Lost: ${lost} | Top loss reason: {reason}`

Keep under 300 characters.

---

## What This Agent Does NOT Do

- Does not send more than 3 follow-ups per proposal
- Does not chase customers who already responded (accepted or declined)
- Does not modify invoice data
- Does not send follow-ups for jobs that are already won, lost, or completed
- Does not route inbound messages itself — `sms_router.py` handles that

---

## Database Tables Used

| Table | Purpose |
|-------|---------|
| `follow_ups` | Queue of scheduled follow-up messages |
| `proposals` | Updated with response_type, responded_at |
| `lost_jobs` | One record per lost proposal |
| `proposal_outcomes` | Monthly closing rate tracker |
| `jobs` | Status updated (scheduled, lost) |

---

## Edge Cases

**No response to "why did you lose it"**
Owner doesn't have to answer. The pending `lost_job_why` follow-up stays in the table.
If they answer later, the router will still route it correctly via `get_pending_followups_by_type`.

**Owner texts a loss reason when no pending question**
`detect_response_type()` returns `loss_reason` but `get_pending_followups_by_type()` finds nothing.
Router falls through to keyword detection. The message goes to proposal_agent as a new request (safe fallback).

**Customer texts ambiguous reply**
"Sounds great" → `accepted`. "Not right now" → `declined`. These are keyword matches.
If genuinely ambiguous (no keyword match) → `unknown` → falls to proposal_agent (new job flow).

**Double-tap — customer texts twice**
Second acceptance attempt looks up latest sent proposal. If already accepted, `get_latest_sent_proposal_for_customer`
won't find a "sent" proposal (status already changed), so it returns None and logs a warning. Safe.
