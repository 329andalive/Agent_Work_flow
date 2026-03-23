-- square_payment_writeback.sql
-- Schema migration required before Square production go-live.
-- Run this in the Supabase SQL editor (Dashboard → SQL Editor → New query).
-- Safe to run multiple times — all statements use IF NOT EXISTS guards.
--
-- What this adds:
--   invoices.square_payment_id      — audit trail of Square payment IDs
--   invoice_links.square_order_id   — reverse lookup key for webhook handler
--   invoice_links.square_payment_link_id — Square payment link ID
--   invoice_links.payment_link_url  — Square checkout URL for PAY NOW button
--
-- Without these columns:
--   - mark_invoice_paid() will throw on every real payment (column missing)
--   - get_link_by_square_order() will always return None (no order ID stored)
--   - /dashboard/payments/ will show no data even after real payments land

-- ── invoices table ──────────────────────────────────────────────────────────

ALTER TABLE invoices
  ADD COLUMN IF NOT EXISTS square_payment_id TEXT;

COMMENT ON COLUMN invoices.square_payment_id IS
  'Square payment ID — written by mark_invoice_paid() on webhook confirmation';

CREATE INDEX IF NOT EXISTS idx_invoices_square_payment_id
  ON invoices (square_payment_id)
  WHERE square_payment_id IS NOT NULL;

-- ── invoice_links table ─────────────────────────────────────────────────────

ALTER TABLE invoice_links
  ADD COLUMN IF NOT EXISTS square_order_id TEXT;

COMMENT ON COLUMN invoice_links.square_order_id IS
  'Square order ID — written by attach_payment_link() in invoice_agent Step 8b.
   Used by get_link_by_square_order() in the /webhooks/square handler to
   reverse-lookup which invoice was paid.';

ALTER TABLE invoice_links
  ADD COLUMN IF NOT EXISTS square_payment_link_id TEXT;

COMMENT ON COLUMN invoice_links.square_payment_link_id IS
  'Square payment link ID returned by create_payment_link()';

ALTER TABLE invoice_links
  ADD COLUMN IF NOT EXISTS payment_link_url TEXT;

COMMENT ON COLUMN invoice_links.payment_link_url IS
  'Square checkout URL — the PAY NOW destination sent to customers';

CREATE INDEX IF NOT EXISTS idx_invoice_links_square_order_id
  ON invoice_links (square_order_id)
  WHERE square_order_id IS NOT NULL;

-- ── verify ──────────────────────────────────────────────────────────────────
-- After running, confirm all columns exist:
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name IN ('invoices', 'invoice_links')
  AND column_name IN (
    'square_payment_id',
    'square_order_id',
    'square_payment_link_id',
    'payment_link_url'
  )
ORDER BY table_name, column_name;
