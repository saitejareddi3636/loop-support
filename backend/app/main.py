"""FastAPI application and Gemini Live voice bridge."""

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from threading import Lock

import anyio
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types

from app.agent.runner import run_turn
from app.api.chat import router as chat_router
from app.config import configured_api_key, validate_runtime_config
from app.db import connect
from app.security import COOKIE, check_origin, consume_quota, limit_session_creation
from app.storage import (
    create_chat,
    finish_voice_request,
    get_chat,
    new_session,
    reserve_voice_request,
    session_id,
    session_valid,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    validate_runtime_config()
    # Request cancel scopes must not kill in-flight chat turns during mode switches.
    async with anyio.create_task_group() as turn_group:
        app.state.turn_group = turn_group
        yield


app = FastAPI(title="Loopp support prototype", lifespan=lifespan)


class VoiceSlots:
    """Bound concurrent Live sockets in this single-worker API process."""

    def __init__(self):
        self.lock = Lock()
        self.total = 0
        self.sessions = {}

    def acquire(self, owner):
        per_session = max(1, int(os.getenv("VOICE_MAX_PER_SESSION", "1")))
        global_limit = max(1, int(os.getenv("VOICE_MAX_GLOBAL", "3")))
        with self.lock:
            if self.total >= global_limit or self.sessions.get(owner, 0) >= per_session:
                return False
            self.total += 1
            self.sessions[owner] = self.sessions.get(owner, 0) + 1
            return True

    def release(self, owner):
        with self.lock:
            self.total -= 1
            remaining = self.sessions[owner] - 1
            if remaining:
                self.sessions[owner] = remaining
            else:
                del self.sessions[owner]


voice_slots = VoiceSlots()


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith(("/api/chats", "/api/session")):
        response.headers.setdefault("Cache-Control", "no-store")
    if os.getenv("APP_ENV", "local") == "production" and request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


app.include_router(chat_router)


@app.post("/api/session")
def demo_session(request: Request, response: Response):
    check_origin(request)
    token = request.cookies.get(COOKIE)
    if not session_valid(token):
        limit_session_creation(request)
        token = new_session()
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=os.getenv("APP_ENV", "local") == "production",
        samesite="strict",
        max_age=30 * 86400,
        path="/",
    )
    return {
        "mock_data": True,
        "identity": "Fictional demo session; email lookup is not real authentication.",
    }


@app.post("/api/session/recover")
async def recover_session(request: Request, response: Response):
    check_origin(request)
    if os.getenv("APP_ENV", "local") != "local" or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(404, "Not found")
    body = await request.json()
    token = body.get("token", "")
    if not isinstance(token, str) or not session_valid(token):
        raise HTTPException(403, "Invalid recovery capability")
    response.set_cookie(
        COOKIE, token, httponly=True, samesite="strict", max_age=30 * 86400, path="/"
    )
    return {"recovered": True}


@app.get("/api/ready")
def readiness():
    try:
        with connect() as db:
            db.execute("SELECT 1 FROM schema_migrations LIMIT 1")
            data = db.execute("SELECT count(*) AS n FROM customers").fetchone()["n"]
            chunks = db.execute(
                "SELECT count(*) AS n FROM knowledge_chunks WHERE active"
            ).fetchone()["n"]
        if not data or not chunks:
            raise ValueError("Not seeded")
        return {"status": "ready", "fictional_customers": data, "active_chunks": chunks}
    except Exception:
        raise HTTPException(503, "Database unavailable or migrations/seeds incomplete")


@app.get("/api/health")
def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "model": os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live"),
        "configured": configured_api_key() is not None,
    }


async def execute_voice_request(
    chat_id: str, call_id: str, query: str, api_key: str, emit: Callable[[dict], Awaitable[None]]
) -> str:
    # Survive WebSocket teardown when the UI switches chat↔voice mid-turn.
    with anyio.CancelScope(shield=True):
        created, previous_answer = await asyncio.to_thread(reserve_voice_request, chat_id, call_id)
        if previous_answer:
            return previous_answer
        if not created:
            return "That request is already being processed. Please wait."
        if not query:
            answer = "I could not understand that request. Please repeat it."
            finish_voice_request(chat_id, call_id, answer)
            return answer
        answer = None
        failure_message = None
        await emit({"type": "turn_started", "call_id": call_id})
        try:
            async for update in run_turn(chat_id, query, api_key, "voice:" + call_id):
                if update["type"] == "complete":
                    answer = update["answer"]
                else:
                    if update["type"] == "error":
                        failure_message = update["message"]
                    await emit(
                        {**update, "type": "agent_error"} if update["type"] == "error" else update
                    )
            finish_voice_request(
                chat_id, call_id, answer or failure_message, failed=bool(failure_message)
            )
            return (
                answer or failure_message or "I could not complete that request. Please try again."
            )
        except Exception:
            logger.exception("Voice agent request failed")
            finish_voice_request(chat_id, call_id, None)
            return "I could not complete that request. Please try again."


@app.websocket("/api/voice")
async def voice(websocket: WebSocket) -> None:
    try:
        check_origin(websocket)
        token = websocket.cookies.get(COOKIE)
        valid = await asyncio.to_thread(session_valid, token)
    except Exception:
        valid = False
    if not valid:
        await websocket.close(
            code=1008, reason="Demo session expired. Refresh before reconnecting."
        )
        return
    owner = session_id(token)
    if not voice_slots.acquire(owner):
        await websocket.accept()
        await websocket.send_json(
            {"type": "error", "message": "Voice is busy. Please try again in a minute."}
        )
        await websocket.close(code=1013)
        return
    try:
        await voice_connected(websocket, owner)
    finally:
        voice_slots.release(owner)


async def voice_connected(websocket: WebSocket, owner: str) -> None:
    await websocket.accept()
    chat_id = websocket.query_params.get("chat_id")
    if chat_id and await asyncio.to_thread(get_chat, chat_id, owner) is None:
        await websocket.send_json(
            {"type": "error", "message": "Chat not found. Start a new chat and reconnect."}
        )
        await websocket.close(code=1008)
        return
    if not chat_id:
        chat_id = (await asyncio.to_thread(create_chat, owner))["id"]
    api_key = configured_api_key()
    if api_key is None:
        await websocket.send_json(
            {"type": "error", "message": "Set GEMINI_API_KEY in .env, then restart the backend."}
        )
        await websocket.close(code=1011)
        return

    model = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")
    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta", "timeout": 20000})
    config = {
        "response_modalities": ["AUDIO"],
        "input_audio_transcription": {},
        "output_audio_transcription": {},
        "tools": [
            {
                "function_declarations": [
                    {
                        "name": "support_request",
                        "description": "Handle every customer speech turn through the connected support agent. Pass the complete spoken request, including identifiers.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {"request": {"type": "STRING"}},
                            "required": ["request"],
                        },
                    }
                ]
            }
        ],
        "system_instruction": "You are Loopp's live voice interface for general customer support. For every complete user speech turn, call support_request once with the user's exact spoken request, including prior concern details when they continue a topic. Never answer from your own knowledge. Speak the returned answer faithfully and concisely, including policy citation IDs when present. Do not invent refund offers, eligibility lists, or confirmation requests that are not in the returned answer. Never claim a refund was issued without a receipt. Speak ticket wording exactly; do not add that a live team is reviewing or will follow up. When the answer presents numbered invoice options for a refund choice, speak the option numbers and amounts; full invoice IDs are on screen, so do not read long date-based IDs aloud. When the answer requires confirmation, say the conversation panel has a confirmation card and tell the customer to click 'Confirm invoice and amount'; spoken yes is not confirmation. If email lookup fails, ask them to spell the full address slowly, including dots and domain, or type it in the email box. Audio interruptions are allowed.",
    }
    # Per-socket agent turns; awaited on disconnect so chat↔voice restore sees completed rows.
    socket_voice_tasks: set[asyncio.Task] = set()

    try:
        async with client.aio.live.connect(model=model, config=config) as session:
            await websocket.send_json({"type": "ready", "model": model, "chat_id": chat_id})

            async def browser_to_gemini() -> None:
                while True:
                    message = await asyncio.wait_for(websocket.receive(), timeout=1800)
                    if message["type"] == "websocket.disconnect":
                        return
                    audio = message.get("bytes")
                    if audio is not None:
                        if len(audio) > 65536:
                            await websocket.send_json(
                                {"type": "error", "message": "Audio chunk too large."}
                            )
                            continue
                        await session.send_realtime_input(
                            audio=types.Blob(data=audio, mime_type="audio/pcm;rate=16000")
                        )
                        continue
                    raw = message.get("text")
                    if raw:
                        try:
                            command = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if command.get("type") == "audio_stream_end":
                            await session.send_realtime_input(audio_stream_end=True)
                        elif command.get("type") == "test_text" and isinstance(
                            command.get("text"), str
                        ):
                            await session.send_realtime_input(text=command["text"][:1000])

            async def gemini_to_browser() -> None:
                # receive() ends after each model turn; keep listening for future turns.
                grounded_response = False
                grounded_audio_started = False
                warned_direct_response = False
                while True:
                    async for response in session.receive():
                        if getattr(response, "usage_metadata", None):
                            await websocket.send_json(
                                {
                                    "type": "live_usage",
                                    "usage": response.usage_metadata.model_dump(
                                        mode="json", exclude_none=True
                                    ),
                                    "estimated_cost_usd": None,
                                    "cost_status": "unavailable",
                                    "scope": "provider_reported_live_usage",
                                }
                            )
                        if response.tool_call:
                            function_responses = []
                            for call in response.tool_call.function_calls or []:
                                if call.name != "support_request":
                                    function_responses.append(
                                        types.FunctionResponse(
                                            id=call.id,
                                            name=call.name,
                                            response={"answer": "Unsupported function."},
                                        )
                                    )
                                    continue
                                try:
                                    await asyncio.to_thread(consume_quota, owner)
                                except HTTPException:
                                    await websocket.send_json(
                                        {
                                            "type": "notice",
                                            "message": "Demo request limit reached. Please wait a minute.",
                                        }
                                    )
                                    function_responses.append(
                                        types.FunctionResponse(
                                            id=call.id,
                                            name=call.name,
                                            response={
                                                "answer": "The demo request limit was reached. Please wait a minute."
                                            },
                                        )
                                    )
                                    continue
                                query = str((call.args or {}).get("request", "")).strip()[:4000]

                                async def emit(update: dict) -> None:
                                    try:
                                        await websocket.send_json(update)
                                    except Exception:
                                        pass

                                task = asyncio.create_task(
                                    execute_voice_request(chat_id, call.id, query, api_key, emit)
                                )
                                socket_voice_tasks.add(task)
                                task.add_done_callback(socket_voice_tasks.discard)
                                answer = await asyncio.shield(task)
                                try:
                                    await websocket.send_json(
                                        {"type": "agent_answer", "answer": answer}
                                    )
                                except Exception:
                                    pass
                                function_responses.append(
                                    types.FunctionResponse(
                                        id=call.id, name=call.name, response={"answer": answer}
                                    )
                                )
                            if function_responses:
                                await session.send_tool_response(
                                    function_responses=function_responses
                                )
                                grounded_response = True
                                grounded_audio_started = False
                                warned_direct_response = False
                        content = response.server_content
                        if content is None:
                            continue
                        if content.interrupted:
                            await websocket.send_json({"type": "interrupted"})
                            grounded_response = False
                            grounded_audio_started = False
                            warned_direct_response = False
                        if content.input_transcription and content.input_transcription.text:
                            await websocket.send_json(
                                {
                                    "type": "transcript",
                                    "speaker": "user",
                                    "text": content.input_transcription.text,
                                }
                            )
                        if (
                            grounded_response
                            and content.output_transcription
                            and content.output_transcription.text
                        ):
                            await websocket.send_json(
                                {
                                    "type": "transcript",
                                    "speaker": "assistant",
                                    "text": content.output_transcription.text,
                                }
                            )
                        if content.model_turn:
                            for part in content.model_turn.parts or []:
                                if part.inline_data and part.inline_data.data:
                                    if grounded_response:
                                        await websocket.send_bytes(part.inline_data.data)
                                        grounded_audio_started = True
                                    elif not warned_direct_response:
                                        await websocket.send_json(
                                            {
                                                "type": "notice",
                                                "message": "Please repeat that request so I can check it with the support agent.",
                                            }
                                        )
                                        warned_direct_response = True
                        # The tool-call turn can complete on the same response that
                        # carries the call. Keep the gate open for the spoken answer
                        # that follows send_tool_response.
                        # Gemini can end the function-call turn before starting
                        # the audio turn produced from the function result.
                        if (
                            content.turn_complete
                            and not response.tool_call
                            and (not grounded_response or grounded_audio_started)
                        ):
                            await websocket.send_json({"type": "turn_complete"})
                            grounded_response = False
                            grounded_audio_started = False
                            warned_direct_response = False

            async def expire_session():
                await asyncio.sleep(1800)
                await websocket.send_json(
                    {
                        "type": "notice",
                        "message": "Voice session expired. Reconnect to continue this conversation.",
                    }
                )
                await websocket.close(code=1000)

            tasks = [
                asyncio.create_task(expire_session()),
                asyncio.create_task(browser_to_gemini()),
                asyncio.create_task(gemini_to_browser()),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Gemini Live session failed")
        try:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "Voice disconnected. Your conversation is saved. Reconnect to continue; pending confirmations remain in the conversation.",
                }
            )
        except Exception:
            pass
    finally:
        if socket_voice_tasks:
            await asyncio.gather(
                *[asyncio.shield(task) for task in list(socket_voice_tasks)],
                return_exceptions=True,
            )
        try:
            await websocket.close()
        except Exception:
            pass
