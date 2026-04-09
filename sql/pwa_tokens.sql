-- pwa_tokens.sql — Magic link tokens for PWA authentication
--
-- A tech requests a login link by entering their phone number on /pwa/login.
-- The system generates an 8-char token, stores a row here, and sends the
-- link via the notify router (email or SMS based on client switches).
-- The tech taps the link, the token is verified + consumed, and a session
-- is set on their device. The token can only be used once.

CREATE TABLE IF NOT EXISTS pwa_tokens (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token         text UNIQUE NOT NULL,
    client_id     uuid NOT NULL REFERENCES clients(id),
    employee_id   uuid REFERENCES employees(id),
    employee_phone text,                                    -- captured for owner_mobile fallback
    purpose       text DEFAULT 'pwa_login',
    created_at    timestamptz DEFAULT now(),
    expires_at    timestamptz NOT NULL DEFAULT (now() + interval '15 minutes'),
    consumed_at   timestamptz,                              -- set when token is verified
    consumed_ip   text,
    user_agent    text
);

CREATE INDEX IF NOT EXISTS idx_pwa_tokens_token
    ON pwa_tokens (token)
    WHERE consumed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_pwa_tokens_employee
    ON pwa_tokens (employee_id, created_at DESC);

COMMENT ON TABLE pwa_tokens IS 'Magic-link tokens for PWA authentication. One-shot, 15-minute expiry.';
COMMENT ON COLUMN pwa_tokens.consumed_at IS 'Set when the token is verified. NULL = unused, NOT NULL = burned.';
