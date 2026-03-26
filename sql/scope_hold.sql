-- scope_hold.sql — Add scope_hold flag to jobs table
-- Run in Supabase SQL editor. Safe to run multiple times.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS scope_hold boolean DEFAULT false;
