# Proposal Agent Directive

## Purpose
Generate professional, structured proposals for septic and sewer service jobs.
Every proposal must sound like the business owner wrote it personally.
Every line item must be a clean, specific trade description — never raw SMS text.

## Trigger keywords
quote, estimate, proposal, price, cost, how much,
pump, inspect, inspection, repair, locate, find,
emergency, backup, overflow, smell, alarm

## Architecture — How This Agent Works

### Customer Resolution (command_routes.py, NOT this agent)
- Command Center commands come from the OWNER, never a customer
- The owner's phone (client.phone, client.owner_mobile) must NEVER be
  treated as a customer phone — skip customer creation entirely
- Customer is resolved from the TEXT: extract name, search DB by ilike
- If name matches exactly one customer → use it
- If zero or multiple matches → ask owner to clarify before proceeding

### Job Description Summarization (Step 5)
- Raw owner input is NEVER stored as job_description
- Haiku summarizes raw_input into a clean 1-line description (≤15 words)
- raw_input stays in jobs.raw_input for audit trail
- job_summary goes into jobs.job_description for display

### Structured Line Item Generation (Step 6)
- Claude Sonnet receives a structured prompt demanding JSON output
- Response MUST be: `{"job_summary": "...", "line_items": [...], "notes": "..."}`
- Each line item: `{"description": "...", "amount": 000.00}`
- If JSON parsing fails, fall back to single line item with extracted amount
- Line items are saved to proposals.line_items as JSONB

### Line Item Rules — NON-NEGOTIABLE
- Each line item describes ONE specific item of work or material
- Description uses trade language only: "Septic pump-out — 1,000 gal. tank"
- Never include customer names, greetings, or partial sentences
- Never truncate — every description must be complete
- Amount is a number, no $ sign
- Minimum charge is $150
- If owner specified a price, use it exactly
- If owner specified hours, calculate at $125/hr

## Typical job types and price ranges (Rural Maine 2026)

Septic pump-out (standard 1000 gal tank): $275 - $375
Septic pump-out (large 1500 gal tank):    $350 - $450
Septic inspection (visual):               $150 - $250
Septic inspection (full with report):     $300 - $450
Tank locate and mark:                     $150 - $200
Distribution box repair:                  $800 - $1200
Baffle replacement:                       $175 - $250
Emergency pump-out:                       $450 - $650
Leach field evaluation:                   $200 - $400
New system design consult:                $450 - $600
12" riser and cover:                      $250 - $350

## Edge cases
- If job type is unclear, default to inspection + pump
- If no address given, note "address not provided" — do not make one up
- If customer mentions smell or alarm, treat as emergency pricing
- If customer mentions it has been over 3 years, recommend pump
- If owner gives explicit pricing, use their numbers exactly

## What correct output looks like

INPUT: "Wentworth needs his thousand gallon tank pumped and baffle
replaced, he needs a 12 inch riser and cover installed the riser
and cover will cost $250 the job will be five hours labor with travel"

EXPECTED JSON:
```json
{
  "job_summary": "Septic pump-out and baffle replacement with riser installation — 1,000 gal. tank",
  "line_items": [
    {"description": "Septic pump-out — 1,000 gal. tank", "amount": 275.00},
    {"description": "Baffle replacement", "amount": 175.00},
    {"description": "12\" riser and cover installation", "amount": 250.00},
    {"description": "Labor: 5 hrs @ $125/hr", "amount": 625.00}
  ],
  "notes": ""
}
```

## What WRONG output looks like (never do this)
- "Hey Wentworth, here's your estimate..." ← customer name in line item
- "pump out and stuff" ← truncated, vague
- "The total comes to $1,325" ← prose, not a line item
- Raw SMS text pasted into line_items ← architectural failure
