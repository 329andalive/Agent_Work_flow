-- time_tracking_chain.sql — Adds job timing columns and current_job tracking
--
-- Enables the full SMS clock chain:
-- clock in → dispatch sent → job_start set → DONE → job_end set → next job starts

-- Job timing columns
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS job_start timestamptz;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS job_end timestamptz;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS job_duration_min integer;

-- Current job tracking on time_entries
-- When a tech is working a dispatch route, this tracks which job in the
-- sequence they're currently on. Updated on DONE to advance to next job.
ALTER TABLE time_entries ADD COLUMN IF NOT EXISTS current_job_id uuid;

COMMENT ON COLUMN jobs.job_start IS 'When work actually started (set on dispatch advance or clock-in match)';
COMMENT ON COLUMN jobs.job_end IS 'When work completed (set on DONE command)';
COMMENT ON COLUMN jobs.job_duration_min IS 'Computed: job_end - job_start in minutes';
COMMENT ON COLUMN time_entries.current_job_id IS 'The job the tech is currently working on in their dispatch route';
