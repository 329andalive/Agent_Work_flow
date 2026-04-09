-- pwa_chat_messages.sql — Chat history for the PWA AI conversation
--
-- Step 6a stores plain user/assistant turns. Step 6b will add action
-- chips by writing structured data to the metadata jsonb column.
--
-- session_id is included from day one even though 6a doesn't expose
-- "new conversation" buttons. It scopes history queries cheaply
-- (employee + session_id covered by an index) and gives us a clean
-- boundary when we want to start fresh later.

CREATE TABLE IF NOT EXISTS pwa_chat_messages (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id    uuid NOT NULL REFERENCES clients(id),
    employee_id  uuid NOT NULL REFERENCES employees(id),
    session_id   uuid NOT NULL DEFAULT gen_random_uuid(),
    role         text NOT NULL,                 -- 'user' | 'assistant'
    content      text NOT NULL,
    metadata     jsonb DEFAULT '{}',            -- room for action chips, model info, tokens used
    created_at   timestamptz DEFAULT now()
);

-- Most common query: load this employee's recent messages
CREATE INDEX IF NOT EXISTS idx_chat_employee
    ON pwa_chat_messages (employee_id, created_at DESC);

-- Session scoping: load one conversation in chronological order
CREATE INDEX IF NOT EXISTS idx_chat_session
    ON pwa_chat_messages (session_id, created_at ASC);

-- Multi-tenant safety: index for client-wide queries (audit, reporting)
CREATE INDEX IF NOT EXISTS idx_chat_client_created
    ON pwa_chat_messages (client_id, created_at DESC);

COMMENT ON TABLE pwa_chat_messages IS
  'Conversation history for the PWA AI chat. One row per turn (user or assistant).';
COMMENT ON COLUMN pwa_chat_messages.session_id IS
  'Conversation boundary. Defaults to a new uuid per row but is overwritten in Python so a single conversation shares one id.';
COMMENT ON COLUMN pwa_chat_messages.metadata IS
  'jsonb for future structured data — action chips, token counts, model name, etc.';
