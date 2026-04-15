-- =============================================================================
-- add_email_to_clients.sql — April 2026
--
-- Adds a permanent `email` column to the clients table and backfills it
-- from the access_requests table for every existing approved client.
--
-- Why: until now the client owner's email only lived on access_requests
-- (the original signup form). Once approved, the email got orphaned and
-- the admin dashboard had no way to reach it, so the Reset PIN / Send
-- Reminder / Resend Welcome forms made the admin retype the address every
-- time. With this column, the email is permanent client metadata and the
-- forms pre-fill from it.
--
-- Run this in the Supabase SQL editor. Idempotent — safe to run twice.
-- =============================================================================

-- Step 1: Add the column. Nullable so we don't force a value for clients
-- that were created without one (we'll surface those as "—" in the UI
-- and the admin can fix them via the existing forms).
ALTER TABLE clients
  ADD COLUMN IF NOT EXISTS email TEXT;

-- Step 2: Backfill from access_requests.
--
-- Match by digits-only-last-10 because:
--   - access_requests.phone is stored free-form ("(207) 555-0100", "+1 207-555-0100")
--   - clients.phone is stored E.164 ("+12075550100") after the approve_request
--     route normalizes it
-- Stripping all non-digits and comparing the trailing 10 digits handles every
-- variation we've seen in production.
--
-- Only updates rows where email is currently NULL — re-running this won't
-- clobber an email an admin has manually corrected via a future UI.
UPDATE clients c
SET    email = ar.email
FROM   access_requests ar
WHERE  c.email IS NULL
  AND  ar.email IS NOT NULL
  AND  ar.email <> ''
  AND  right(regexp_replace(c.phone,  '\D', '', 'g'), 10) =
       right(regexp_replace(ar.phone, '\D', '', 'g'), 10);

-- Step 3: Verification report.
--
-- Run this after the UPDATE to spot-check the backfill. Any row marked
-- "no match" needs manual handling — most likely an old client whose
-- access_request record was deleted, or a client provisioned through
-- a non-form path. You can fix those one at a time via the admin UI
-- (the Send Reminder form already accepts an email and could be
-- extended to upsert it back to clients.email).
SELECT
  business_name,
  phone,
  COALESCE(email, '— no match — manual fix needed') AS email,
  CASE WHEN email IS NULL THEN '⚠'  ELSE '✓' END     AS status,
  created_at
FROM   clients
ORDER  BY (email IS NULL) DESC, business_name;
