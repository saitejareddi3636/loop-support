"""One-time, read-only SQLite import. Original file is never modified.
The recovery capability is stored locally with mode 0600 and is never printed.
"""

import sqlite3
from pathlib import Path
from app.db import connect
from app.storage import new_session, session_id

SOURCE = Path(__file__).with_name("loopp.sqlite3")


def main():
    if not SOURCE.exists():
        raise SystemExit("No legacy SQLite file found")
    source = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    # Stable owner makes repeated imports idempotent; retrieval still requires the secret cookie.
    with connect() as db:
        prior = db.execute(
            "SELECT 1 FROM schema_migrations WHERE name='legacy-sqlite-import'"
        ).fetchone()
        if prior:
            raise SystemExit("Legacy data already imported. No changes made.")
        db.execute("INSERT INTO schema_migrations(name) VALUES ('legacy-sqlite-import')")
        db.execute("INSERT INTO demo_sessions(id) VALUES ('legacy-import') ON CONFLICT DO NOTHING")
        for row in source.execute("SELECT * FROM chats"):
            db.execute(
                "INSERT INTO chats(id,session_id,title) VALUES (%s,'legacy-import',%s) ON CONFLICT DO NOTHING",
                (row["id"], row["title"]),
            )
        for row in source.execute("SELECT * FROM messages ORDER BY id"):
            db.execute(
                "INSERT INTO messages(chat_id,role,content) VALUES (%s,%s,%s)",
                (row["chat_id"], row["role"], row["content"]),
            )
        for table, columns in [
            ("verified_customers", ["chat_id", "customer_id"]),
            (
                "refund_receipts",
                [
                    "receipt_id",
                    "confirmation_token",
                    "invoice_id",
                    "customer_id",
                    "amount_cents",
                    "policy_version",
                    "chat_id",
                ],
            ),
            (
                "escalation_tickets",
                ["ticket_id", "chat_id", "customer_id", "category", "reason", "status"],
            ),
        ]:
            for row in source.execute(f"SELECT * FROM {table}"):
                db.execute(
                    f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join(['%s'] * len(columns))}) ON CONFLICT DO NOTHING",
                    tuple(row[c] for c in columns),
                )
    token = new_session()
    owner = session_id(token)
    with connect() as db:
        db.execute("UPDATE chats SET session_id=%s WHERE session_id='legacy-import'", (owner,))
    source.close()
    # Keep capability in a local restricted file, never include it in logs or tracked files.
    import os

    target = Path(__file__).with_name(".legacy-session")
    fd = os.open(target, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    print(
        "Imported history and receipts. Private recovery capability saved to backend/.legacy-session (mode 0600)."
    )
    print(
        "The local /api/session/recover endpoint accepts this capability as the token. Original SQLite remains unchanged."
    )


if __name__ == "__main__":
    main()
