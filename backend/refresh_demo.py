"""Insert two dated fictional invoices per day; never update invoices or receipts."""

from datetime import date
from app.db import connect

TARGETS = ((1, "cus_1003", 6900), (2, "cus_1001", 2900))


def refresh():
    today = date.today()
    inserted, available = [], []
    with connect() as db:
        db.execute("SELECT pg_advisory_xact_lock(681203)")
        for number, customer_id, amount in TARGETS:
            invoice_id = f"INV-D{today:%Y%m%d}-{number}"
            account = db.execute(
                """
                SELECT a.account_id, s.subscription_id, c.contract_id, r.window_days,
                       r.max_amount_cents, r.billing_cycles
                FROM customers u JOIN accounts a USING(customer_id)
                JOIN subscriptions s USING(account_id) JOIN contracts c USING(account_id)
                JOIN LATERAL (
                    SELECT * FROM refund_rules WHERE tier=s.tier AND active
                    AND effective_date<=%s AND (expiration_date IS NULL OR expiration_date>=%s)
                    ORDER BY version DESC LIMIT 1
                ) r ON TRUE
                WHERE u.customer_id=%s AND u.fictional
                AND s.status IN ('active','cancelled') AND s.billing_cycle='monthly'
                AND cardinality(a.restrictions)=0 AND c.status='active'
                AND (c.ends_on IS NULL OR c.ends_on>=%s)
                AND NOT r.manual_review AND r.window_days>=0
                AND 'monthly'=ANY(r.billing_cycles) AND r.max_amount_cents>=%s
                """,
                (today, today, customer_id, today, amount),
            ).fetchone()
            if not account:
                continue
            row = db.execute(
                """
                INSERT INTO invoices(invoice_id,customer_id,account_id,subscription_id,
                    contract_id,date,amount_cents,currency,status,description,billing_cycle)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'USD','paid','Fictional daily demo monthly invoice','monthly')
                ON CONFLICT(invoice_id) DO NOTHING RETURNING invoice_id
                """,
                (
                    invoice_id,
                    customer_id,
                    account["account_id"],
                    account["subscription_id"],
                    account["contract_id"],
                    today,
                    amount,
                ),
            ).fetchone()
            if row:
                inserted.append(row["invoice_id"])
            row = db.execute(
                """
                SELECT i.invoice_id FROM invoices i
                WHERE i.invoice_id=%s AND i.customer_id=%s AND i.account_id=%s
                AND i.subscription_id=%s AND i.contract_id=%s AND i.status='paid'
                AND i.currency='USD' AND i.amount_cents>0 AND i.amount_cents<=%s
                AND i.billing_cycle=ANY(%s) AND i.date BETWEEN %s-%s::int AND %s
                AND NOT EXISTS(SELECT 1 FROM refund_receipts r WHERE r.invoice_id=i.invoice_id)
                """,
                (
                    invoice_id,
                    customer_id,
                    account["account_id"],
                    account["subscription_id"],
                    account["contract_id"],
                    account["max_amount_cents"],
                    account["billing_cycles"],
                    today,
                    account["window_days"],
                    today,
                ),
            ).fetchone()
            if row:
                available.append(row["invoice_id"])
    return {"date": today.isoformat(), "inserted": inserted, "available": available}


if __name__ == "__main__":
    import json

    print(json.dumps(refresh()))
