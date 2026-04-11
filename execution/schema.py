"""
schema.py — Single source of truth for Supabase table + column names.

WHY THIS FILE EXISTS
--------------------
Every column name in this codebase used to be a magic string. Different
agents had different mental models of the same table — proposal_agent
wrote `customer_address`, the chat agent template asked for `address`,
test fixtures used `address` again, and the bug only surfaced in
production logs when a customer email landed empty. We've shipped fixes
for `address` vs `customer_address`, `employee_id` vs `tech_id` on
`pwa_tokens`, and a `subtotal` column that doesn't exist on `proposals`
at all. The disease is the same in every case: schema is implicit, and
each module's mental model drifts.

This file is the antidote. Every table we touch has a class. Every
column on that table is a class attribute holding the literal column
name. New code that touches the database imports from here:

    from execution.schema import Customers as C
    sb.table(C.TABLE).select("*").eq(C.CLIENT_ID, client_id).execute()

If a typo creeps in (`C.CLINT_ID`), Python raises AttributeError at
import time — not at runtime against Supabase. If a column gets renamed
in the database, you rename it here and grep tells you every site that
needs updating.

WHY NOT SQLModel / SQLAlchemy / an ORM
--------------------------------------
We're on Supabase. The whole platform speaks PostgREST through the
official `supabase-py` client and we get RLS, connection pooling, auth,
realtime, and storage for free. Bolting an ORM on top means
maintaining two parallel client paths (guaranteed to drift) or fighting
the whole platform. The bug we actually have is "the column name is
wrong," not "we lack object lifecycle management." Constants solve the
real bug at zero migration cost.

WHY NOT JUST READ THE SUPABASE DASHBOARD
----------------------------------------
Because the LLM you're talking to right now can't read the Supabase
dashboard. It can read this file. Every new Claude Code session that
opens this codebase reads CLAUDE.md → CONVENTIONS.md → this file, and
gets the right column names without having to guess from grep results.

CONVENTIONS
-----------
- TABLE = "<table_name>" lives at the top of every class.
- Column attributes are SHOUTY_SNAKE_CASE on the class, mapping to the
  literal lowercase_snake_case the database uses.
- Foreign keys follow the rule {target_table_singular}_id — so
  `customer_id`, `job_id`, `client_id` — never `customer` or `cust`.
- Timestamps are `_at` suffixed and past tense — `created_at`,
  `sent_at`, `accepted_at`, `paid_at`.
- Boolean flags are `sms_consent`, `is_paid`, etc.
- Comments next to a constant document KNOWN GOTCHAS — e.g. columns
  that DON'T exist on a sibling table, or names that have been
  mistakenly used in the past.

CHANGING THIS FILE
------------------
Adding a column you've verified exists: just add it. The class is
intentionally permissive — we'd rather have an entry that turns out
to be unused than miss one that drifts. Adding a comment explaining
why a column is or isn't present is encouraged.

Renaming a column: rename here first, then grep for the old literal
string and update every site. The schema file gives us a stable
identifier (the class attribute name) so the rename is safe.

Removing a column you discovered doesn't exist: delete it here AND
add a guard test in tests/test_schema.py asserting the dead name is
not present, so it can never be reintroduced silently.
"""


# ---------------------------------------------------------------------------
# clients — multi-tenant root. Every other table FKs to clients.id.
# ---------------------------------------------------------------------------

class Clients:
    TABLE = "clients"

    ID                    = "id"
    BUSINESS_NAME         = "business_name"
    OWNER_NAME            = "owner_name"
    PHONE                 = "phone"           # Telnyx brand number for the business
    OWNER_MOBILE          = "owner_mobile"    # Owner's personal cell — different from Telnyx number
    PERSONALITY           = "personality"     # Free-form Markdown personality layer
    TRADE_VERTICAL        = "trade_vertical"  # See vertical_loader._normalize_vertical_key for accepted values
    SMS_OUTBOUND_ENABLED  = "sms_outbound_enabled"   # Layer 1 kill switch — false by default per HARD RULE #7
    EMAIL_OUTBOUND_ENABLED = "email_outbound_enabled"
    TIMEZONE              = "timezone"        # IANA tz, e.g. "America/New_York"
    CREATED_AT            = "created_at"


# ---------------------------------------------------------------------------
# customers — every customer belongs to exactly one client (multi-tenant).
# ---------------------------------------------------------------------------

class Customers:
    TABLE = "customers"

    ID                = "id"
    CLIENT_ID         = "client_id"
    CUSTOMER_NAME     = "customer_name"     # NOT "name"
    CUSTOMER_PHONE    = "customer_phone"    # NOT "phone" — HARD RULE #1: required, never null
    CUSTOMER_EMAIL    = "customer_email"    # NOT "email"
    CUSTOMER_ADDRESS  = "customer_address"  # NOT "address" — caused the chat agent fields-bug
    SMS_CONSENT       = "sms_consent"       # bool — CTIA compliance gate for outbound SMS
    SMS_CONSENT_AT    = "sms_consent_at"    # timestamptz when consent was granted
    SMS_CONSENT_SRC   = "sms_consent_src"   # "owner_command" | "web_form" | "sms_yes"
    PROPERTY_NOTES    = "property_notes"    # free-form notes about the property/site
    LAST_CONTACT      = "last_contact"
    CREATED_AT        = "created_at"


# ---------------------------------------------------------------------------
# employees — workers belonging to a client (techs, foremen, office, owner).
# ---------------------------------------------------------------------------

class Employees:
    TABLE = "employees"

    ID         = "id"
    CLIENT_ID  = "client_id"
    NAME       = "name"           # full name
    PHONE      = "phone"          # E.164
    EMAIL      = "email"          # nullable, used for /pwa/login magic link delivery
    ROLE       = "role"           # "owner" | "foreman" | "field_tech" | "office"
    ACTIVE     = "active"         # bool
    SMS_OPTED_OUT = "sms_opted_out"
    CREATED_AT = "created_at"


# ---------------------------------------------------------------------------
# jobs — the central work record. Wide table, lots of columns.
# ---------------------------------------------------------------------------

class Jobs:
    TABLE = "jobs"

    ID                  = "id"
    CLIENT_ID           = "client_id"
    CUSTOMER_ID         = "customer_id"
    JOB_TYPE            = "job_type"
    JOB_DESCRIPTION     = "job_description"
    JOB_NOTES           = "job_notes"        # internal scope notes — see render-time $-line filter for customer view
    RAW_INPUT           = "raw_input"        # original tech text before AI cleanup
    STATUS              = "status"           # "new" | "estimated" | "scheduled" | "in_progress" | "complete" | "lost"
    DISPATCH_STATUS     = "dispatch_status"  # "unassigned" | "assigned" | "completed" | "carry_forward" | "no_show" | "scope_review"
    SCHEDULED_DATE      = "scheduled_date"
    REQUESTED_TIME      = "requested_time"
    JOB_START           = "job_start"        # timestamptz when tech tapped Start
    JOB_END             = "job_end"          # timestamptz when tech tapped Done
    ASSIGNED_WORKER_ID  = "assigned_worker_id"
    SORT_ORDER          = "sort_order"
    ZONE_CLUSTER        = "zone_cluster"
    ESTIMATED_AMOUNT    = "estimated_amount"
    ESTIMATED_HOURS     = "estimated_hours"
    ACTUAL_AMOUNT       = "actual_amount"
    ACTUAL_HOURS        = "actual_hours"
    SOURCE_PROPOSAL_ID  = "source_proposal_id"
    SCOPE_HOLD          = "scope_hold"       # bool — flagged by tech mid-job, blocks auto-invoice
    COMPLETED_DATE      = "completed_date"
    GEO_LAT             = "geo_lat"
    GEO_LNG             = "geo_lng"
    CREATED_AT          = "created_at"


# ---------------------------------------------------------------------------
# proposals — drafts of estimates. Status='draft' until /doc/send.
#
# GOTCHA: this table does NOT have subtotal/tax_rate/tax_amount columns.
# Those columns DO exist on the invoices table but NOT here. Writing
# them via supabase.table("proposals").update({"subtotal": ...}) raises
# a PostgREST error "Could not find the 'subtotal' column of 'proposals'".
# update_proposal_fields() in db_document.py keeps them in its signature
# for backwards compat with /doc/save callers, but silently drops them
# from the actual update dict.
# ---------------------------------------------------------------------------

class Proposals:
    TABLE = "proposals"

    ID                  = "id"
    CLIENT_ID           = "client_id"
    CUSTOMER_ID         = "customer_id"
    JOB_ID              = "job_id"
    PROPOSAL_TEXT       = "proposal_text"     # customer-visible scope notes — render-time $-filter strips price lines
    AMOUNT_ESTIMATE     = "amount_estimate"   # the canonical money column on this table
    LINE_ITEMS          = "line_items"        # jsonb [{description, amount, total, taxable}]
    STATUS              = "status"            # "draft" | "sent" | "accepted" | "declined"
    SENT_AT             = "sent_at"           # null until /doc/send fires — used as belt-and-suspenders cron filter
    EDIT_TOKEN          = "edit_token"        # uuid, default gen_random_uuid() — owner edit URL token
    HTML_URL            = "html_url"          # legacy storage URL; customers now hit /p/<token> instead
    RESPONSE_TYPE       = "response_type"     # null | "accepted" | "declined" | "cold"
    ACCEPTED_AT         = "accepted_at"
    RESPONDED_AT        = "responded_at"
    LOST_REASON         = "lost_reason"       # "price" | "timing" | "competitor" | "relationship" | "unknown"
    LOST_REASON_DETAIL  = "lost_reason_detail"
    CREATED_AT          = "created_at"


# ---------------------------------------------------------------------------
# invoices — billed work. Mirrors proposals but has tax columns.
# ---------------------------------------------------------------------------

class Invoices:
    TABLE = "invoices"

    ID            = "id"
    CLIENT_ID     = "client_id"
    CUSTOMER_ID   = "customer_id"
    JOB_ID        = "job_id"
    INVOICE_TEXT  = "invoice_text"   # customer-visible scope notes
    AMOUNT_DUE    = "amount_due"     # the canonical money column on this table
    LINE_ITEMS    = "line_items"     # jsonb [{description, amount, total, taxable}]
    SUBTOTAL      = "subtotal"       # exists on invoices but NOT on proposals (verified asymmetry)
    TAX_RATE      = "tax_rate"       # exists on invoices but NOT on proposals
    TAX_AMOUNT    = "tax_amount"     # exists on invoices but NOT on proposals
    STATUS        = "status"         # "draft" | "sent" | "paid" | "overdue"
    SENT_AT       = "sent_at"
    PAID_AT       = "paid_at"
    EDIT_TOKEN    = "edit_token"
    HTML_URL      = "html_url"
    SCOPE_HOLD    = "scope_hold"
    CREATED_AT    = "created_at"


# ---------------------------------------------------------------------------
# pwa_tokens — magic-link auth tokens for the PWA.
#
# GOTCHA: the worker reference column is `tech_id`, NOT `employee_id`.
# We've been bitten by this exactly once. The Python session key for the
# logged-in user IS still `session["employee_id"]` — that's a Flask
# session key, not a DB column — but inserts/lookups against this table
# must use TECH_ID.
# ---------------------------------------------------------------------------

class PwaTokens:
    TABLE = "pwa_tokens"

    ID              = "id"
    TOKEN           = "token"          # 8-char alphanumeric
    CLIENT_ID       = "client_id"
    TECH_ID         = "tech_id"        # NOT "employee_id" — DO NOT use that name on this table
    EMPLOYEE_PHONE  = "employee_phone"  # cached for fallback resolution
    PURPOSE         = "purpose"        # "pwa_login" today; reserved for future scopes
    EXPIRES_AT      = "expires_at"
    CONSUMED_AT     = "consumed_at"    # null until first use; one-shot enforcement
    CONSUMED_IP     = "consumed_ip"
    USER_AGENT      = "user_agent"
    CREATED_AT      = "created_at"


# ---------------------------------------------------------------------------
# pwa_chat_messages — persistent chat history for /pwa/chat.
# ---------------------------------------------------------------------------

class PwaChatMessages:
    TABLE = "pwa_chat_messages"

    ID           = "id"
    CLIENT_ID    = "client_id"
    EMPLOYEE_ID  = "employee_id"   # NOTE: this column IS named employee_id here, not tech_id (asymmetry with pwa_tokens)
    SESSION_ID   = "session_id"
    ROLE         = "role"          # "user" | "assistant"
    CONTENT      = "content"
    METADATA     = "metadata"      # jsonb — currently {model, action?}
    CREATED_AT   = "created_at"


# ---------------------------------------------------------------------------
# time_entries — clock punch records.
# ---------------------------------------------------------------------------

class TimeEntries:
    TABLE = "time_entries"

    ID                = "id"
    CLIENT_ID         = "client_id"
    EMPLOYEE_ID       = "employee_id"
    SCHEDULE_ID       = "schedule_id"
    JOB_ID            = "job_id"
    CURRENT_JOB_ID    = "current_job_id"
    CLOCK_IN          = "clock_in"           # NOTE: not _at suffixed — legacy from pre-convention era
    CLOCK_OUT         = "clock_out"
    DURATION_MINUTES  = "duration_minutes"
    STATUS            = "status"             # "open" | "closed"


# ---------------------------------------------------------------------------
# route_assignments — joins workers to jobs for a given dispatch_date.
#
# GOTCHA: column is `worker_id`, NOT `employee_id` or `tech_id`. The
# dispatch domain uses "worker" terminology consistently.
# ---------------------------------------------------------------------------

class RouteAssignments:
    TABLE = "route_assignments"

    ID            = "id"
    CLIENT_ID     = "client_id"
    JOB_ID        = "job_id"
    WORKER_ID     = "worker_id"      # NOT "employee_id" — dispatch_chain uses worker_id
    DISPATCH_DATE = "dispatch_date"  # date column — NOT scheduled_date (that's on jobs)
    SORT_ORDER    = "sort_order"
    WAVE_ID       = "wave_id"
    STATUS        = "status"
    ASSIGNED_AT   = "assigned_at"


# ---------------------------------------------------------------------------
# route_tokens — public token URLs for the worker_route.html fallback page.
# ---------------------------------------------------------------------------

class RouteTokens:
    TABLE = "route_tokens"

    TOKEN          = "token"
    CLIENT_ID      = "client_id"
    WORKER_ID      = "worker_id"
    SESSION_ID     = "session_id"
    DISPATCH_DATE  = "dispatch_date"
    EXPIRES_AT     = "expires_at"
    VIEWED_AT      = "viewed_at"


# ---------------------------------------------------------------------------
# dispatch_decisions — AI learning loop record for the dispatch board.
# ---------------------------------------------------------------------------

class DispatchDecisions:
    TABLE = "dispatch_decisions"

    ID              = "id"
    CLIENT_ID       = "client_id"
    SESSION_ID      = "session_id"
    DISPATCH_DATE   = "dispatch_date"
    JOB_ID          = "job_id"
    WORKER_ID       = "worker_id"
    JOB_TYPE        = "job_type"
    ZONE_CLUSTER    = "zone_cluster"
    REQUESTED_TIME  = "requested_time"
    SORT_ORDER      = "sort_order"
    WAS_SUGGESTED   = "was_suggested"
    WAS_ACCEPTED    = "was_accepted"
    WAS_OVERRIDDEN  = "was_overridden"
    OVERRIDE_REASON = "override_reason"
    OUTCOME_STATUS  = "outcome_status"   # populated by /pwa/api/job/<id>/done
    OUTCOME_AT      = "outcome_at"


# ---------------------------------------------------------------------------
# follow_ups — scheduled customer follow-up records (cron-driven).
#
# GOTCHA: the type column is `follow_up_type`, NOT `followup_type`.
# Both spellings are tempting; this is the one in the DB.
# ---------------------------------------------------------------------------

class FollowUps:
    TABLE = "follow_ups"

    ID              = "id"
    CLIENT_ID       = "client_id"
    CUSTOMER_ID     = "customer_id"
    JOB_ID          = "job_id"
    PROPOSAL_ID     = "proposal_id"
    FOLLOW_UP_TYPE  = "follow_up_type"  # "estimate_followup" | "payment_chase" | "seasonal_reminder" | "pending_consent" | "lost_job_why"
    STATUS          = "status"          # "pending" | "sent" | "cancelled"
    SCHEDULED_FOR   = "scheduled_for"
    SENT_AT         = "sent_at"
    MESSAGE_SENT    = "message_sent"    # text body that actually went out
    CREATED_AT      = "created_at"


# ---------------------------------------------------------------------------
# agent_activity — append-only audit log for every agent action.
#
# GOTCHA: tenant column is `client_phone`, NOT `client_id`. This table
# pre-dates the multi-tenant ID refactor and was never migrated.
# ---------------------------------------------------------------------------

class AgentActivity:
    TABLE = "agent_activity"

    ID              = "id"
    CLIENT_PHONE    = "client_phone"     # NOT client_id — legacy column on this table
    AGENT_NAME      = "agent_name"
    ACTION_TAKEN    = "action_taken"
    INPUT_SUMMARY   = "input_summary"
    OUTPUT_SUMMARY  = "output_summary"
    SMS_SENT        = "sms_sent"
    CREATED_AT      = "created_at"


# ---------------------------------------------------------------------------
# needs_attention — owner inbox cards for things the AI couldn't auto-resolve.
#
# Same legacy quirk as agent_activity: tenant is `client_phone`, not id.
# ---------------------------------------------------------------------------

class NeedsAttention:
    TABLE = "needs_attention"

    ID                 = "id"
    CLIENT_PHONE       = "client_phone"
    CARD_TYPE          = "card_type"          # "delivery_blocked_no_email" | "scope_hold" | etc.
    PRIORITY           = "priority"           # "low" | "medium" | "high"
    RELATED_RECORD     = "related_record"
    RAW_CONTEXT        = "raw_context"
    CLAUDE_SUGGESTION  = "claude_suggestion"
    STATUS             = "status"             # "open" | "resolved" | "dismissed"
    RESOLVED_BY        = "resolved_by"
    RESOLVED_AT        = "resolved_at"
    CREATED_AT         = "created_at"


# ---------------------------------------------------------------------------
# estimate_edits — diff log for the document edit/learn loop.
# ---------------------------------------------------------------------------

class EstimateEdits:
    TABLE = "estimate_edits"

    ID              = "id"
    CLIENT_ID       = "client_id"
    DOCUMENT_TYPE   = "document_type"   # "proposal" | "invoice"
    DOCUMENT_ID     = "document_id"
    FIELD_CHANGED   = "field_changed"   # "line_items" | "notes" | "total"
    ORIGINAL_VALUE  = "original_value"
    NEW_VALUE       = "new_value"
    CREATED_AT      = "created_at"


# ---------------------------------------------------------------------------
# client_prompt_overrides — learned style guidance for the proposal/invoice
# Claude prompts. One row per client.
# ---------------------------------------------------------------------------

class ClientPromptOverrides:
    TABLE = "client_prompt_overrides"

    CLIENT_ID            = "client_id"
    ESTIMATE_STYLE_NOTES = "estimate_style_notes"
    INVOICE_STYLE_NOTES  = "invoice_style_notes"
    UPDATED_AT           = "updated_at"


# ---------------------------------------------------------------------------
# estimate_sessions — in-progress guided estimate conversations.
# One row per active estimate. Status drives the state machine in
# execution/guided_estimate.py.
#
# GOTCHA: session_id here is the pwa_chat_messages.session_id UUID that
# links this estimate to the chat conversation. It is NOT this table's
# primary key (id). Don't confuse them.
# ---------------------------------------------------------------------------

class EstimateSessions:
    TABLE = "estimate_sessions"

    ID                 = "id"
    CLIENT_ID          = "client_id"
    EMPLOYEE_ID        = "employee_id"
    SESSION_ID         = "session_id"         # links to pwa_chat_messages.session_id
    STATUS             = "status"             # gathering | confirming_customer | awaiting_price | awaiting_line_items | review | done | cancelled
    CUSTOMER_ID        = "customer_id"
    CUSTOMER_CONFIRMED = "customer_confirmed"
    JOB_TYPE           = "job_type"
    JOB_TYPE_CONFIRMED = "job_type_confirmed"
    PRIMARY_PRICE      = "primary_price"      # tech-entered — NEVER AI-generated
    LINE_ITEMS         = "line_items"         # jsonb [{description, amount}]
    NOTES              = "notes"
    CURRENT_STEP       = "current_step"       # last question asked — used to resume mid-flow
    CREATED_AT         = "created_at"
    UPDATED_AT         = "updated_at"


# ---------------------------------------------------------------------------
# job_pricing_history — one row per sent proposal. Powers the "last 3
# averaged $X" reference shown to the tech during guided estimate flow.
# Written by /doc/send ONLY — never by an AI agent.
# ---------------------------------------------------------------------------

class JobPricingHistory:
    TABLE = "job_pricing_history"

    ID           = "id"
    CLIENT_ID    = "client_id"
    CUSTOMER_ID  = "customer_id"
    JOB_ID       = "job_id"
    PROPOSAL_ID  = "proposal_id"
    JOB_TYPE     = "job_type"
    DESCRIPTION  = "description"
    AMOUNT       = "amount"       # tech-entered final price — never AI-generated
    EMPLOYEE_ID  = "employee_id"
    COMPLETED_AT = "completed_at"
