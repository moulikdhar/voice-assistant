

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from livekit import api
from pydantic import BaseModel

from . import config

log = logging.getLogger("server")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Voice-Led Claims – Call Controller")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

Status = Literal["ringing", "connecting", "active", "wrapping_up", "ended", "missed", "declined"]


# ─────────────────────────── in-memory state ───────────────────────────
class Call(BaseModel):
    call_id: str
    room: str
    caller_name: str
    caller_number: str
    status: Status = "ringing"
    created_at: float
    answered_at: float | None = None
    ended_at: float | None = None
    agent_state: str | None = None
    user_state: str | None = None
    lines: list[dict] = []
    live: dict[str, str] = {}  # {"customer": "...", "ai": "..."} text still being spoken
    summary: dict | None = None
    file: str | None = None
    error: str | None = None


calls: dict[str, Call] = {}
settings: dict[str, Any] = {"auto_answer": True, "ring_seconds": config.RING_SECONDS}
consoles: set[WebSocket] = set()


async def broadcast(event: dict) -> None:
    dead = []
    for ws in consoles:
        try:
            await ws.send_text(json.dumps(event))
        except Exception:
            dead.append(ws)
    for ws in dead:
        consoles.discard(ws)


async def push_call(call: Call) -> None:
    await broadcast({"type": "call.updated", "call": call.model_dump()})


def lk_api() -> api.LiveKitAPI:
    # LiveKit's server API wants http(s)://, the browser wants ws(s)://
    url = config.LIVEKIT_URL.replace("ws://", "http://").replace("wss://", "https://")
    return api.LiveKitAPI(url, config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)


def new_call_id() -> str:
    return f"CALL-{datetime.now():%Y%m%d-%H%M%S}-{random.randint(100, 999)}"


# ─────────────────────────── REST API ───────────────────────────
class StartCall(BaseModel):
    caller_name: str = "Riya Sharma"
    caller_number: str = "+91 98765 43210"


@app.post("/api/calls")
async def start_call(body: StartCall):
    """Customer presses the green call button."""
    call_id = new_call_id()
    call = Call(
        call_id=call_id,
        room=f"room-{call_id}",
        caller_name=body.caller_name,
        caller_number=body.caller_number,
        created_at=time.time(),
    )
    calls[call_id] = call

    token = (
        api.AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
        .with_identity(f"customer-{call_id}")
        .with_name(body.caller_name)
        .with_grants(api.VideoGrants(room_join=True, room=call.room))
        .to_jwt()
    )
    await push_call(call)

    if settings["auto_answer"]:
        asyncio.create_task(_auto_answer(call_id, settings["ring_seconds"]))

    return {"call": call.model_dump(), "livekit_url": config.LIVEKIT_URL, "token": token}


async def _auto_answer(call_id: str, delay: float) -> None:
    await asyncio.sleep(delay)
    call = calls.get(call_id)
    if call and call.status == "ringing":
        await _dispatch_agent(call)


async def _dispatch_agent(call: Call) -> None:
    call.status = "connecting"
    await push_call(call)
    lk = lk_api()
    try:
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=config.AGENT_NAME,
                room=call.room,
                metadata=json.dumps(
                    {
                        "call_id": call.call_id,
                        "caller_name": call.caller_name,
                        "caller_number": call.caller_number,
                    }
                ),
            )
        )
    finally:
        await lk.aclose()


@app.post("/api/calls/{call_id}/answer")
async def answer_call(call_id: str):
    """Console clicks 'Answer with AI'."""
    call = _get(call_id)
    if call.status != "ringing":
        raise HTTPException(409, f"call is {call.status}")
    await _dispatch_agent(call)
    return call.model_dump()


@app.post("/api/calls/{call_id}/decline")
async def decline_call(call_id: str):
    call = _get(call_id)
    if call.status == "ringing":
        call.status, call.ended_at = "declined", time.time()
        await push_call(call)
        await _delete_room(call)
    return call.model_dump()


@app.post("/api/calls/{call_id}/hangup")
async def hangup_call(call_id: str):
    """Customer hangs up, or console ends the call."""
    call = _get(call_id)
    if call.status in ("ringing", "connecting"):
        call.status = "missed"  # hung up before the AI picked up
    elif call.status == "active":
        call.status = "wrapping_up"  # agent is saving transcript + summary
    call.ended_at = call.ended_at or time.time()
    await push_call(call)
    await _delete_room(call)
    return call.model_dump()


async def _delete_room(call: Call) -> None:
    lk = lk_api()
    try:
        await lk.room.delete_room(api.DeleteRoomRequest(room=call.room))
    except Exception as e:
        log.info("delete_room(%s): %s", call.room, e)
    finally:
        await lk.aclose()


@app.get("/api/calls")
async def list_calls():
    return [c.model_dump() for c in sorted(calls.values(), key=lambda c: -c.created_at)]


@app.get("/api/calls/{call_id}/record")
async def call_record(call_id: str):
    """The saved JSON file (transcript + summary)."""
    path = config.CALLS_DIR / f"{call_id}.json"
    if not path.exists():
        raise HTTPException(404, "not saved yet")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/history")
async def history():
    """All saved calls on disk (survives server restarts)."""
    out = []
    for p in sorted(config.CALLS_DIR.glob("CALL-*.json"), reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append(
                {
                    "call_id": d["call_id"],
                    "caller": d.get("caller"),
                    "started_at": d.get("started_at"),
                    "duration_seconds": d.get("duration_seconds"),
                    "short_summary": (d.get("summary") or {}).get("short_summary"),
                }
            )
        except Exception:
            continue
    return out


class Settings(BaseModel):
    auto_answer: bool | None = None
    ring_seconds: float | None = None


@app.post("/api/settings")
async def update_settings(body: Settings):
    for k, v in body.model_dump(exclude_none=True).items():
        settings[k] = v
    await broadcast({"type": "settings", "settings": settings})
    return settings


def _get(call_id: str) -> Call:
    if call_id not in calls:
        raise HTTPException(404, "unknown call")
    return calls[call_id]


# ─────────────────────────── agent -> backend events ───────────────────────────
class AgentEvent(BaseModel):
    call_id: str
    type: str
    data: dict = {}


@app.post("/internal/events")
async def agent_event(ev: AgentEvent):
    call = calls.get(ev.call_id)
    if call is None:
        return {"ok": False}
    d = ev.data

    if ev.type == "agent.joined":
        call.status, call.answered_at = "active", time.time()
    elif ev.type == "customer.live":
        call.live["customer"] = d.get("text", "")
    elif ev.type == "ai.live":
        call.live["ai"] = d.get("text", "")
    elif ev.type == "line":
        call.lines.append(d)
        call.live.pop("customer" if d.get("speaker") == "customer" else "ai", None)
    elif ev.type == "agent.state":
        call.agent_state = d.get("state")
    elif ev.type == "user.state":
        call.user_state = d.get("state")
    elif ev.type == "agent.error":
        call.error = d.get("message")
    elif ev.type == "agent.warning":
        await broadcast({"type": "warning", "call_id": call.call_id, "message": d.get("message")})
        return {"ok": True}
    elif ev.type == "call.summarizing":
        call.status, call.file, call.live = "wrapping_up", d.get("file"), {}
        call.ended_at = call.ended_at or time.time()
    elif ev.type == "call.saved":
        call.status, call.summary, call.file = "ended", d.get("summary"), d.get("file")
        call.ended_at = call.ended_at or time.time()

    # Small, frequent events are sent as deltas; state changes send the whole call.
    if ev.type in ("customer.live", "ai.live"):
        await broadcast({"type": ev.type, "call_id": call.call_id, "text": d.get("text", "")})
    elif ev.type == "line":
        await broadcast({"type": "line", "call_id": call.call_id, "line": d})
    else:
        await push_call(call)
    return {"ok": True}


# ─────────────────────────── console websocket ───────────────────────────
@app.websocket("/ws/console")
async def console_ws(ws: WebSocket):
    await ws.accept()
    consoles.add(ws)
    await ws.send_text(
        json.dumps(
            {
                "type": "snapshot",
                "settings": settings,
                "calls": [c.model_dump() for c in calls.values()],
            }
        )
    )
    try:
        while True:
            await ws.receive_text()  # keep-alive pings from the browser
    except WebSocketDisconnect:
        pass
    finally:
        consoles.discard(ws)


@app.get("/api/health")
async def health():
    return {"ok": True, "mistral_key_loaded": bool(config.MISTRAL_API_KEY)}
