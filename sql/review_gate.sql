-- review_gate.sql — Adds review tracking and draft corrections for agent learning
--
-- The review gate ensures every AI-generated document is reviewed by the owner
-- before reaching the customer. Every correction becomes a training signal.

-- Review tracking on proposals
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS reviewed_at timestamptz;
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS reviewed_by text;
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS rejected_at timestamptz;
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS rejection_reason text;

-- Review tracking on invoices
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS reviewed_at timestamptz;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS reviewed_by text;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS rejected_at timestamptz;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS rejection_reason text;

-- Draft corrections table — contextual training signals from owner edits
-- Every field the owner changes during review is logged with job context
-- so the AI can learn conditional preferences (e.g. "high tier for commercial")
CREATE TABLE IF NOT EXISTS draft_corrections (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       uuid NOT NULL REFERENCES clients(id),
    document_type   text NOT NULL,              -- proposal or invoice
    document_id     uuid NOT NULL,
    job_id          uuid,
    job_type        text,                       -- pump, repair, inspect, etc.
    customer_type   text,                       -- residential, commercial (if known)
    field_name      text NOT NULL,              -- description, price, job_name, customer_name, etc.
    ai_value        text,                       -- what the AI generated
    owner_value     text,                       -- what the owner changed it to
    action          text DEFAULT 'edit',        -- edit, add (new line item), remove, reject
    created_at      timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_draft_corrections_client
    ON draft_corrections (client_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_draft_corrections_job_type
    ON draft_corrections (client_id, job_type)
    WHERE job_type IS NOT NULL;

COMMENT ON TABLE draft_corrections IS 'Training signals from owner review of AI-generated documents. Each row is one field correction with job context.';
