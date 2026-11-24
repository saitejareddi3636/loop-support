"""Chat↔voice conversation sync: persistence across disconnect and shared restore."""

import asyncio
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.api.chat import iter_turn_events


class IterTurnCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_consumer_cancel_still_finishes_turn_producer(self):
        finished = asyncio.Event()

        async def fake_run_turn(chat_id, query, api_key, request_id=None):
            yield {"type": "delta", "text": "partial"}
            await asyncio.sleep(0.2)
            finished.set()
            yield {"type": "complete", "answer": "done"}

        with patch("app.api.chat.run_turn", fake_run_turn):
            stream = iter_turn_events("chat", "hello", "key", "request-sync-1")
            self.assertEqual((await stream.__anext__())["type"], "delta")
            await stream.aclose()
            await asyncio.wait_for(finished.wait(), timeout=1.0)


@unittest.skipUnless(
    os.getenv("RUN_DB_TESTS") == "1", "Set RUN_DB_TESTS=1 for isolated PostgreSQL tests"
)
class ConversationSyncDBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        from migrate import migrate
        from seed_data import seed as seed_records
        from app.policy import seed
        from unittest.mock import patch as mock_patch

        cls.original_url = os.environ["DATABASE_URL"]
        cls.dbname = "loopp_sync_" + uuid4().hex[:10]
        cls.admin = psycopg.connect(cls.original_url, autocommit=True)
        cls.admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.dbname)))
        os.environ["DATABASE_URL"] = make_conninfo(cls.original_url, dbname=cls.dbname)
        migrate()
        seed_records()
        with mock_patch("app.policy.embed", return_value=[1.0] + [0.0] * 767):
            seed()

    @classmethod
    def tearDownClass(cls):
        from psycopg import sql

        os.environ["DATABASE_URL"] = cls.original_url
        cls.admin.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(cls.dbname))
        )
        cls.admin.close()

    def setUp(self):
        from app import storage

        self.owner = storage.session_id(storage.new_session())
        self.chat = storage.create_chat(self.owner)["id"]

    def test_active_turn_flag_and_shared_restore_chat_then_voice(self):
        from app import storage
        from app.db import connect
        from app.main import execute_voice_request
        from app.refunds import confirm_refund, stage_refund

        storage.set_verified_customer(self.chat, "cus_1003")
        storage.add_message(self.chat, "user", "Which invoices qualify?")
        storage.add_message(self.chat, "assistant", "INV-3005 is eligible.")
        storage.record_trace("trace-chat-1", self.chat, [{"type": "complete", "answer": "INV-3005 is eligible."}])

        staged = stage_refund(self.chat, "INV-3005")
        self.assertEqual(staged["status"], "confirmation_required")
        confirmed = confirm_refund(
            self.chat, staged["token"], staged["invoice_id"], staged["amount_cents"]
        )
        self.assertIn("receipt_id", confirmed)

        with connect() as db:
            db.execute(
                "INSERT INTO turn_requests(chat_id,request_id,status) VALUES (%s,%s,'running')",
                (self.chat, "running-sync-1"),
            )
        mid = storage.get_chat(self.chat, self.owner)
        self.assertTrue(mid["active_turn"])
        with connect() as db:
            db.execute(
                "UPDATE turn_requests SET status='complete',answer=%s WHERE chat_id=%s AND request_id=%s",
                ("done", self.chat, "running-sync-1"),
            )

        async def fake_run_turn(chat_id, query, api_key, request_id=None):
            storage.add_message(chat_id, "user", query)
            storage.add_message(chat_id, "assistant", "Voice saw the prior receipt.")
            storage.record_trace(
                "trace-voice-1",
                chat_id,
                [{"type": "complete", "answer": "Voice saw the prior receipt."}],
            )
            yield {"type": "complete", "answer": "Voice saw the prior receipt."}

        async def emit(_event):
            return None

        async def run():
            with patch("app.main.run_turn", fake_run_turn):
                return await execute_voice_request(
                    self.chat, "voice-call-1", "What was refunded?", "fake", emit
                )

        answer = asyncio.run(run())
        self.assertEqual(answer, "Voice saw the prior receipt.")

        restored = storage.get_chat(self.chat, self.owner)
        self.assertFalse(restored["active_turn"])
        contents = [m["content"] for m in restored["messages"]]
        self.assertIn("Which invoices qualify?", contents)
        self.assertIn("Voice saw the prior receipt.", contents)
        self.assertTrue(any("INV-3005" in content for content in contents))
        self.assertEqual(len(restored["refund_receipts"]), 1)
        self.assertEqual(restored["refund_receipts"][0]["invoice_id"], "INV-3005")
        with connect() as db:
            traces = db.execute(
                "SELECT trace_id FROM traces WHERE chat_id=%s ORDER BY created_at",
                (self.chat,),
            ).fetchall()
        self.assertGreaterEqual(len(traces), 2)

    def test_voice_then_chat_same_chat_id_restores_messages(self):
        from app import storage
        from app.main import execute_voice_request

        async def fake_run_turn(chat_id, query, api_key, request_id=None):
            storage.add_message(chat_id, "user", query)
            storage.add_message(chat_id, "assistant", "Account is active.")
            storage.record_trace(
                "trace-voice-first",
                chat_id,
                [
                    {"type": "tool_completed", "name": "get_account"},
                    {"type": "complete", "answer": "Account is active."},
                ],
            )
            yield {"type": "complete", "answer": "Account is active."}

        async def emit(_event):
            return None

        async def voice_turn():
            with patch("app.main.run_turn", fake_run_turn):
                return await execute_voice_request(
                    self.chat, "voice-first", "What is my account status?", "fake", emit
                )

        self.assertEqual(asyncio.run(voice_turn()), "Account is active.")

        # Chat mode opens the same chat id and must see voice messages + traces.
        restored = storage.get_chat(self.chat, self.owner)
        self.assertEqual(
            [(m["role"], m["content"]) for m in restored["messages"]],
            [("user", "What is my account status?"), ("assistant", "Account is active.")],
        )
        from app.db import connect

        with connect() as db:
            traces = db.execute(
                "SELECT events FROM traces WHERE chat_id=%s", (self.chat,)
            ).fetchall()
        self.assertTrue(traces)
        self.assertTrue(any(e.get("type") == "tool_completed" for e in traces[0]["events"]))
