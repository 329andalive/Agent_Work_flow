-- =============================================================================
-- Migration 001 — Job costing columns + job_costs table
--
-- Run this in the Supabase SQL editor AFTER running supabase_schema.sql.
-- All statements are safe to re-run (IF NOT EXISTS / IF NOT EXISTS column).
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Add job costing columns to the jobs table
-- -----------------------------------------------------------------------------
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS
    estimated_hours  numeric(5,2);

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS
    actual_hours     numeric(5,2);

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS
    contract_type    text default 'time_and_materials';
    -- values: time_and_materials, fixed_price

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS
    estimated_amount numeric(10,2);
    -- pulled from proposal at job start

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS
    actual_amount    numeric(10,2);
    -- what was actually invoiced


-- -----------------------------------------------------------------------------
-- job_costs — stores the costing breakdown for every completed job
-- The owner uses this to understand whether they won or lost on each job.
-- Written by job_cost_agent after every invoice is generated.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS job_costs (
    id                uuid primary key default gen_random_uuid(),
    job_id            uuid references jobs(id),
    client_id         uuid references clients(id),
    contract_type     text,
    estimated_hours   numeric(5,2),
    actual_hours      numeric(5,2),
    hour_variance     numeric(5,2),      -- actual minus estimated (positive = ran over)
    estimated_amount  numeric(10,2),
    actual_amount     numeric(10,2),
    amount_variance   numeric(10,2),     -- actual minus estimated (positive = more revenue)
    labor_cost        numeric(10,2),     -- actual_hours x hourly_rate
    job_margin        numeric(10,2),     -- actual_amount minus labor_cost
    result            text,             -- won, lost, break_even
    summary_line      text,             -- one-line plain English for owner
    created_at        timestamptz default now()
);
