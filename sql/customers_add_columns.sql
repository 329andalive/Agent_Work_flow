-- customers_add_columns.sql — Add email and notes columns to customers table
-- Run in Supabase SQL editor. Safe to run multiple times.
ALTER TABLE customers ADD COLUMN IF NOT EXISTS customer_email TEXT;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS notes TEXT;
