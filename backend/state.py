
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from . import db, hub

Status = Literal["ringing", "connecting", "active", "wrapping_up", "ended", "missed", "declined"]


class Call(BaseModel):
    call_id: str
    room: str
    caller_name: str
    caller_number: str
    status: Status = "ringing"
    mode: Literal["intake", "status"] = "intake"  # Step 1 intake agent | Step 4 status agent
    phase: Literal["ai", "handover", "interview"] = "ai"  # who is talking to the customer
    created_at: float
    answered_at: float | None = None
    ended_at: float | None = None
    customer_id: str | None = None
    claim_id: str | None = None
    agent_state: str | None = None
    user_state: str | None = None
    lines: list[dict] = Field(default_factory=list)
    live: dict[str, str] = Field(default_factory=dict)  # speaker -> text still being spoken
    verification: dict[str, Any] = Field(default_factory=lambda: {"otp_sent": False, "verified": False, "attempts": 0})
    card_actions: list[dict] = Field(default_factory=list)
    pending: dict[str, dict] = Field(default_factory=dict)  # read-back actions awaiting a "yes"
    summary: dict | None = None
    assist: dict | None = None  # Step 2 live checklist
    handover_reason: str | None = None  # why the AI transferred the call (given to the specialist)
    specialist: dict | None = None  # Step 2: {"mode": "human"|"simulated", "name", "status"}
    metrics: dict[str, Any] = Field(default_factory=lambda: {"turns": []})
    audio_file: str | None = None
    batch_transcript: dict | None = None
    file: str | None = None
    error: str | None = None


calls: dict[str, Call] = {}


def load() -> None:
    """Reload previous calls from disk (survives backend restarts)."""
    for d in db.all("calls"):
        try:
            c = Call(**d)
            if c.status in ("ringing", "connecting", "active", "wrapping_up"):
                c.status = "ended"
            calls[c.call_id] = c
        except Exception:
            continue


def persist(call: Call) -> None:
    db.put("calls", call.call_id, call.model_dump())


async def push(call: Call) -> None:
    persist(call)
    await hub.broadcast({"type": "call.updated", "call": public(call)})


def public(call: Call) -> dict:
    d = call.model_dump()
    d.pop("pending", None)
    d["pending"] = [{"id": k, **{x: v[x] for x in ("kind", "read_back")}} for k, v in call.pending.items()]
    return d
