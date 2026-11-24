"""Durable PostgreSQL application state. No schema DDL on request paths."""

import hashlib
import re
import secrets
from datetime import datetime, timezone
from uuid import uuid4
from psycopg.types.json import Jsonb
from app.db import connect
from app.identifiers import invoice_ids, selected_invoice_ids, partial_invoice_reference


def session_id(token):
    return hashlib.sha256(token.encode()).hexdigest()


def session_valid(token):
    if not token or len(token) > 128:
        return False
    with connect() as db:
        return bool(
            db.execute(
                "SELECT 1 FROM demo_sessions WHERE id=%s AND expires_at>now()", (session_id(token),)
            ).fetchone()
        )


def new_session():
    token = secrets.token_urlsafe(32)
    with connect() as db:
        db.execute("INSERT INTO demo_sessions(id) VALUES (%s)", (session_id(token),))
    return token


def create_chat(owner):
    with connect() as db:
        return db.execute(
            "INSERT INTO chats(id,session_id) VALUES (%s,%s) RETURNING id,title,created_at",
            (str(uuid4()), owner),
        ).fetchone()


def list_chats(owner):
    with connect() as db:
        return db.execute(
            "SELECT id,title,created_at FROM chats WHERE session_id=%s ORDER BY created_at DESC",
            (owner,),
        ).fetchall()


def get_chat(chat_id, owner=None):
    with connect() as db:
        chat = db.execute(
            "SELECT id,title,created_at,escalation FROM chats WHERE id=%s"
            + (" AND session_id=%s" if owner else ""),
            (chat_id, owner) if owner else (chat_id,),
        ).fetchone()
        if chat:
            chat["refund_receipts"] = db.execute(
                "SELECT r.receipt_id,r.invoice_id,r.amount_cents,i.currency FROM refund_receipts r JOIN invoices i USING(invoice_id) WHERE r.chat_id=%s ORDER BY r.created_at",
                (chat_id,),
            ).fetchall()
            chat["messages"] = db.execute(
                "SELECT id,role,content FROM messages WHERE chat_id=%s ORDER BY id", (chat_id,)
            ).fetchall()
            # Mode switches hydrate from Postgres; expose in-flight turns so clients can wait.
            chat["active_turn"] = bool(
                db.execute(
                    "SELECT 1 FROM turn_requests WHERE chat_id=%s AND status='running' LIMIT 1",
                    (chat_id,),
                ).fetchone()
                or db.execute(
                    "SELECT 1 FROM voice_requests WHERE chat_id=%s AND status='running' LIMIT 1",
                    (chat_id,),
                ).fetchone()
            )
        return chat


def add_message(chat_id, role, content, connection=None):
    def save(db):
        db.execute(
            "INSERT INTO messages(chat_id,role,content) VALUES (%s,%s,%s)", (chat_id, role, content)
        )
        if role == "user":
            db.execute(
                "UPDATE chats SET title=%s WHERE id=%s AND title='New chat'",
                (content[:55], chat_id),
            )

    if connection:
        save(connection)
    else:
        with connect() as db:
            save(db)


def set_verified_customer(chat_id, customer_id):
    with connect() as db:
        db.execute("SELECT id FROM chats WHERE id=%s FOR UPDATE", (chat_id,))
        existing = db.execute(
            "SELECT customer_id FROM verified_customers WHERE chat_id=%s FOR UPDATE", (chat_id,)
        ).fetchone()
        if existing and existing["customer_id"] != customer_id:
            raise ValueError("Start a new conversation to switch the demo customer")
        db.execute(
            "INSERT INTO verified_customers VALUES (%s,%s) ON CONFLICT DO NOTHING",
            (chat_id, customer_id),
        )


def verified_customer(chat_id, connection=None):
    def lookup(db):
        row = db.execute(
            "SELECT customer_id FROM verified_customers WHERE chat_id=%s", (chat_id,)
        ).fetchone()
        return row["customer_id"] if row else None

    if connection:
        return lookup(connection)
    with connect() as db:
        return lookup(db)


def offer_escalation(chat_id, reason, evidence=None):
    state = {"status": "eligible", "reason": reason, "evidence": evidence or []}
    with connect() as db:
        db.execute("UPDATE chats SET escalation=%s WHERE id=%s", (Jsonb(state), chat_id))
    return state


def reserve_voice_request(chat_id, call_id):
    with connect() as db:
        row = db.execute(
            "INSERT INTO voice_requests(chat_id,call_id,status) VALUES (%s,%s,'running') ON CONFLICT DO NOTHING RETURNING call_id",
            (chat_id, call_id),
        ).fetchone()
        old = db.execute(
            "SELECT status,answer,updated_at FROM voice_requests WHERE chat_id=%s AND call_id=%s",
            (chat_id, call_id),
        ).fetchone()
        if (
            not row
            and old["status"] == "running"
            and (datetime.now(timezone.utc) - old["updated_at"]).total_seconds() > 180
        ):
            return (
                False,
                "The previous request was interrupted. Check the conversation before starting a new request; nothing will be replayed automatically.",
            )
        return bool(row), old["answer"] if old["status"] in ("complete", "failed") else None


def finish_voice_request(chat_id, call_id, answer, failed=False):
    with connect() as db:
        db.execute(
            "UPDATE voice_requests SET status=%s,answer=%s,updated_at=now() WHERE chat_id=%s AND call_id=%s",
            (
                "complete" if answer and not failed else "failed",
                answer
                or "The earlier request failed. Please make a new request; no automatic replay was attempted.",
                chat_id,
                call_id,
            ),
        )


def record_trace(trace_id, chat_id, events):
    with connect() as db:
        db.execute(
            "INSERT INTO traces VALUES (%s,%s,%s,now()) ON CONFLICT(trace_id) DO UPDATE SET events=EXCLUDED.events",
            (trace_id, chat_id, Jsonb(events)),
        )


def update_context(chat_id, values):
    with connect() as db:
        db.execute("UPDATE chats SET context=context || %s WHERE id=%s", (Jsonb(values), chat_id))


def conversation_context(chat_id):
    from app.data import account_by_customer

    customer_id = verified_customer(chat_id)
    account = account_by_customer(customer_id) if customer_id else None
    with connect() as db:
        row = db.execute("SELECT context FROM chats WHERE id=%s", (chat_id,)).fetchone()
        receipts = db.execute(
            "SELECT r.receipt_id,r.invoice_id,r.amount_cents,i.currency,r.created_at FROM refund_receipts r JOIN invoices i USING(invoice_id) WHERE r.chat_id=%s ORDER BY r.created_at",
            (chat_id,),
        ).fetchall()
        tickets = db.execute(
            "SELECT ticket_id,category,status,reason FROM escalation_tickets WHERE chat_id=%s ORDER BY created_at",
            (chat_id,),
        ).fetchall()
    return {
        "account": account,
        "references": row["context"] if row else {},
        "refund_receipts": receipts,
        "open_tickets": [t for t in tickets if t.get("status") == "open"],
        "tickets": tickets,
    }


def user_invoice_ids(text):
    return invoice_ids(text)


def remember_invoice_choice(chat_id, query):
    """Only original user input can establish an explicit invoice selection."""
    references = conversation_context(chat_id)["references"]
    ids = selected_invoice_ids(query, references)
    if ids:
        update_context(
            chat_id, {"user_selected_invoice_id": next(iter(ids)) if len(ids) == 1 else None}
        )
    elif partial_invoice_reference(query):
        values = {"user_selected_invoice_id": None}
        if re.search(r"(?i)\brefund\b", query):
            values["awaiting_refund_invoice_choice"] = True
        update_context(chat_id, values)
