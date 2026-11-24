"""Reproducible fictional assessment records; never resets receipts or history."""

from datetime import date, timedelta
from app.db import connect

ANCHOR = date(2026, 11, 21)
NAMES = [
    "Alex Morgan",
    "Jordan Lee",
    "Sam Patel",
    "Taylor Chen",
    "Riley Brooks",
    "Casey Rivera",
    "Morgan Bell",
    "Jamie Park",
    "Avery Reed",
    "Cameron Ellis",
    "Drew Walker",
    "Quinn Shah",
    "Robin Scott",
    "Skyler Diaz",
    "Parker Kim",
    "Sage Wilson",
    "Rowan Ahmed",
    "Reese Martin",
    "Emery Clark",
    "Finley Ross",
]


def seed():
    with connect() as db:
        for i, name in enumerate(NAMES, 1):
            cid, aid, sid, kid = (f"{prefix}_{1000 + i}" for prefix in ("cus", "acc", "sub", "ctr"))
            tier = (
                ["Free", "Pro", "Business", "Enterprise"][(i - 1) % 4]
                if i > 2
                else ["Pro", "Free"][i - 1]
            )
            status = ["active", "trial", "suspended", "cancelled", "expired"][(i - 1) // 4]
            contract = (
                "active"
                if status in ("active", "trial", "suspended")
                else "terminated"
                if status == "cancelled"
                else "expired"
            )
            cycle = "annual" if tier == "Enterprise" or i % 5 == 0 else "monthly"
            restrictions = (
                ["billing_hold"]
                if status == "suspended"
                else ["read_only"]
                if status == "expired"
                else []
            )
            db.execute(
                "INSERT INTO customers VALUES (%s,%s,%s,TRUE) ON CONFLICT DO NOTHING",
                (cid, name.lower().replace(" ", ".") + "@example.test", name),
            )
            db.execute(
                "INSERT INTO accounts VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (aid, cid, restrictions),
            )
            db.execute(
                "INSERT INTO subscriptions VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (sid, aid, tier, status, cycle, ANCHOR - timedelta(days=400)),
            )
            db.execute(
                "INSERT INTO contracts VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    kid,
                    aid,
                    contract,
                    ANCHOR - timedelta(days=400),
                    ANCHOR + timedelta(days=300)
                    if contract == "active"
                    else ANCHOR - timedelta(days=20),
                ),
            )
            for j in range(4):
                inv_id = f"INV-{i * 1000 + j + 1}"
                days = [6, 51, 20, 95][j]
                amount = {"Free": 1200, "Pro": 2900, "Business": 14900, "Enterprise": 240000}[tier]
                inv_status = ["paid", "unpaid", "overdue", "refunded", "disputed", "cancelled"][
                    (i + j - 1) % 6
                ]
                desc = f"Loopp {tier} {cycle} subscription"
                if i == 1:
                    inv_id = f"INV-100{j + 1}"
                    inv_status = "paid" if j < 3 else "disputed"
                    days = [20, 51, 6, 95][j]
                    amount = 1900 if j == 2 else 2900
                    desc = (
                        "Loopp Pro plan adjustment" if j == 2 else "Loopp Pro monthly subscription"
                    )
                if i == 3 and j in (0, 1):
                    inv_status = "paid"
                    days = 6 if j == 0 else 3
                    amount = 14900 if j == 0 else 6900
                    desc = (
                        "Loopp Business monthly subscription"
                        if j == 0
                        else "Loopp Business plan adjustment"
                    )
                if i == 4 and j == 0:
                    inv_status = "paid"
                if i == 2 and j == 0:
                    amount, days, inv_status, desc = (
                        1200,
                        17,
                        "paid",
                        "Loopp Starter monthly subscription (legacy plan)",
                    )
                db.execute(
                    "INSERT INTO invoices VALUES (%s,%s,%s,%s,%s,%s,%s,'USD',%s,%s,%s) ON CONFLICT DO NOTHING",
                    (
                        inv_id,
                        cid,
                        aid,
                        sid,
                        kid,
                        ANCHOR - timedelta(days=days),
                        amount,
                        inv_status,
                        desc,
                        cycle,
                    ),
                )
        db.execute(
            """INSERT INTO invoices(invoice_id,customer_id,account_id,subscription_id,contract_id,date,amount_cents,currency,status,description,billing_cycle)
            SELECT 'INV-3005',customer_id,account_id,subscription_id,contract_id,%s,8900,'USD','paid','Loopp Business additional workspace monthly invoice (Loom demo)','monthly'
            FROM invoices WHERE invoice_id='INV-3002' ON CONFLICT DO NOTHING""",
            (ANCHOR,),
        )
        print(
            "Seeded",
            db.execute("SELECT count(*) AS n FROM customers").fetchone()["n"],
            "fictional customers and",
            db.execute("SELECT count(*) AS n FROM invoices").fetchone()["n"],
            "invoices",
        )


if __name__ == "__main__":
    seed()
