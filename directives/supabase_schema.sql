-- =============================================================================
-- Trades AI — Supabase Schema
-- Vertical: Sewer & Drain / Septic
--
-- Run this entire file in the Supabase SQL editor to create all tables.
-- Order matters — referenced tables must exist before foreign keys.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- clients
-- The business owner. One client = one trades business using the system.
-- Stores their identity and the Personality Layer the AI uses to speak as them.
-- -----------------------------------------------------------------------------
create table if not exists clients (
    id               uuid primary key default gen_random_uuid(),
    business_name    text not null,
    owner_name       text not null,
    phone            text not null unique,
    service_area     text,
    trade_vertical   text default 'sewer_drain',
    personality      text,           -- full personality layer document
    active           boolean default true,
    created_at       timestamptz default now()
);


-- -----------------------------------------------------------------------------
-- customers
-- The septic company's customers. One client has many customers.
-- Stores property notes like tank size, last pump date, access info.
-- -----------------------------------------------------------------------------
create table if not exists customers (
    id                uuid primary key default gen_random_uuid(),
    client_id         uuid references clients(id),
    customer_name     text not null,
    customer_phone    text,
    customer_email    text,
    customer_address  text,
    property_notes    text,           -- tank size, last pump date, access notes
    created_at        timestamptz default now()
);


-- -----------------------------------------------------------------------------
-- jobs
-- Core work order record. Everything ties back to a job.
-- Created when an owner texts in a request. Status tracks the full lifecycle.
-- -----------------------------------------------------------------------------
create table if not exists jobs (
    id               uuid primary key default gen_random_uuid(),
    client_id        uuid references clients(id),
    customer_id      uuid references customers(id),
    job_type         text,            -- pump, inspect, repair, emergency, install
    job_description  text,            -- human-readable summary
    status           text default 'new', -- new, estimated, scheduled, complete, invoiced, paid
    raw_input        text,            -- exactly what the owner texted in
    scheduled_date   date,
    completed_date   date,
    job_notes        text,
    created_at       timestamptz default now()
);


-- -----------------------------------------------------------------------------
-- proposals
-- AI-generated estimates linked to jobs.
-- Tracks the full proposal text and whether the customer accepted.
-- -----------------------------------------------------------------------------
create table if not exists proposals (
    id               uuid primary key default gen_random_uuid(),
    job_id           uuid references jobs(id),
    client_id        uuid references clients(id),
    customer_id      uuid references customers(id),
    proposal_text    text,            -- full generated proposal
    amount_estimate  numeric(10,2),
    status           text default 'draft', -- draft, sent, accepted, rejected, expired
    sent_at          timestamptz,
    accepted_at      timestamptz,
    created_at       timestamptz default now()
);


-- -----------------------------------------------------------------------------
-- invoices
-- AI-generated invoices linked to completed jobs.
-- Tracks payment status through the collection lifecycle.
-- -----------------------------------------------------------------------------
create table if not exists invoices (
    id               uuid primary key default gen_random_uuid(),
    job_id           uuid references jobs(id),
    client_id        uuid references clients(id),
    customer_id      uuid references customers(id),
    invoice_text     text,            -- full generated invoice
    amount_due       numeric(10,2),
    status           text default 'draft', -- draft, sent, paid, overdue
    due_date         date,
    sent_at          timestamptz,
    paid_at          timestamptz,
    created_at       timestamptz default now()
);


-- -----------------------------------------------------------------------------
-- messages
-- Every SMS in and out. Full conversation history for every client/customer pair.
-- Agent_used tracks which AI agent generated an outbound message.
-- -----------------------------------------------------------------------------
create table if not exists messages (
    id                 uuid primary key default gen_random_uuid(),
    client_id          uuid references clients(id),
    direction          text not null,  -- inbound, outbound
    from_number        text,
    to_number          text,
    body               text,
    agent_used         text,           -- which agent generated this (outbound only)
    job_id             uuid references jobs(id),
    telnyx_message_id  text,
    created_at         timestamptz default now()
);


-- -----------------------------------------------------------------------------
-- follow_ups
-- Scheduled follow-up queue. The follow-up agent reads this table to know
-- what to send and when — estimate chasers, payment reminders, seasonal nudges.
-- -----------------------------------------------------------------------------
create table if not exists follow_ups (
    id               uuid primary key default gen_random_uuid(),
    client_id        uuid references clients(id),
    customer_id      uuid references customers(id),
    job_id           uuid references jobs(id),
    proposal_id      uuid references proposals(id),
    follow_up_type   text,    -- estimate_followup, payment_chase, seasonal_reminder
    scheduled_for    timestamptz,
    sent_at          timestamptz,
    status           text default 'pending', -- pending, sent, cancelled
    message_sent     text,
    created_at       timestamptz default now()
);


-- -----------------------------------------------------------------------------
-- reviews
-- Review requests sent after job completion.
-- Tracks whether the customer clicked through and (hopefully) posted.
-- -----------------------------------------------------------------------------
create table if not exists reviews (
    id               uuid primary key default gen_random_uuid(),
    client_id        uuid references clients(id),
    customer_id      uuid references customers(id),
    job_id           uuid references jobs(id),
    status           text default 'pending', -- pending, sent, clicked, posted
    sent_at          timestamptz,
    review_link      text,
    created_at       timestamptz default now()
);
