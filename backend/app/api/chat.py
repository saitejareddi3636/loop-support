"""Chat HTTP and SSE routes. LangGraph and tools live in separate modules."""

import asyncio
import json
import logging

import anyio
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.runner import run_turn
from app.config import configured_api_key
from app.refunds import confirm_refund, pending_refund
from app.storage import create_chat, get_chat, list_chats
from app.security import owner, consume_quota
from app.db import connect

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chats")
# Fallback when lifespan task group is unavailable (unit tests).
_turn_tasks: set[asyncio.Task] = set()


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    request_id: str = Field(min_length=8, max_length=100)


class RefundConfirmation(BaseModel):
    token: str = Field(max_length=100)
    invoice_id: str = Field(max_length=60)
    amount_cents: int = Field(gt=0)


def event(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _spawn_turn(produce, turn_group=None):
    """Run produce outside the HTTP request cancel scope when possible."""
    if turn_group is not None:
        turn_group.start_soon(produce)
        return None
    task = asyncio.create_task(produce())
    _turn_tasks.add(task)
    task.add_done_callback(_turn_tasks.discard)
    return task


async def iter_turn_events(
    chat_id: str, prompt: str, key: str, request_id: str, turn_group=None
):
    """Yield run_turn events and finish Postgres writes even if the SSE client disconnects."""
    queue: asyncio.Queue = asyncio.Queue()

    async def produce():
        try:
            async for update in run_turn(chat_id, prompt, key, request_id):
                await queue.put(("event", update))
        except Exception as error:
            await queue.put(("error", error))
        finally:
            await queue.put(("done", None))

    task = _spawn_turn(produce, turn_group)
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "done":
                break
            if kind == "error":
                raise payload
            yield payload
    finally:
        # Lifespan-hosted producers keep running after disconnect; fallback tasks need a wait.
        if task is not None and not task.done():
            with anyio.CancelScope(shield=True):
                await task


@router.get("")
def chats(session: str = Depends(owner)):
    return list_chats(session)


@router.post("")
def new_chat(session: str = Depends(owner)):
    return create_chat(session)


@router.get("/{chat_id}")
def chat(chat_id: str, session: str = Depends(owner)):
    result = get_chat(chat_id, session)
    if result is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return result


@router.post("/{chat_id}/messages")
async def send_message(
    chat_id: str, body: ChatInput, request: Request, session: str = Depends(owner)
):
    consume_quota(session)
    key = configured_api_key()
    if key is None:
        raise HTTPException(status_code=503, detail="Set GEMINI_API_KEY in .env")
    existing = get_chat(chat_id, session)
    if existing is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    prompt = body.message.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="Message cannot be empty")
    turn_group = getattr(request.app.state, "turn_group", None)

    async def stream():
        try:
            async for update in iter_turn_events(
                chat_id, prompt, key, body.request_id, turn_group=turn_group
            ):
                yield event(update)
        except asyncio.CancelledError:
            # Client navigated away (chat↔voice). Persistence continues in turn_group.
            raise
        except Exception:
            logger.exception("Chat stream failed")
            yield event(
                {
                    "type": "error",
                    "message": "The request could not finish. Your conversation is saved. Please retry with a new request or ask for human support.",
                }
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{chat_id}/refund-confirmations")
def confirm(chat_id: str, body: RefundConfirmation, session: str = Depends(owner)):
    if get_chat(chat_id, session) is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    result = confirm_refund(chat_id, body.token, body.invoice_id, body.amount_cents)
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return result


@router.get("/{chat_id}/refund-pending")
def pending(chat_id: str, session: str = Depends(owner)):
    if get_chat(chat_id, session) is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return pending_refund(chat_id)


@router.get("/{chat_id}/traces")
def traces(chat_id: str, session: str = Depends(owner)):
    if not get_chat(chat_id, session):
        raise HTTPException(404, "Chat not found")
    with connect() as db:
        return db.execute(
            "SELECT * FROM traces WHERE chat_id=%s ORDER BY created_at DESC LIMIT 20", (chat_id,)
        ).fetchall()
