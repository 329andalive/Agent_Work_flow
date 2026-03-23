-- Add square_payment_id audit column to invoices table
-- Safe to run multiple times (IF NOT EXISTS guard)
ALTER TABLE invoices
  ADD COLUMN IF NOT EXISTS square_payment_id TEXT;

-- Index for audit lookups
CREATE INDEX IF NOT EXISTS idx_invoices_square_payment_id
  ON invoices (square_payment_id)
  WHERE square_payment_id IS NOT NULL;

-- Add square_order_id to invoice_links if not present
-- Required for get_link_by_square_order() reverse lookup
ALTER TABLE invoice_links
  ADD COLUMN IF NOT EXISTS square_order_id TEXT;

ALTER TABLE invoice_links
  ADD COLUMN IF NOT EXISTS square_payment_link_id TEXT;

ALTER TABLE invoice_links
  ADD COLUMN IF NOT EXISTS payment_link_url TEXT;

CREATE INDEX IF NOT EXISTS idx_invoice_links_square_order_id
  ON invoice_links (square_order_id)
  WHERE square_order_id IS NOT NULL;
