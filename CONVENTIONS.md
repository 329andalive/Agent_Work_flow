# CONVENTIONS.md — How to write code in this repo

> Read this before writing any database code, any new agent, any new
> route. It's short on purpose. The rules here come from real bugs
> we've already fixed at least once.

## The single source of truth

Every Supabase column name lives in **[`execution/schema.py`](execution/schema.py)**.
That file has one class per table with constants for every column we
touch. New code that hits the database must import from there:

```python
from execution.schema import Customers as C, Proposals as P

sb.table(C.TABLE).select(
    f"{C.ID}, {C.CUSTOMER_NAME}, {C.CUSTOMER_PHONE}"
).eq(C.CLIENT_ID, client_id).execute()
```

A typo (`C.CLINT_ID`) becomes `AttributeError` at import time, not a
PostgREST error 30 minutes later in production logs. If a column name
ever has to change, you rename it in `schema.py` and grep tells you
every site that needs updating.

**You don't have to refactor existing files in one go.** Convert agents
to use schema constants when you're already touching them for another
reason. The two paths coexist cleanly because the constants are just
strings — `C.CLIENT_ID == "client_id"`, no magic.

---

## Naming rules

These match what's actually in the database. Don't reinvent them per
table — the cost of inconsistency compounds across agents.

| Kind | Convention | Example |
|---|---|---|
| Primary key inside a table | `id` | `customers.id`, `proposals.id` |
| Foreign key to another table | `{singular_target}_id` | `customer_id`, `job_id`, `client_id` |
| Human-readable reference numbers | `{thing}_number` | `invoice_number`, `job_number` |
| Timestamps | `{event}_at`, past tense | `created_at`, `sent_at`, `accepted_at`, `paid_at` |
| Booleans | `is_{thing}` or descriptive verb | `is_paid`, `sms_consent`, `scope_hold`, `active` |
| Free-form text scope | `{thing}_text` or `{thing}_notes` | `proposal_text`, `job_notes`, `invoice_text` |
| Money on a document | the canonical column for THAT table | `proposals.amount_estimate`, `invoices.amount_due` |

**Three quirks worth knowing about:**

1. `time_entries.clock_in` and `clock_out` are NOT `_at` suffixed.
   Legacy from before this convention existed. Don't try to "fix" it.
2. `agent_activity` and `needs_attention` use `client_phone` (text) as
   the tenant column, not `client_id` (uuid). They predate the
   ID-based multi-tenant refactor and were never migrated.
3. `route_assignments.worker_id` uses "worker" terminology, not
   "employee" or "tech". The whole dispatch domain is consistent on
   this — `dispatch_decisions.worker_id`, `route_tokens.worker_id`,
   etc. The PWA and clock domains use `employee_id`. The table
   `pwa_tokens` uses `tech_id`. There is no single convention across
   the whole DB; there's a single convention per table family. When
   in doubt, check `schema.py`.

---

## DO NOT

Concrete things we've shipped fixes for at least once. Each one was
a runtime bug that surfaced in production logs:

- **DO NOT** use `address` for customers — the column is `customer_address`.
- **DO NOT** use `phone` for customers — the column is `customer_phone`.
- **DO NOT** use `name` for customers — the column is `customer_name`.
- **DO NOT** use `email` for customers — the column is `customer_email`.
- **DO NOT** write `subtotal`, `tax_rate`, or `tax_amount` to the
  `proposals` table. **Those columns do not exist on proposals.** They
  exist on `invoices` only. `update_proposal_fields()` keeps them in
  the function signature for backwards compat with `/doc/save` callers
  but silently drops them from the actual update dict.
- **DO NOT** use `employee_id` when querying `pwa_tokens` — the column
  is `tech_id`. (The Flask `session["employee_id"]` key is unrelated;
  that's a session key, not a DB column.)
- **DO NOT** use `followup_type` — the column is `follow_up_type` (with
  the underscore between "follow" and "up").
- **DO NOT** use `worker_id` on `time_entries` or `pwa_chat_messages` —
  those tables use `employee_id`. Conversely don't use `employee_id`
  on `route_assignments` or `dispatch_decisions` — those use
  `worker_id`. See `schema.py` if you can't remember which is which.
- **DO NOT** put prices, totals, or dollar figures in `proposal_text`
  or `invoice_text`. Those fields are customer-facing scope notes only.
  A render-time filter in `document_html.py` strips any line containing
  `$` from the customer view, but the safer path is to never write the
  prices in the first place.
- **DO NOT** call `update_proposal_status(id, "sent")` from
  `proposal_agent.run()`. The agent's job is to draft. The transition
  to `status='sent'` and the `sent_at` write happen in `/doc/send`
  (the route the owner hits when they tap Approve & Send) — never at
  draft time. Same rule applies to `schedule_followup()` for the
  3-day estimate follow-up — that timer starts in `/doc/send`, not in
  the agent.
- **DO NOT** call `send_sms()` from anything except the existing
  `notify()` router. Telnyx outbound is **dead at the carrier** per
  HARD RULE #7 — calls to it will silently fail or get blocked. If
  you need to message a worker or owner, route through `notify()`,
  which falls back to email automatically.
- **DO NOT** bypass the `db_*.py` repository helpers and write inline
  `sb.table(...)` queries from agents or routes when there's already a
  helper for it. If a helper doesn't exist for what you need, ADD ONE
  to the appropriate `db_*.py` file rather than inlining the query.
  See "Repository pattern" below.
- **DO NOT** invent customer names, phone numbers, or addresses in any
  agent prompt. The chat agent has a CRITICAL RULES block forbidding
  this; the proposal agent uses the matched-customer block from
  `_find_customer()`. If you need customer data, look it up — never
  guess.
- **DO NOT** depend on Supabase Storage `content-type` headers to
  render customer documents. The header is dropped silently across
  storage3 SDK versions. Customer document URLs go through the
  Flask `/p/<token>` (proposals) and `/i/<token>` (invoices) routes,
  which `render_template()` server-side and get the right content
  type for free.
- **DO NOT** hardcode `BOLTS11_BASE_URL` in any agent. Read from
  `os.environ.get("BOLTS11_BASE_URL", "https://bolts11.com")` so the
  same code works in dev, staging, and production.

---

## Repository pattern (lite)

Every table has a `db_*.py` helper file in `execution/`. The intent is
that every agent calls helper functions instead of writing inline SQL.
We have the files; the discipline is keeping the queries in them.

| Table | Helper file |
|---|---|
| `clients` | `db_client.py` |
| `customers` | `db_customer.py` |
| `employees` | `db_employee.py` |
| `jobs` | `db_jobs.py` |
| `proposals` | `db_proposals.py` |
| `invoices` | `db_invoices.py` |
| `follow_ups` | `db_followups.py` |
| `pwa_tokens` | `pwa_auth.py` (legacy location) |
| `pwa_chat_messages` | `pwa_chat_messages.py` |
| `agent_activity` | `db_agent_activity.py` |
| `needs_attention` | (inlined in routes/agents currently — TODO consolidate) |

**Rule:** if you're about to write `sb.table(...)` in an agent or
route, stop and check if there's already a helper for it. If there
isn't, add one to the helper file, not inline. The helper file is
where the schema constants get used heaviest, and where future
schema changes propagate cleanest.

There are still legacy inline queries scattered through agents that
predate this rule. Don't make it worse. Convert them as you touch
them for other reasons.

---

## How to add a new column to an existing table

1. Add the column in the **Supabase dashboard** (SQL editor or table
   editor) — that's the single source of truth for the actual schema.
2. Add the matching constant to the right class in
   [`execution/schema.py`](execution/schema.py). Document any gotchas
   in a comment next to the constant.
3. Add or update the helper function in the right `db_*.py` file.
4. Use the schema constant from your agent code.
5. If the new column has a known-dead alternative spelling that
   someone might mistakenly use (like `subtotal` on proposals), add a
   guard to `tests/test_schema.py` asserting the dead name is NOT
   present.

---

## How to add a new table

1. Create the table in Supabase. Follow the naming rules above.
2. Add a new class to `execution/schema.py` with `TABLE` + every column.
3. Create a `db_<table>.py` helper file with the obvious CRUD functions.
4. If the table is multi-tenant (almost every table is), every query
   in the helper file MUST filter by `client_id` (or `client_phone`
   for the legacy tables). HARD RULE #4.
5. Add a row to the table-helper map above in this file.

---

## Where to look when you're stuck

| You want to know... | Look here |
|---|---|
| What columns does this table have? | [`execution/schema.py`](execution/schema.py) |
| Why is this code structured this way? | `CLAUDE.md` (loaded into every Claude Code session) |
| Why does this agent do what it does? | `directives/agents/<agent_name>.md` |
| What's the personality of this client? | `directives/clients/<phone>/personality.md` |
| Is this a known bad pattern? | The "DO NOT" list above |
| Has this bug been fixed before? | `git log --grep "<keyword>"` |
