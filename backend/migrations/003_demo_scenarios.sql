-- Preserve receipt rows; correct the new fictional scenarios seeded by this change.
UPDATE invoices SET status='paid',date='2026-11-15',amount_cents=14900,description='Loopp Business monthly subscription' WHERE invoice_id='INV-3001' AND NOT EXISTS(SELECT 1 FROM refund_receipts WHERE invoice_id='INV-3001');
UPDATE invoices SET status='paid',date='2026-11-18',amount_cents=6900,description='Loopp Business plan adjustment' WHERE invoice_id='INV-3002' AND NOT EXISTS(SELECT 1 FROM refund_receipts WHERE invoice_id='INV-3002');
UPDATE invoices SET status='paid' WHERE invoice_id='INV-4001' AND NOT EXISTS(SELECT 1 FROM refund_receipts WHERE invoice_id='INV-4001');
