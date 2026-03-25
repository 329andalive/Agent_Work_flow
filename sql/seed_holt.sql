-- seed_holt.sql — Seed dispatch data for Holt Sewer & Drain
-- Run in Supabase SQL editor AFTER scheduling columns exist on jobs table.
-- Safe to run multiple times — uses ON CONFLICT or checks before insert.
--
-- Client: Holt Sewer & Drain
-- Client ID: 8aafcd73-b41c-4f1a-bd01-3e7955798367
-- Client phone: +12074190986

-- ═══════════════════════════════════════════════════════════════════════════
-- Step 0: Add dispatch columns to jobs table (if not already present)
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS geo_lat numeric(9,6);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS geo_lng numeric(9,6);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS zone_cluster text;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS requested_time time;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS dispatch_status text DEFAULT 'unassigned';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS assigned_worker_id uuid;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS wave_id text;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS sort_order integer DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS incomplete_reason text;

CREATE INDEX IF NOT EXISTS idx_jobs_dispatch_status
  ON jobs (dispatch_status) WHERE dispatch_status IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_scheduled_date
  ON jobs (scheduled_date) WHERE scheduled_date IS NOT NULL;

-- ═══════════════════════════════════════════════════════════════════════════
-- Step 1: Add dispatch_log table
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dispatch_log (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id   uuid REFERENCES clients(id),
  session_id  uuid,
  job_count   integer DEFAULT 0,
  worker_count integer DEFAULT 0,
  created_at  timestamptz DEFAULT now()
);

-- ═══════════════════════════════════════════════════════════════════════════
-- Step 2: Add route_assignments table
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS route_assignments (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id   uuid REFERENCES clients(id),
  session_id  uuid,
  job_id      uuid REFERENCES jobs(id),
  worker_id   uuid REFERENCES employees(id),
  wave_id     text,
  sort_order  integer DEFAULT 0,
  assigned_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_route_assignments_session
  ON route_assignments (session_id);
CREATE INDEX IF NOT EXISTS idx_route_assignments_worker
  ON route_assignments (worker_id);

-- ═══════════════════════════════════════════════════════════════════════════
-- Step 3: Add route_tokens table
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS route_tokens (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  token          text UNIQUE NOT NULL,
  client_id      uuid REFERENCES clients(id),
  worker_id      uuid REFERENCES employees(id),
  session_id     uuid,
  dispatch_date  date,
  expires_at     timestamptz,
  viewed_at      timestamptz,
  created_at     timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_route_tokens_token
  ON route_tokens (token);

-- ═══════════════════════════════════════════════════════════════════════════
-- Step 4: Seed 3 employees (workers) for Holt Sewer & Drain
-- ═══════════════════════════════════════════════════════════════════════════

-- Check if employees already exist before inserting
INSERT INTO employees (client_id, name, phone, role, active)
SELECT '8aafcd73-b41c-4f1a-bd01-3e7955798367', 'Dad', '+12076538819', 'owner', true
WHERE NOT EXISTS (
  SELECT 1 FROM employees
  WHERE client_id = '8aafcd73-b41c-4f1a-bd01-3e7955798367' AND name = 'Dad'
);

INSERT INTO employees (client_id, name, phone, role, active)
SELECT '8aafcd73-b41c-4f1a-bd01-3e7955798367', 'Jesse', '+12075551001', 'foreman', true
WHERE NOT EXISTS (
  SELECT 1 FROM employees
  WHERE client_id = '8aafcd73-b41c-4f1a-bd01-3e7955798367' AND name = 'Jesse'
);

INSERT INTO employees (client_id, name, phone, role, active)
SELECT '8aafcd73-b41c-4f1a-bd01-3e7955798367', 'Austin', '+12075551002', 'field_tech', true
WHERE NOT EXISTS (
  SELECT 1 FROM employees
  WHERE client_id = '8aafcd73-b41c-4f1a-bd01-3e7955798367' AND name = 'Austin'
);

-- ═══════════════════════════════════════════════════════════════════════════
-- Step 5: Seed 3 jobs for today + 1 carry-forward from yesterday
-- Uses existing customers from the 25 Holt test customers
-- ═══════════════════════════════════════════════════════════════════════════

-- Today's jobs
INSERT INTO jobs (client_id, customer_id, job_type, job_description, status,
  scheduled_date, estimated_amount, dispatch_status, zone_cluster, raw_input)
SELECT
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  c.id,
  'pump',
  'Septic pump-out — 1,000 gal. tank',
  'scheduled',
  CURRENT_DATE,
  275.00,
  'unassigned',
  'Central',
  'Seed data — pump out for dispatch testing'
FROM customers c
WHERE c.client_id = '8aafcd73-b41c-4f1a-bd01-3e7955798367'
ORDER BY c.customer_name
LIMIT 1;

INSERT INTO jobs (client_id, customer_id, job_type, job_description, status,
  scheduled_date, estimated_amount, dispatch_status, zone_cluster, requested_time, raw_input)
SELECT
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  c.id,
  'repair',
  'Baffle replacement + riser installation',
  'scheduled',
  CURRENT_DATE,
  625.00,
  'unassigned',
  'North',
  '09:00',
  'Seed data — repair job for dispatch testing'
FROM customers c
WHERE c.client_id = '8aafcd73-b41c-4f1a-bd01-3e7955798367'
ORDER BY c.customer_name
OFFSET 1 LIMIT 1;

INSERT INTO jobs (client_id, customer_id, job_type, job_description, status,
  scheduled_date, estimated_amount, dispatch_status, zone_cluster, raw_input)
SELECT
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  c.id,
  'inspect',
  'Septic inspection — full with report',
  'scheduled',
  CURRENT_DATE,
  350.00,
  'unassigned',
  'South',
  'Seed data — inspection for dispatch testing'
FROM customers c
WHERE c.client_id = '8aafcd73-b41c-4f1a-bd01-3e7955798367'
ORDER BY c.customer_name
OFFSET 2 LIMIT 1;

-- Yesterday's carry-forward job
INSERT INTO jobs (client_id, customer_id, job_type, job_description, status,
  scheduled_date, estimated_amount, dispatch_status, zone_cluster,
  incomplete_reason, raw_input)
SELECT
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  c.id,
  'pump',
  'Carry-forward: pump-out rescheduled from yesterday',
  'scheduled',
  CURRENT_DATE - 1,
  300.00,
  'carry_forward',
  'Central',
  'Customer not home — carry forward',
  'Seed data — carry forward for dispatch testing'
FROM customers c
WHERE c.client_id = '8aafcd73-b41c-4f1a-bd01-3e7955798367'
ORDER BY c.customer_name
OFFSET 3 LIMIT 1;

-- ═══════════════════════════════════════════════════════════════════════════
-- Verification queries (run after seed to confirm)
-- ═══════════════════════════════════════════════════════════════════════════

-- Check workers
-- SELECT name, phone, role FROM employees
-- WHERE client_id = '8aafcd73-b41c-4f1a-bd01-3e7955798367' AND active = true;
-- Expected: Dad (owner), Jesse (foreman), Austin (field_tech)

-- Check today's dispatch jobs
-- SELECT job_type, job_description, dispatch_status, zone_cluster, scheduled_date
-- FROM jobs
-- WHERE client_id = '8aafcd73-b41c-4f1a-bd01-3e7955798367'
--   AND scheduled_date >= CURRENT_DATE - 1
-- ORDER BY scheduled_date DESC;
-- Expected: 3 today (unassigned) + 1 yesterday (carry_forward)
