"""Chat HTTP and SSE routes. LangGraph and tools live in separate modules."""

import json
import logging

from fastapi import APIRouter, HTTPException, Depends
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


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    request_id: str = Field(min_length=8, max_length=100)


class RefundConfirmation(BaseModel):
    token: str = Field(max_length=100)
    invoice_id: str = Field(max_length=60)
    amount_cents: int = Field(gt=0)


def event(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


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
async def send_message(chat_id: str, body: ChatInput, session: str = Depends(owner)):
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

    async def stream():
        try:
            async for update in run_turn(chat_id, prompt, key, body.request_id):
                yield event(update)
        except Exception as error:
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
