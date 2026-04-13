-- ══════════════════════════════════════════════════════════════════
-- seed_20_jobs.sql
-- 20 realistic test jobs for B&B Septic dashboard testing
-- Client: B&B Septic  |  client_id: 8aafcd73-b41c-4f1a-bd01-3e7955798367
--
-- Covers a mix of:
--   • statuses: scheduled, in_progress, completed, invoiced, paid
--   • job types: pump, inspect, repair, camera, jetting, locate, emergency
--   • dates: past 2 weeks through next week (good for all dashboard views)
--   • amounts: $175 – $575 (realistic sewer & drain pricing)
--
-- Paste into Supabase SQL editor and run.
-- Safe to run multiple times — uses gen_random_uuid() so no conflicts.
-- ══════════════════════════════════════════════════════════════════

INSERT INTO jobs (
  id,
  client_id,
  customer_id,
  job_type,
  job_description,
  job_address,
  status,
  scheduled_date,
  estimated_amount,
  agent_used,
  raw_input,
  sort_order,
  created_at
) VALUES

-- ── Past jobs (2 weeks ago) — mix of completed/invoiced/paid ─────

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  '2bcbe487-3447-488b-87cc-b75ae9b9dd21', -- Arthur Crockett
  'pump',
  'Septic pump-out — 1,000 gal tank',
  '310 Northport Avenue, Belfast, ME',
  'paid',
  CURRENT_DATE - INTERVAL '14 days',
  325.00,
  'seed_sql',
  'Seeded test job',
  0,
  NOW() - INTERVAL '14 days'
),

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  'daaf75d5-764d-4bc3-81ac-e7788c908591', -- Beverly Whitaker
  'inspect',
  'Full inspection with written report — pre-sale',
  '8 Fogg Road, Brooks, ME',
  'paid',
  CURRENT_DATE - INTERVAL '13 days',
  375.00,
  'seed_sql',
  'Seeded test job',
  1,
  NOW() - INTERVAL '13 days'
),

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  'bb27b047-ad3b-4b0c-bb30-7c5e38d2644f', -- Brenda Elwell
  'repair',
  'Outlet baffle replacement',
  '66 Main Street, Lincolnville, ME',
  'paid',
  CURRENT_DATE - INTERVAL '12 days',
  225.00,
  'seed_sql',
  'Seeded test job',
  2,
  NOW() - INTERVAL '12 days'
),

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  '47c17d0b-32ff-4d91-9e4a-e3c3715af7e8', -- Carol Tweedie
  'camera',
  'Camera inspection — suspected root intrusion in main line',
  '204 Monroe Road, Monroe, ME',
  'invoiced',
  CURRENT_DATE - INTERVAL '10 days',
  300.00,
  'seed_sql',
  'Seeded test job',
  3,
  NOW() - INTERVAL '10 days'
),

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  '7fbba1f0-c341-4a83-b780-f4b4c585d405', -- Cheryl Overlock
  'pump',
  'Septic pump-out — 1,500 gal tank',
  '21 Liberty Road, Morrill, ME',
  'invoiced',
  CURRENT_DATE - INTERVAL '9 days',
  375.00,
  'seed_sql',
  'Seeded test job',
  4,
  NOW() - INTERVAL '9 days'
),

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  'ecb2fc02-a9ef-40bc-8abd-dcc4435fda19', -- Dale Wentworth
  'jetting',
  'Hydro jetting — main line grease blockage',
  '227 Route 1, Searsport, ME',
  'completed',
  CURRENT_DATE - INTERVAL '7 days',
  350.00,
  'seed_sql',
  'Seeded test job',
  5,
  NOW() - INTERVAL '7 days'
),

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  'be7ad6c7-517d-4341-aae6-87f52446aab6', -- Dennis Knowlton
  'pump',
  'Septic pump-out — 2,000 gal tank',
  '38 Halldale Road, Montville, ME',
  'completed',
  CURRENT_DATE - INTERVAL '6 days',
  450.00,
  'seed_sql',
  'Seeded test job',
  6,
  NOW() - INTERVAL '6 days'
),

-- ── This week — mix of scheduled, in_progress ────────────────────

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  '89fad4e0-840d-45df-bad8-5b9aac86a7b4', -- Donna Littlefield
  'pump',
  'Septic pump-out — 1,000 gal tank',
  '9 Church Street, Stockton Springs, ME',
  'completed',
  CURRENT_DATE - INTERVAL '2 days',
  325.00,
  'seed_sql',
  'Seeded test job',
  0,
  NOW() - INTERVAL '2 days'
),

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  '0e210c7e-6732-4aec-acc5-06f1caee4288', -- Gail Patterson
  'locate',
  'Tank locate and mark — new property owner',
  '29 Palermo Center Road, Palermo, ME',
  'completed',
  CURRENT_DATE - INTERVAL '2 days',
  175.00,
  'seed_sql',
  'Seeded test job',
  1,
  NOW() - INTERVAL '2 days'
),

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  '5f1cc9d4-5de6-48b6-8958-29dca62cc5cf', -- Glenn Pendleton
  'inspect',
  'Tank inspection — visual check and report',
  '12 Harbor View Lane, Islesboro, ME',
  'in_progress',
  CURRENT_DATE - INTERVAL '1 day',
  175.00,
  'seed_sql',
  'Seeded test job',
  0,
  NOW() - INTERVAL '1 day'
),

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  '468b4862-4d4d-490a-a421-5f90c10790c1', -- Heather Moody
  'repair',
  'Inlet baffle replacement — cracked collar',
  '77 Cook Road, Thorndike, ME',
  'in_progress',
  CURRENT_DATE - INTERVAL '1 day',
  200.00,
  'seed_sql',
  'Seeded test job',
  1,
  NOW() - INTERVAL '1 day'
),

-- ── Today ─────────────────────────────────────────────────────────

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  '7117419f-1a83-4f6d-b6ac-80c22fce5f28', -- Howard Bryant
  'pump',
  'Septic pump-out — 1,000 gal tank',
  '142 Youngtown Road, Lincolnville, ME',
  'scheduled',
  CURRENT_DATE,
  325.00,
  'seed_sql',
  'Seeded test job',
  0,
  NOW()
),

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  'b453bd21-956c-4c9c-8c7f-bebd47dec615', -- Janet Sprague
  'camera',
  'Camera inspection — main line, slow drain complaint',
  '91 Lebanon Road, Winterport, ME',
  'scheduled',
  CURRENT_DATE,
  275.00,
  'seed_sql',
  'Seeded test job',
  1,
  NOW()
),

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  '2d0ef957-bc70-4695-9d7e-a9342562b484', -- Kevin Peavey
  'emergency',
  'Emergency pump-out — system backing up into house',
  '31 Pond Road, Brooks, ME',
  'scheduled',
  CURRENT_DATE,
  575.00,
  'seed_sql',
  'Seeded test job',
  2,
  NOW()
),

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  '46d71bca-1786-46fa-8cab-2165756a562e', -- Linda Staples
  'pump',
  'Septic pump-out — 1,500 gal tank',
  '53 School Street, Unity, ME',
  'scheduled',
  CURRENT_DATE,
  375.00,
  'seed_sql',
  'Seeded test job',
  3,
  NOW()
),

-- ── Next week — all scheduled ─────────────────────────────────────

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  'a4e8a25a-f54c-4df7-8b13-85ef45502ecf', -- Norman Harriman
  'pump',
  'Septic pump-out — 1,000 gal tank',
  '116 Loggin Road, Frankfort, ME',
  'scheduled',
  CURRENT_DATE + INTERVAL '2 days',
  325.00,
  'seed_sql',
  'Seeded test job',
  0,
  NOW()
),

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  '5b357137-a9fd-4646-999c-94412be037c2', -- Philip Robbins
  'repair',
  'Outlet baffle and riser installation',
  '45 Mortland Road, Searsport, ME',
  'scheduled',
  CURRENT_DATE + INTERVAL '2 days',
  425.00,
  'seed_sql',
  'Seeded test job',
  1,
  NOW()
),

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  '3d22abea-3ff9-43e2-9e6a-57f914d5133e', -- Roberta Cross
  'jetting',
  'Hydro jetting — root intrusion, slow drain since spring',
  '120 Depot Street, Unity, ME',
  'scheduled',
  CURRENT_DATE + INTERVAL '3 days',
  350.00,
  'seed_sql',
  'Seeded test job',
  0,
  NOW()
),

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  '83909b5d-85d8-4af6-a99d-f4d0988797c8', -- Russell Hamlin
  'pump',
  'Septic pump-out — 2,000 gal tank, last done 4 years ago',
  '402 Ridge Road, Freedom, ME',
  'scheduled',
  CURRENT_DATE + INTERVAL '3 days',
  450.00,
  'seed_sql',
  'Seeded test job',
  1,
  NOW()
),

(
  gen_random_uuid(),
  '8aafcd73-b41c-4f1a-bd01-3e7955798367',
  'ef2fe217-eda4-4956-8f93-9604df457afa', -- Sharon Nickerson
  'locate',
  'Tank locate and mark — selling property, needs coords for realtor',
  '85 Stream Road, Winterport, ME',
  'scheduled',
  CURRENT_DATE + INTERVAL '4 days',
  200.00,
  'seed_sql',
  'Seeded test job',
  0,
  NOW()
);

-- ── Verify insert ────────────────────────────────────────────────
SELECT
  j.status,
  COUNT(*)                        AS job_count,
  SUM(j.estimated_amount)         AS total_estimated,
  MIN(j.scheduled_date)           AS earliest,
  MAX(j.scheduled_date)           AS latest
FROM jobs j
WHERE j.client_id = '8aafcd73-b41c-4f1a-bd01-3e7955798367'
  AND j.agent_used = 'seed_sql'
GROUP BY j.status
ORDER BY j.status;
