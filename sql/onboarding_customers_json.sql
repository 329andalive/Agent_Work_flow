-- Add customers_json column to onboarding_sessions table
-- Stores imported customer list during setup wizard (Step 5)
-- Customers are created in the customers table on approval

ALTER TABLE onboarding_sessions
  ADD COLUMN IF NOT EXISTS customers_json jsonb;

COMMENT ON COLUMN onboarding_sessions.customers_json IS
  'Array of {name, phone, email, address} imported during onboarding wizard Step 5';
