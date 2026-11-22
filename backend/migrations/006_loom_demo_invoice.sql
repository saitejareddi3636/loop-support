-- Add one fresh fictional example. Never reset or delete an existing receipt.
INSERT INTO invoices(invoice_id,customer_id,account_id,subscription_id,contract_id,date,amount_cents,currency,status,description,billing_cycle)
SELECT 'INV-3005',customer_id,account_id,subscription_id,contract_id,DATE '2026-11-21',8900,'USD','paid','Loopp Business additional workspace monthly invoice (Loom demo)','monthly'
FROM invoices WHERE invoice_id='INV-3002' ON CONFLICT DO NOTHING;
