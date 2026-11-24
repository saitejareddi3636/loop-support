"""Financial and ticket invariants are enforced transactionally in PostgreSQL."""

import re
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from psycopg.types.json import Jsonb
from app.db import connect
from app.data import account_by_customer, invoice_by_id
from app.policy import active_refund_rule
from app.storage import verified_customer, add_message


def eligibility(chat_id, invoice_id, db, lock=True):
    customer_id = verified_customer(chat_id, db)
    if not customer_id:
        return {"error": "Verify the fictional account first."}
    invoice = invoice_by_id(invoice_id, db)
    if not invoice or invoice["customer_id"] != customer_id:
        return {"error": "Invoice not found for the verified customer."}
    # Locks keep eligibility stable through receipt persistence, even against concurrent writers.
    if lock:
        db.execute(
            "SELECT invoice_id FROM invoices WHERE invoice_id=%s FOR UPDATE",
            (invoice["invoice_id"],),
        )
        db.execute(
            "SELECT subscription_id FROM subscriptions WHERE account_id=%s FOR SHARE",
            (invoice["account_id"],),
        )
        db.execute(
            "SELECT contract_id FROM contracts WHERE account_id=%s FOR SHARE",
            (invoice["account_id"],),
        )
    invoice = invoice_by_id(invoice_id, db)
    account = account_by_customer(customer_id, db)
    if not invoice or invoice["customer_id"] != customer_id:
        return {"error": "Invoice ownership changed; no action was performed."}
    if invoice["status"] != "paid":
        return {"error": "Only paid invoices are eligible for automatic mock refunds."}
    if account["status"] not in ("active", "cancelled") or account["restrictions"]:
        return {"error": "The subscription status or account restrictions require human review."}
    if account["contract_status"] != "active" or (
        account["contract_ends_on"] and account["contract_ends_on"] < date.today()
    ):
        return {"error": "An active, unexpired contract is required; human review is available."}
    rule = active_refund_rule(account["tier"], db)
    if not rule:
        return {"error": "No current tier-specific refund rule is available."}
    db.execute(
        "SELECT tier FROM refund_rules WHERE tier=%s AND version=%s FOR SHARE",
        (rule["tier"], rule["version"]),
    )
    if rule["manual_review"]:
        return {"error": "This tier requires human review and has no automatic mock refund."}
    age = (date.today() - invoice["date"]).days
    if invoice["billing_cycle"] not in rule["billing_cycles"]:
        return {"error": "This billing cycle requires human review."}
    if age < 0 or age > rule["window_days"]:
        return {
            "error": f"The invoice is outside the current {rule['window_days']}-day eligibility window."
        }
    if invoice["amount_cents"] <= 0 or invoice["amount_cents"] > rule["max_amount_cents"]:
        return {"error": "The invoice amount exceeds the automatic refund limit."}
    return {"customer_id": customer_id, "invoice": invoice, "rule": rule}


def existing_refund(db, chat_id, invoice_id):
    """Return an owned, persisted receipt without re-running new-refund eligibility."""
    receipt = db.execute(
        """SELECT r.receipt_id,r.invoice_id,r.amount_cents,r.policy_version,i.currency
        FROM refund_receipts r JOIN invoices i ON i.invoice_id=r.invoice_id
        JOIN verified_customers v ON v.customer_id=r.customer_id AND v.customer_id=i.customer_id
        WHERE v.chat_id=%s AND r.invoice_id=%s""",
        (chat_id, invoice_id.strip().upper()),
    ).fetchone()
    if receipt:
        return {"status": "already_refunded", "mock": True, "replayed": True, **receipt}
    return None


def stage_refund(chat_id, invoice_id):
    with connect() as db:
        db.execute("SELECT id FROM chats WHERE id=%s FOR UPDATE", (chat_id,))
        prior = existing_refund(db, chat_id, invoice_id)
        if prior:
            return prior
        check = eligibility(chat_id, invoice_id, db)
        if "error" in check:
            return check
        inv = check["invoice"]
        rule = check["rule"]
        prior = existing_refund(db, chat_id, inv["invoice_id"])
        if prior:
            return prior
        pending = db.execute(
            """SELECT * FROM refund_pending WHERE chat_id=%s AND invoice_id=%s AND amount_cents=%s
            AND policy_version=%s AND expires_at>now() LIMIT 1""",
            (chat_id, inv["invoice_id"], inv["amount_cents"], rule["version"]),
        ).fetchone()
        token = pending["token"] if pending else str(uuid4())
        if not pending:
            db.execute(
                "INSERT INTO refund_pending VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    token,
                    chat_id,
                    check["customer_id"],
                    inv["invoice_id"],
                    inv["amount_cents"],
                    rule["version"],
                    datetime.now(timezone.utc) + timedelta(minutes=10),
                ),
            )
        return {
            "status": "confirmation_required",
            "token": token,
            "invoice_id": inv["invoice_id"],
            "amount_cents": inv["amount_cents"],
            "currency": inv["currency"],
            "policy_version": rule["version"],
            "message": "Use the invoice-specific confirmation button. No receipt has been issued.",
        }


def pending_refund(chat_id):
    with connect() as db:
        row = db.execute(
            """SELECT p.token,p.invoice_id,p.amount_cents,i.currency FROM refund_pending p JOIN invoices i USING(invoice_id)
            WHERE p.chat_id=%s AND expires_at>now() AND NOT EXISTS(SELECT 1 FROM refund_receipts r WHERE r.invoice_id=p.invoice_id) ORDER BY expires_at DESC LIMIT 1""",
            (chat_id,),
        ).fetchone()
        return {"status": "confirmation_required", **row} if row else None


def confirm_refund(chat_id, token, invoice_id, amount_cents):
    with connect() as db:
        db.execute("SELECT id FROM chats WHERE id=%s FOR UPDATE", (chat_id,))
        prior = db.execute(
            "SELECT * FROM refund_receipts WHERE confirmation_token=%s AND chat_id=%s",
            (token, chat_id),
        ).fetchone()
        if prior and prior["invoice_id"] == invoice_id and prior["amount_cents"] == amount_cents:
            return {"status": "issued", **prior, "currency": "USD", "mock": True, "replayed": True}
        p = db.execute(
            "SELECT * FROM refund_pending WHERE token=%s AND chat_id=%s FOR UPDATE",
            (token, chat_id),
        ).fetchone()
        if not p or p["invoice_id"] != invoice_id or p["amount_cents"] != amount_cents:
            return {"error": "No matching pending confirmation for this invoice and amount."}
        if p["expires_at"] < datetime.now(timezone.utc):
            return {"error": "Confirmation expired. Request a new eligibility check."}
        check = eligibility(chat_id, invoice_id, db)
        if "error" in check:
            return check
        if (
            check["customer_id"] != p["customer_id"]
            or check["invoice"]["amount_cents"] != amount_cents
            or check["rule"]["version"] != p["policy_version"]
        ):
            return {"error": "Eligibility changed; request a new confirmation."}
        receipt_id = "mock_" + uuid4().hex[:12]
        row = db.execute(
            """INSERT INTO refund_receipts(receipt_id,confirmation_token,invoice_id,customer_id,amount_cents,policy_version,chat_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(invoice_id) DO NOTHING RETURNING receipt_id""",
            (
                receipt_id,
                token,
                invoice_id,
                p["customer_id"],
                amount_cents,
                p["policy_version"],
                chat_id,
            ),
        ).fetchone()
        if not row:
            return {"error": "This invoice was already refunded."}
        db.execute("DELETE FROM refund_pending WHERE invoice_id=%s", (invoice_id,))
        message = f"Mock refund issued for {invoice_id}: ${amount_cents / 100:.2f} USD. Receipt: {receipt_id}. No real money moved."
        add_message(chat_id, "assistant", message, db)
        return {
            "status": "issued",
            "receipt_id": receipt_id,
            "invoice_id": invoice_id,
            "amount_cents": amount_cents,
            "policy_version": p["policy_version"],
            "currency": "USD",
            "mock": True,
            "message": message,
        }


def create_ticket(chat_id, category, reason):
    with connect() as db:
        chat = db.execute(
            "SELECT escalation FROM chats WHERE id=%s FOR UPDATE", (chat_id,)
        ).fetchone()
        customer_id = verified_customer(chat_id, db)
        if not customer_id:
            return {"error": "Resolve the fictional customer before creating a ticket."}
        messages = db.execute(
            "SELECT role,content FROM messages WHERE chat_id=%s ORDER BY id DESC LIMIT 12",
            (chat_id,),
        ).fetchall()
        latest = next((m["content"].lower() for m in messages if m["role"] == "user"), "")
        if re.search(
            r"\b(?:do not|don't|no|without)\b.{0,35}\b(?:ticket|escalat|human|agent)\b", latest
        ):
            return {"error": "The customer did not authorize a ticket."}
        explicit = re.search(
            r"\b(?:create|open|file|raise)\s+(?:a\s+)?(?:human\s+)?(?:support\s+)?ticket\b|\b(?:want|need|talk to|speak to|connect me with|transfer me to)\s+(?:a\s+)?(?:human|agent|representative)\b|\bescalate\s+(?:this|my issue|my question)\b",
            latest,
        )
        previous = next((m["content"].lower() for m in messages if m["role"] == "assistant"), "")
        accepted = bool(
            re.match(r"(?i)^\s*(yes|yeah|yep|yes please|please do|go ahead)\b", latest)
            and not re.search(
                r"(?i)\b(?:do not|don't|no|never mind|not now)\b.{0,20}\b(?:ticket|escalat|human)\b",
                latest,
            )
            and re.search(
                r"(?i)(would you like|shall i|want me to|i can).{0,120}(ticket|human|support team)",
                previous,
            )
        )
        if not explicit and not accepted:
            return {"error": "Ask whether the customer wants a human support ticket first."}
        if len(reason.strip()) < 10:
            return {"error": "Clarify the issue before creating a ticket."}
        existing = db.execute(
            "SELECT * FROM escalation_tickets WHERE chat_id=%s AND status='open'", (chat_id,)
        ).fetchone()
        if existing:
            return {**existing, "replayed": True}
        state = chat["escalation"] or {
            "status": "eligible",
            "reason": "Customer explicitly requested human support",
            "evidence": [],
        }
        context = {"escalation": state, "recent_messages": list(reversed(messages))}
        ticket = db.execute(
            """INSERT INTO escalation_tickets(ticket_id,chat_id,customer_id,category,reason,context)
            VALUES (%s,%s,%s,%s,%s,%s) RETURNING ticket_id,customer_id,category,reason,status""",
            (
                "TKT-" + uuid4().hex[:8].upper(),
                chat_id,
                customer_id,
                category[:60],
                reason[:1000],
                Jsonb(context),
            ),
        ).fetchone()
        db.execute(
            "UPDATE chats SET escalation=%s WHERE id=%s",
            (
                Jsonb({**state, "status": "ticket_created", "ticket_id": ticket["ticket_id"]}),
                chat_id,
            ),
        )
        return ticket
