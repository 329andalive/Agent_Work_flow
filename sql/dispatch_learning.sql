-- dispatch_learning.sql — Granular dispatch log for AI learning
-- Run in Supabase SQL editor. Safe to run multiple times.
--
-- This table is the foundation of the dispatch apprentice.
-- Every assignment gets one row. After 30+ sessions, Claude reads
-- patterns from this data to suggest future assignments.

-- ═══════════════════════════════════════════════════════════════════════════
-- dispatch_decisions — one row per job assignment per session
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dispatch_decisions (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id         uuid REFERENCES clients(id) NOT NULL,
  session_id        uuid NOT NULL,
  dispatch_date     date NOT NULL,

  -- What was assigned
  job_id            uuid REFERENCES jobs(id) NOT NULL,
  worker_id         uuid REFERENCES employees(id) NOT NULL,
  job_type          text,
  zone_cluster      text,
  requested_time    time,
  sort_order        integer DEFAULT 0,

  -- Learning signals
  was_suggested     boolean DEFAULT false,     -- true if AI suggested this pairing
  was_accepted      boolean DEFAULT false,     -- true if human accepted the suggestion
  was_overridden    boolean DEFAULT false,     -- true if human moved it to a different worker
  override_reason   text,                      -- optional: "closer to next job", "Jesse knows this customer"

  -- Outcome (filled in later by worker SMS replies)
  outcome_status    text,                      -- completed, carry_forward, no_show, parts_pending
  outcome_at        timestamptz,

  created_at        timestamptz DEFAULT now()
);

-- Indexes for pattern queries
CREATE INDEX IF NOT EXISTS idx_dispatch_decisions_client_date
  ON dispatch_decisions (client_id, dispatch_date);
CREATE INDEX IF NOT EXISTS idx_dispatch_decisions_worker
  ON dispatch_decisions (worker_id);
CREATE INDEX IF NOT EXISTS idx_dispatch_decisions_session
  ON dispatch_decisions (session_id);

COMMENT ON TABLE dispatch_decisions IS
  'One row per job assignment per dispatch session. The AI apprentice reads
   patterns from this table after 30+ sessions to suggest future assignments.
   was_suggested + was_overridden flags teach the model which suggestions
   were accepted vs rejected by the human dispatcher.';
