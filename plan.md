# Plan: Iron Out Bolts11 — Post-PWA-Pivot Roadmap

**Status:** Not started
**Started:** 2026-04-23

## Goal
Take the project from "functionally complete Phase 1" to "confidently ready for
first field client." The PWA pivot, unified document creation, and dispatch
fixes are shipped — now we close loose ends, verify recent work in production,
and unblock Square-go-live + inbound SMS. No new features are required to ship;
every item below is either cleanup, verification, or removing a known ship-blocker.

## Non-goals
- 10DLC outbound SMS registration (explicitly deferred — email is the customer channel)
- Multi-tenant billing / Stripe integration
- Voice command input / Whisper wiring (Phase 3)
- Content agent, safety agent, self-learning agent (Phase 2 — out of scope here)
- Any UI refactor beyond what's needed to fix a specific bug
- Dashboard mobile responsiveness (owners use laptops)

## Constraints
- **Hard Rule #2:** customer-facing outbound is email-only (Resend)
- **Hard Rule #7:** Telnyx outbound is dead at the carrier — do not add `send_sms` anywhere
- **Hard Rule #8:** AI never generates prices — tech-entered only
- **Multi-tenancy:** every new DB query must filter by `client_id` — check `CONVENTIONS.md` before opening a PR
- **Schema source of truth:** use `execution/schema.py` classes for column names (no magic strings)
- Python 3.12.9 pinned via `.python-version` — do not bump
- Test suite must stay green (290 passing, `tests/test_pwa.py` deselected) before each commit

## Relevant files / context
- [CLAUDE.md](CLAUDE.md) — project rules, current build status, hard rules, schema notes
- [HANDOFF.md](HANDOFF.md) — session log, DNS/deploy to-dos
- [CONVENTIONS.md](CONVENTIONS.md) — DO NOT list, naming, lite repository pattern
- [execution/schema.py](execution/schema.py) — single source of truth for every column
- [deploy.md](deploy.md) — Railway + admin.bolts11.com setup
- [execution/sms_receive.py](execution/sms_receive.py) — tenant app entry (gunicorn target)
- [admin_app.py](admin_app.py) — separate admin Flask app
- [sql/](sql/) — 18 migrations, run status currently opaque

## Approach
Work top-down in five phases. Each phase is independent enough that we can stop
at any point and still have shipped useful work. Inside each phase, subtasks
are ordered by blast radius — smallest/safest first. One subtask = one commit.
The main trade-off: we're prioritizing de-risking production launch over new
features. If a real client signal forces a feature into scope, we stop, update
this doc in the Decisions log, and insert the new subtask.

## Open questions
- [ ] **Brand number live yet?** HANDOFF says Telnyx brand number needs registration for inbound. Is this done? (If not, Phase 3 Subtask 3.1 is a blocker.)
- [ ] **admin.bolts11.com DNS cut over?** HANDOFF:49 says CNAME still needed in Cloudflare. Check before claiming admin is production.
- [ ] **Which SQL migrations have actually been run in Supabase?** No tracking table exists. Phase 1 Subtask 1.4 addresses this.
- [ ] **Is the Review tile + PWA New Job form working with real data?** Shipped commits `7b71efc` and `e388754` — verify before considering them done.
- [ ] **Does the WO backlog fix (commit `7926829`) hold with live WOs?** Verify before closing.

## Subtasks

### Phase 1 — Stabilize (fast, low risk)
- [ ] **1.1** Fix 4 stale PWA template tests in `tests/test_pwa.py` to match current shell + chat markup, OR mark them as intentionally deselected with a one-line reason. Get CI fully green.
- [ ] **1.2** Replace 4 instances of `datetime.utcnow()` with `datetime.now(timezone.utc)` in `routes/admin_routes.py` and `routes/access_request_routes.py`. Python 3.13+ deprecation.
- [ ] **1.3** Resolve the TODO at `routes/dashboard_routes.py:141` — decide: add clock_in/out columns to employees, link via jobs.assigned_employee_id, or delete the TODO and document the non-decision.
- [ ] **1.4** Add `sql/_migrations_run.md` that lists every `.sql` file in date order with a "run on <date> in prod" checkbox. One-time back-fill from memory/git history; going forward, appending to this file becomes part of running a migration.
- [ ] **1.5** Clean up orphaned chat infrastructure from the April 17 removals — grep for `CHAT_SESSION`, `pwa_chat_messages`, `/pwa/api/chat/*` usages. Delete any route handlers or helpers that no tile/tab reaches.

### Phase 2 — Verify recent shipments (manual QA, not code)
- [ ] **2.1** Create one estimate, one WO, one invoice via the unified dashboard form. Confirm each redirects to the correct review page and line items render with qty × rate.
- [ ] **2.2** Same three doc types via the PWA New Job tab. Confirm customer-create path works when the tech picks "new."
- [ ] **2.3** Drag a WO around the Planner board — confirm it stays in backlog after reload (commit `7926829`), even if dragged onto a day column.
- [ ] **2.4** Dispatch the same job twice on the dashboard. Confirm PWA Route shows it once, not twice (commit `18c5599`).
- [ ] **2.5** Tap Send App on a team member card (Dashboard Team tile + Workers page). Confirm Resend actually delivered and the install link opens the PWA on a phone.

### Phase 3 — Unblock ship (inbound SMS + Square production)
- [ ] **3.1** Register Telnyx brand number for inbound (see HANDOFF roadmap). Point webhook at `/sms-receive`. Smoke-test: text STOP and YES, verify `sms_consent` flips in `customers`.
- [ ] **3.2** Cut over `admin.bolts11.com` DNS (CNAME in Cloudflare to the `web-production-5e96f` Railway service per [deploy.md:43](deploy.md#L43)). Verify admin login with PIN.
- [ ] **3.3** Swap Square sandbox credentials for production in Railway env. Update `SQUARE_ENVIRONMENT=production`. Smoke-test by creating one real invoice and verifying the PAY NOW link resolves to a live Square checkout (use a $1 test).
- [ ] **3.4** Confirm `clients.sms_outbound_enabled` is `false` for every row (the kill switch). Keep it false until 10DLC lands.

### Phase 4 — Customer email workflow (the one feature gap that blocks Rule #2)
- [ ] **4.1** Audit every codepath that emails a customer (proposal send, invoice send, follow-up). Confirm each logs `delivery_blocked_no_email` when `customers.customer_email` is missing — this landed for invoices, verify proposals + follow-ups match.
- [ ] **4.2** Build a "Missing Email" needs_attention card on the owner dashboard that lists customers without emails and has a one-click "ask for email" action (generates a review-style link the customer can fill in, or a manual-entry field).
- [ ] **4.3** Smoke test: create a proposal for a customer with no email → confirm the card surfaces → fill in the email → confirm the proposal sends on the retry.

### Phase 5 — Polish and verify guided estimate in the wild
- [ ] **5.1** End-to-end test the guided estimate state machine with a real-feeling job description. Walk from pwa_chat intent through to `/doc/send`. Confirm `job_pricing_history` writes. Confirm no Claude pricing calls fire (per HARD RULE #8).
- [ ] **5.2** If anything in 5.1 is awkward (unclear prompts, broken transitions), file a subtask here and fix in one commit.
- [ ] **5.3** Decide: is the self-learning agent (prompts for NULL pricebook fields) worth including in the first-field-client ship, or parked? Document the answer in the Decisions log below. If parked, add a README note so no one builds it prematurely.

## Test plan
- **Unit / integration:** `pytest --ignore=tests/test_pwa.py` stays green after every commit. `tests/test_pwa.py` becomes green by the end of Phase 1.
- **Manual:** Phase 2 is the manual pass. Phase 3 smoke tests (SMS STOP/YES, real Square $1, admin DNS).
- **Regression watch:** after each commit, eyeball `/dashboard/planner`, `/dashboard/workorders/`, and PWA `/pwa/review` to confirm nothing moved unexpectedly.

## Decisions log
- **2026-04-23:** Chose phase-based structure over single-feature plan because the scope is "iron out," not "add one feature." Phases are independent so we can stop after any of them and still have shipped value.
- **2026-04-23:** Kept Bolts11 on paid Supabase rather than migrating. Future projects default to Neon. Storage stays on Supabase free tier. Coupling audit: ~130 PostgREST query chains across 22 db_*.py files, single chokepoint in `execution/db_connection.py` — migration remains feasible if ever needed.

## Risks
- **Risk:** Recent commits (WO backlog, route dedup, PWA unified form) may have subtle bugs not covered by tests. **Mitigation:** Phase 2 is entirely manual QA of exactly those commits before building more.
- **Risk:** SQL migration state drift between local and prod. **Mitigation:** Subtask 1.4 adds a tracking file; going forward, treat running a migration as a PR.
- **Risk:** Square production swap breaks payment links if sandbox-only code paths exist. **Mitigation:** Smoke-test with a $1 real invoice before telling the first client to use payments.
- **Risk:** Brand number SMS inbound webhook config has an off-by-one path (e.g. webhook points at `/sms` not `/sms-receive`). **Mitigation:** Phase 3 Subtask 3.1 has an explicit STOP/YES smoke test.
- **Risk:** We discover that `sms_outbound_enabled` was accidentally set `true` somewhere. **Mitigation:** Subtask 3.4 explicitly verifies the kill switch.
