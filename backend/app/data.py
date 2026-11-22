"""Structured fictional records live in PostgreSQL. See seed_data.py for fixtures."""
from app.db import connect

def customer_by_email(email):
    with connect() as db:
        return db.execute("SELECT * FROM customers WHERE email=%s", (email.strip().lower(),)).fetchone()

def account_by_customer(customer_id, connection=None):
    query = """SELECT c.customer_id,c.name,c.email,a.account_id,a.restrictions,s.subscription_id,
        s.tier,s.tier AS plan,s.status,s.billing_cycle,k.contract_id,k.status AS contract_status,
        k.ends_on AS contract_ends_on FROM customers c JOIN accounts a USING(customer_id)
        JOIN subscriptions s USING(account_id) JOIN contracts k USING(account_id) WHERE c.customer_id=%s"""
    if connection:
        return connection.execute(query, (customer_id,)).fetchone()
    with connect() as db:
        return db.execute(query, (customer_id,)).fetchone()

def invoice_by_id(invoice_id, connection=None):
    query = "SELECT *, amount_cents/100.0 AS amount FROM invoices WHERE invoice_id=%s"
    if connection:
        return connection.execute(query,(invoice_id.strip().upper(),)).fetchone()
    with connect() as db:
        return db.execute(query,(invoice_id.strip().upper(),)).fetchone()
