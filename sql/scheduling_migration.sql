-- scheduling_migration.sql
-- Creates the sms_message_log table for tracking all outbound SMS.
-- Safe to run multiple times (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS sms_message_log (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_phone      text NOT NULL,
    recipient_phone   text NOT NULL,
    message_type      text NOT NULL DEFAULT 'invoice',
    body              text,
    telnyx_message_id text,
    status            text NOT NULL DEFAULT 'sent',
    sent_at           timestamptz DEFAULT now(),
    created_at        timestamptz DEFAULT now()
);

-- Index for querying by client + type
CREATE INDEX IF NOT EXISTS idx_sms_message_log_client_type
    ON sms_message_log (client_phone, message_type);

-- Index for querying by recipient
CREATE INDEX IF NOT EXISTS idx_sms_message_log_recipient
    ON sms_message_log (recipient_phone);

COMMENT ON TABLE sms_message_log IS
    'Tracks every outbound SMS sent via Telnyx. Written by execution/sms_send.py.';

COMMENT ON COLUMN sms_message_log.message_type IS
    'One of: route, schedule_nudge, booking_confirm, appt_reminder, cancellation, '
    'invoice, review_ask, waitlist_notify, no_show_followup, carry_forward_notify, wave_assignment';
