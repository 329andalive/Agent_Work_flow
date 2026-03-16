-- =============================================================================
-- Migration 002 — Follow-up agent: proposal tracking, lost jobs, outcomes
--
-- Run this in the Supabase SQL editor AFTER migration 001.
-- All statements are safe to re-run (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Add response tracking columns to the proposals table
-- Tracks when and how a customer responded to a proposal.
-- -----------------------------------------------------------------------------
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS
    responded_at timestamptz;

ALTER TABLE proposals ADD COLUMN IF NOT EXISTS
    response_type text;
    -- values: accepted, declined, cold (no response after 14 days)

ALTER TABLE proposals ADD COLUMN IF NOT EXISTS
    lost_reason text;
    -- values: price, timing, competition, relationship, unknown

ALTER TABLE proposals ADD COLUMN IF NOT EXISTS
    lost_reason_detail text;
    -- free-text detail from owner, e.g. "neighbor does septic work"


-- -----------------------------------------------------------------------------
-- lost_jobs — records every job that wasn't won
-- Populated when a proposal is declined or marked cold.
-- Used for pattern analysis: why do we keep losing on price?
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lost_jobs (
    id                   uuid primary key default gen_random_uuid(),
    client_id            uuid references clients(id),
    customer_id          uuid references customers(id),
    job_id               uuid references jobs(id),
    proposal_id          uuid references proposals(id),
    lost_reason          text,           -- price, timing, competition, relationship, unknown
    lost_reason_detail   text,           -- owner's own words
    competitor_mentioned text,           -- any competitor name if mentioned
    proposal_amount      numeric(10,2),  -- what we quoted
    created_at           timestamptz default now()
);


-- -----------------------------------------------------------------------------
-- proposal_outcomes — monthly closing rate tracker
-- One row per client per month. Updated after every proposal status change.
-- Powers the "March summary: won 8 of 12 quotes" SMS report.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS proposal_outcomes (
    id                  uuid primary key default gen_random_uuid(),
    client_id           uuid references clients(id),
    month               text,             -- format: 2026-03
    proposals_sent      integer default 0,
    proposals_accepted  integer default 0,
    proposals_declined  integer default 0,
    proposals_cold      integer default 0,
    revenue_won         numeric(10,2) default 0,
    revenue_lost        numeric(10,2) default 0,
    top_lost_reason     text,
    created_at          timestamptz default now(),
    updated_at          timestamptz default now()
);

-- Unique constraint: one row per client per month
CREATE UNIQUE INDEX IF NOT EXISTS proposal_outcomes_client_month
    ON proposal_outcomes(client_id, month);
