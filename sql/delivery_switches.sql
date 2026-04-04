-- delivery_switches.sql — Per-client outbound channel controls
--
-- Two switches per client:
--   sms_outbound_enabled:   false until 10DLC campaign approved
--   email_outbound_enabled: true from day one (Resend)
--
-- Plus employee opt-out tracking for internal SMS.

-- Client-level delivery switches
ALTER TABLE clients ADD COLUMN IF NOT EXISTS sms_outbound_enabled boolean DEFAULT false;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS email_outbound_enabled boolean DEFAULT true;

COMMENT ON COLUMN clients.sms_outbound_enabled IS 'Set to true after 10DLC campaign is approved. Controls ALL outbound SMS.';
COMMENT ON COLUMN clients.email_outbound_enabled IS 'Email delivery via Resend. True by default for all clients.';

-- Employee SMS opt-out (internal team)
ALTER TABLE employees ADD COLUMN IF NOT EXISTS sms_opted_out boolean DEFAULT false;

COMMENT ON COLUMN employees.sms_opted_out IS 'Set to true if employee texts STOP. Respected even after 10DLC approval.';
