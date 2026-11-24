"""Shared turn coordinator: cross-process serialization, replay safety, persisted traces."""

import asyncio
import hashlib
import json
import time
import logging
from uuid import uuid4
from langchain_core.messages import AIMessage, HumanMessage
from app.db import connect
from app.storage import (
    get_chat,
    add_message,
    record_trace,
    offer_escalation,
    conversation_context,
    remember_invoice_choice,
)
from app.resilience import classify, RetryLater
from app.agent.graph import build_graph
from app.agent.conversation import social_reply


async def run_turn(chat_id, query, api_key, request_id=None):
    trace_id = uuid4().hex
    started = time.perf_counter()
    events = []
    answer = ""
    emitted = ""
    request_id = request_id or uuid4().hex
    # Session-level advisory lock spans awaits without blocking the event loop.
    lock = await asyncio.to_thread(connect)
    key = int.from_bytes(hashlib.sha256(chat_id.encode()).digest()[:8], "big", signed=True)
    try:
        acquired = lock.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (key,)).fetchone()[
            "acquired"
        ]
        lock.commit()
        if not acquired:
            yield {
                "type": "error",
                "category": "conversation_busy",
                "message": "Another request is still running in this conversation. Please wait.",
            }
            return
        existing = await asyncio.to_thread(get_chat, chat_id)
        if not existing:
            raise ValueError("Chat not found")
        with connect() as db:
            prior = db.execute(
                "SELECT status,answer FROM turn_requests WHERE chat_id=%s AND request_id=%s",
                (chat_id, request_id),
            ).fetchone()
            if prior:
                if prior["status"] == "complete":
                    yield {"type": "delta", "text": prior["answer"], "replayed": True}
                    yield {"type": "complete", "answer": prior["answer"], "replayed": True}
                    return
                yield {
                    "type": "error",
                    "message": "This request was already attempted. Check the saved conversation before starting a new request.",
                    "category": "duplicate_request",
                }
                return
            db.execute(
                "INSERT INTO turn_requests(chat_id,request_id,status) VALUES (%s,%s,'running')",
                (chat_id, request_id),
            )
        await asyncio.to_thread(add_message, chat_id, "user", query)
        await asyncio.to_thread(remember_invoice_choice, chat_id, query)
        history = [
            HumanMessage(content=m["content"])
            if m["role"] == "user"
            else AIMessage(content=m["content"])
            for m in existing["messages"][-40:]
        ]
        context = await asyncio.to_thread(conversation_context, chat_id)
        answer = social_reply(query, existing["messages"], context)
        if answer:
            events.append(
                {
                    "type": "conversation_reply",
                    "trace_id": trace_id,
                    "reason": "Greeting or conversational introduction; no policy facts asserted.",
                }
            )
        else:
            graph = build_graph(api_key, chat_id, trace_id)
            async with asyncio.timeout(150):
                async for payload in graph.astream(
                    {
                        "messages": [*history, HumanMessage(content=query)],
                        "results": [],
                        "context": context,
                    },
                    stream_mode="custom",
                    config={"recursion_limit": 20},
                ):
                    event = {
                        **payload,
                        "trace_id": trace_id,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    }
                    if event["type"] == "final_answer":
                        answer = event["answer"]
                        continue
                    if event["type"] == "delta":
                        emitted += event["text"]
                    if event["type"] == "answer_reset":
                        emitted = ""
                    events.append(event)
                    yield event
        if not answer.strip():
            raise RuntimeError("No final answer")
        if emitted != answer:
            if emitted:
                yield {"type": "answer_reset"}
            # Evidence is emitted by passage, not misrepresented as model token streaming.
            for passage in answer.split("\n\n"):
                yield {
                    "type": "delta",
                    "text": passage + "\n\n",
                    "stream_origin": "conversation"
                    if any(e["type"] == "conversation_reply" for e in events)
                    else "validated_evidence",
                }
        with connect() as db:
            add_message(chat_id, "assistant", answer, db)
            db.execute(
                "UPDATE turn_requests SET status='complete',answer=%s,updated_at=now() WHERE chat_id=%s AND request_id=%s",
                (answer, chat_id, request_id),
            )
        complete = {
            "type": "complete",
            "answer": answer,
            "trace_id": trace_id,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
        events.append(complete)
        yield complete
    except (Exception, asyncio.CancelledError) as error:
        category, _, _ = classify(error)
        message = (
            error.user_message
            if isinstance(error, RetryLater)
            else "The request could not finish. Your conversation is saved. Would you like a human support ticket?"
        )
        with connect() as db:
            db.execute(
                "UPDATE turn_requests SET status='failed',updated_at=now() WHERE chat_id=%s AND request_id=%s AND status='running'",
                (chat_id, request_id),
            )
        if not isinstance(error, RetryLater):
            await asyncio.to_thread(offer_escalation, chat_id, message)
        finished = {
            e.get("tool_id")
            for e in events
            if e["type"] in ("tool_completed", "tool_failed", "tool_blocked")
        }
        for start in [
            e for e in events if e["type"] == "tool_started" and e.get("tool_id") not in finished
        ]:
            ended = {
                "type": "tool_failed",
                "name": start["name"],
                "tool_id": start.get("tool_id"),
                "trace_id": trace_id,
                "duration_ms": round((time.perf_counter() - started) * 1000) - start["elapsed_ms"],
                "result": {
                    "error": "The request ended before tool completion was observed. Check persisted state before retrying.",
                    "error_category": category,
                    "outcome": "error",
                },
            }
            events.append(ended)
            if not isinstance(error, asyncio.CancelledError):
                yield ended
        failure = {"type": "error", "category": category, "message": message, "trace_id": trace_id}
        if isinstance(error, RetryLater):
            failure.update(state="retry_later", retry_after_seconds=error.retry_after_seconds)
        events.append(failure)
        if isinstance(error, asyncio.CancelledError):
            raise
        yield failure
        if not isinstance(error, RetryLater):
            yield {"type": "escalation_offered", "message": message, "trace_id": trace_id}
    finally:
        try:
            if events:
                await asyncio.to_thread(
                    record_trace, trace_id, chat_id, json.loads(json.dumps(events, default=str))
                )
        except Exception:
            logging.getLogger(__name__).exception("Could not persist trace %s", trace_id)
        finally:
            try:
                lock.execute("SELECT pg_advisory_unlock(%s)", (key,))
            finally:
                lock.close()
