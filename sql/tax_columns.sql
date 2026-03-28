-- tax_columns.sql — Add tax fields to invoices table
-- Run in Supabase SQL editor. Safe to run multiple times.
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS tax_rate numeric(5,4) DEFAULT 0.0;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS tax_amount numeric(10,2) DEFAULT 0.0;
