-- guided_estimate_tables.sql
-- Run in Supabase SQL editor. Creates two new tables for the guided
-- estimate flow. Safe to re-run — both use CREATE TABLE IF NOT EXISTS.
--
-- Tables:
--   estimate_sessions   — in-progress guided estimate conversations
--   job_pricing_history — completed proposal pricing for the "last 3 averaged $X" reference

-- ---------------------------------------------------------------------------
-- estimate_sessions
-- Tracks the state of a guided estimate conversation turn-by-turn.
-- One row per in-progress estimate. Status moves: gathering → review → done.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.estimate_sessions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id           uuid REFERENCES public.clients(id) NOT NULL,
    employee_id         uuid REFERENCES public.employees(id) NOT NULL,
    session_id          uuid NOT NULL,                      -- links to pwa_chat_messages.session_id
    status              text NOT NULL DEFAULT 'gathering',  -- gathering | confirming_customer | awaiting_price | awaiting_line_items | review | done | cancelled
    customer_id         uuid REFERENCES public.customers(id),
    customer_confirmed  boolean NOT NULL DEFAULT false,
    job_type            text,                               -- pump_out | baffle_replacement | etc.
    job_type_confirmed  boolean NOT NULL DEFAULT false,
    primary_price       numeric(10,2),                      -- tech-entered primary job price
    line_items          jsonb NOT NULL DEFAULT '[]',        -- [{description, amount}]
    notes               text,
    current_step        text,                               -- which question we just asked
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- One active session per employee per chat session
CREATE INDEX IF NOT EXISTS idx_estimate_sessions_employee_session
    ON public.estimate_sessions(employee_id, session_id);

-- Fast tenant-scoped lookup
CREATE INDEX IF NOT EXISTS idx_estimate_sessions_client
    ON public.estimate_sessions(client_id, status, updated_at DESC);

-- ---------------------------------------------------------------------------
-- job_pricing_history
-- Every sent proposal becomes a pricing data point.
-- Powers the "last 3 pump outs averaged $285" reference in the guided flow.
-- Written by /doc/send after the tech approves and sends an estimate.
-- NEVER written by the AI — only by completed real-world jobs.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.job_pricing_history (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       uuid REFERENCES public.clients(id) NOT NULL,
    customer_id     uuid REFERENCES public.customers(id),
    job_id          uuid REFERENCES public.jobs(id),
    proposal_id     uuid REFERENCES public.proposals(id),
    job_type        text NOT NULL,
    description     text,
    amount          numeric(10,2) NOT NULL,
    employee_id     uuid REFERENCES public.employees(id),
    completed_at    timestamptz NOT NULL DEFAULT now()
);

-- Primary lookup: recent history for a client + job type (for shop average)
CREATE INDEX IF NOT EXISTS idx_pricing_history_client_job
    ON public.job_pricing_history(client_id, job_type, completed_at DESC);

-- Secondary: history for a specific customer + job type (for "last 3 for William")
CREATE INDEX IF NOT EXISTS idx_pricing_history_customer_job
    ON public.job_pricing_history(client_id, customer_id, job_type, completed_at DESC);
