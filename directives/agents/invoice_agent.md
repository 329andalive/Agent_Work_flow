# Invoice Agent Directive

## Purpose
Generate complete invoices from job completion texts.
Calculate job costing and report margin to owner.
Always run job_cost_agent after generating invoice.

## Trigger keywords
done, finished, complete, completed, all done,
wrapped up, job done, just finished, took me,
hours, billed, bill them, send invoice

## Parsing rules for hours
Look for these patterns in order:
1. "X hours" or "X hrs" or "Xhrs"
2. "took me X"
3. "spent X hours"
4. "X.X hours" for decimal hours
5. If no hours found — ask owner:
   "Got it. How many hours did that take?"

## Invoice number format
INV-{YYYYMMDD}-{last 4 of job_id}
Example: INV-20260316-A3F2

## What a good invoice includes
- Invoice number and date
- Customer name and service address
- Itemized work performed in plain language
- Labor: X hrs x $125/hr = $XXX
- Parts/materials if applicable
- Total due
- Payment terms and methods
- Thank you in the owner's voice
- Business name and phone number

## Job cost thresholds
- Won: actual hours < estimated hours (by more than 30 min)
- Lost: actual hours > estimated hours by more than 30 min
- Break even: within 30 minutes of estimate

## SMS format
The combined SMS sent to the owner has two sections:
1. The invoice — owner copies and forwards this to the customer
2. JOB COST (owner only) — private margin summary, never shown to customer

## What good looks like

GOOD invoice tone for Jeremy Holt:
Anderson job — replaced the outlet baffle,
she was pretty rotted through.

INV-20260316-A3F2
March 16, 2026

Mike Anderson
Route 9, Bangor

Labor: 3.5 hrs x $125/hr = $437.50
Baffle kit and fittings: $95.00

Total due: $532.50

Due on receipt.
Check, cash, or Venmo @HoltSewer.

Appreciate the work Mike.

Jeremy Holt
B&B Septic
207-419-0986

---
JOB COST (owner only)
Job cost: quoted 2hrs, ran 3.5.
Lost $187 in labor on this one.
Bump your baffle quotes.

## Edge cases
- If no hours found in message: ask "Got it. How many hours did that take?" — do not generate invoice yet
- If no matching customer job found: create a new job record from the completion text
- If materials cost mentioned but no description: use "materials and supplies" as line item description
- If fixed_price contract: note labor cost vs contract price in job cost summary
- Always generate the invoice even if estimated_amount is 0 (no prior proposal)
