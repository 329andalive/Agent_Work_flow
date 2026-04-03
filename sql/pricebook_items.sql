-- pricebook_items — Per-client service catalog with 3-tier pricing
--
-- This is the central pricing table. Every client gets their own rows,
-- seeded from the vertical template on onboarding, then customized.
-- Proposal and invoice agents read from this table instead of hardcoded
-- system prompts. Price adjustments feed back here via the learning loop.

CREATE TABLE IF NOT EXISTS pricebook_items (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id         uuid NOT NULL REFERENCES clients(id),
    job_code          text,                                        -- short code (e.g. SPT-001), auto-generated
    job_name          text NOT NULL,                               -- customer-facing name
    description       text,                                        -- longer description for estimates/invoices
    category          text,                                        -- grouping for reports (e.g. pumping, repair, inspection)
    price_low         numeric(10,2),                               -- economy tier
    price_mid         numeric(10,2),                               -- standard tier (default)
    price_high        numeric(10,2),                               -- premium tier
    labor_hours_est   numeric(5,2),                                -- nullable, learned over time
    material_cost     numeric(10,2),                               -- nullable, learned over time
    markup_pct        numeric(5,2),                                -- nullable, applied to materials
    unit_of_measure   text DEFAULT 'per job',                      -- per job / per gallon / per ft / per hour
    tax_code          text,                                        -- nullable
    is_active         boolean DEFAULT true,                        -- soft delete
    sort_order        integer DEFAULT 0,
    vertical_key      text,                                        -- trade vertical this item belongs to
    source            text DEFAULT 'template',                     -- template / onboarding / manual / csv_import
    confidence        numeric(3,2) DEFAULT 0.0,                    -- 0.0-1.0, for self-learning (Phase 2)
    times_used        integer DEFAULT 0,                           -- how many jobs used this item
    last_used_at      timestamptz,
    created_at        timestamptz DEFAULT now(),
    updated_at        timestamptz DEFAULT now()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_pricebook_client_active
    ON pricebook_items (client_id, is_active)
    WHERE is_active = true;

CREATE INDEX IF NOT EXISTS idx_pricebook_client_vertical
    ON pricebook_items (client_id, vertical_key)
    WHERE is_active = true;

CREATE INDEX IF NOT EXISTS idx_pricebook_job_code
    ON pricebook_items (client_id, job_code)
    WHERE job_code IS NOT NULL;

-- Unique constraint: one job_name per client (prevents duplicate services)
CREATE UNIQUE INDEX IF NOT EXISTS idx_pricebook_client_jobname
    ON pricebook_items (client_id, lower(job_name))
    WHERE is_active = true;
