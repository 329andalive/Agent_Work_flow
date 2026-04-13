-- job_log_tables.sql
-- Daily job logging for multi-day work orders.
-- Records crew presence, equipment on site, and material deliveries
-- per job per day. Feeds the bid vs actual cost report.
--
-- Run order: this file only. No dependencies beyond existing jobs + employees tables.
-- Multi-tenant: every table has client_id. RLS policy should mirror pricebook_items.

-- ---------------------------------------------------------------------------
-- 1. job_log_sessions — state machine for the daily log chat flow
--    One row per foreman per job per log_date.
--    Mirrors the estimate_sessions pattern exactly.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS job_log_sessions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    employee_id     uuid NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    session_id      uuid NOT NULL,          -- links to pwa_chat_messages.session_id
    job_id          uuid REFERENCES jobs(id) ON DELETE SET NULL,
    log_date        date NOT NULL DEFAULT CURRENT_DATE,
    status          text NOT NULL DEFAULT 'open',
                    -- open | crew_confirmed | equipment_confirmed | materials_done | closed | abandoned
    current_step    text NOT NULL DEFAULT 'select_job',
                    -- missed_log_check | select_job | confirm_crew |
                    -- confirm_equipment | log_materials | day_close
    crew_confirmed      boolean NOT NULL DEFAULT false,
    equipment_confirmed boolean NOT NULL DEFAULT false,
    notes           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_job_log_sessions_employee
    ON job_log_sessions (client_id, employee_id, status, log_date DESC);

CREATE INDEX IF NOT EXISTS idx_job_log_sessions_session
    ON job_log_sessions (session_id);

COMMENT ON TABLE job_log_sessions IS
    'State machine for the daily job log chat flow. One row per foreman per job per day.';
COMMENT ON COLUMN job_log_sessions.session_id IS
    'Links to pwa_chat_messages.session_id — NOT this table''s PK.';
COMMENT ON COLUMN job_log_sessions.log_date IS
    'Date column (not timestamp) so backdating missed logs works cleanly.';


-- ---------------------------------------------------------------------------
-- 2. job_crew_log — who was present on a job on a given date
--    One row per employee per job per day.
--    An employee can appear on multiple jobs the same day (dispatched to two sites).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS job_crew_log (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id   uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    job_id      uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    employee_id uuid NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    log_date    date NOT NULL,
    logged_by   uuid REFERENCES employees(id) ON DELETE SET NULL,
                -- foreman who recorded this entry (may differ from employee_id)
    notes       text,
    billed      boolean NOT NULL DEFAULT false,
                -- true once this day has been included in an invoice
    created_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_crew_log UNIQUE (client_id, job_id, employee_id, log_date)
    -- prevents double-logging same person on same job same day
);

CREATE INDEX IF NOT EXISTS idx_job_crew_log_job
    ON job_crew_log (client_id, job_id, log_date DESC);

CREATE INDEX IF NOT EXISTS idx_job_crew_log_unbilled
    ON job_crew_log (client_id, job_id, billed)
    WHERE billed = false;

COMMENT ON TABLE job_crew_log IS
    'One row per employee per job per day. Presence only for MVP — hours added later.';
COMMENT ON COLUMN job_crew_log.billed IS
    'Set true when this crew-day is included in a sent invoice. Prevents double-billing.';


-- ---------------------------------------------------------------------------
-- 3. job_equipment_log — equipment on site per day
--    Presence only for MVP. equipment_name is free text typed by the foreman.
--    The system fuzzy-matches prior entries for the same job to surface
--    the "same as yesterday?" prompt.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS job_equipment_log (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    job_id          uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    logged_by       uuid REFERENCES employees(id) ON DELETE SET NULL,
    equipment_name  text NOT NULL,          -- "8 ton excavator", "5 ton track loader"
    log_date        date NOT NULL,
    notes           text,
    billed          boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_job_equipment_log_job
    ON job_equipment_log (client_id, job_id, log_date DESC);

CREATE INDEX IF NOT EXISTS idx_job_equipment_log_unbilled
    ON job_equipment_log (client_id, job_id, billed)
    WHERE billed = false;

COMMENT ON TABLE job_equipment_log IS
    'Equipment presence per job per day. Free-text name, presence only for MVP.';


-- ---------------------------------------------------------------------------
-- 4. job_material_log — materials received or consumed
--    Quantity + unit is the core data. Supplier is optional free text for MVP.
--    billable flag distinguishes consumables (rags, fuel) from billed materials.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS job_material_log (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    job_id          uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    logged_by       uuid REFERENCES employees(id) ON DELETE SET NULL,
    material_name   text NOT NULL,          -- "3/4 crushed gravel", "4 inch perf pipe"
    quantity        numeric(10, 2) NOT NULL,
    unit            text NOT NULL,          -- "yards", "tons", "feet", "each", "lf"
    supplier        text,                   -- "Poulin Grain" — nullable, free text for MVP
    log_date        date NOT NULL,
    billable        boolean NOT NULL DEFAULT true,
                    -- false for consumables not billed to customer (fuel, rags, etc.)
    billed          boolean NOT NULL DEFAULT false,
                    -- true once included in a sent invoice
    notes           text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_job_material_log_job
    ON job_material_log (client_id, job_id, log_date DESC);

CREATE INDEX IF NOT EXISTS idx_job_material_log_unbilled
    ON job_material_log (client_id, job_id, billed, billable)
    WHERE billed = false AND billable = true;

COMMENT ON TABLE job_material_log IS
    'Materials received or consumed per job per day. Core billing data.';
COMMENT ON COLUMN job_material_log.billable IS
    'False for consumables the company absorbs (fuel, etc.). True = show on invoice.';
COMMENT ON COLUMN job_material_log.billed IS
    'Set true when included in a sent invoice. Prevents double-billing on partial invoices.';
