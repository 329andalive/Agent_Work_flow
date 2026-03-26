-- carry_forward.sql — Add carry_forward_from column to jobs table
-- Run in Supabase SQL editor. Safe to run multiple times.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS carry_forward_from date;
