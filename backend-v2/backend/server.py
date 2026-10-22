
from __future__ import annotations

from . import winssl  # noqa: F401  (Windows: cache the certificate store, see winssl.py)

import asyncio
import contextlib
import json
import logging
import random
import time
from datetime import datetime

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import agent, assist, bank, brief, claims, config, db, hub, lk, llm, playbooks, sim_specialist, state
from .mcp_tools import issue_otp, send_sms
from .state import Call
from .summarizer import summarize

log = logging.getLogger("server")
logging.basicConfig(level=logging.INFO)


_bg: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    """create_task that keeps a reference (asyncio may otherwise garbage-collect a running task)."""
    t = asyncio.create_task(coro)
    _bg.add(t)
    t.add_done_callback(_bg.discard)
    return t


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    bank.seed()
    state.load()
    # build the Mistral client now (TLS certificate loading is slow on Windows), not at the first call
    await asyncio.to_thread(llm.client)
    yield
    if tasks := agent.stop_all("server_shutdown"):  # Ctrl+C: let live calls save their recording first
        await asyncio.wait(tasks, timeout=8)


class _QuietHarmless(logging.Filter):
    """Hide harmless library chatter in the terminal (LiveKit data streams we don't use)."""
    NOISE = ("ignoring text stream with topic", "ignoring byte stream with topic",
             "error putting to queue: Event loop is closed", "failed to send session event",
             "closed unexpectedly")  # data channels of a room we just deleted

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(n in msg for n in self.NOISE)


for _name in ("", "root", "livekit"):
    logging.getLogger(_name).addFilter(_QuietHarmless())

app = FastAPI(title="Voice-Led Claims POC (v2: Mistral voice sessions)", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

settings: dict = {"auto_answer": True, "auto_specialist": True, "ring_seconds": config.RING_SECONDS,
                  "jurisdiction": config.JURISDICTION}

# Approximate list prices (USD) for the "cost per claim" success measure. Override in .env if needed.
PRICES = {
    "stt_realtime_per_min": 0.006,
    "stt_batch_per_min": 0.003,
    "tts_per_1k_chars": 0.016,
    "llm_input_per_m": 0.5,
    "llm_output_per_m": 1.5,
}


def _get(call_id: str) -> Call:
    if call_id not in state.calls:
        raise HTTPException(404, "unknown call")
    return state.calls[call_id]


# ─────────────────────────── calls ───────────────────────────
class StartCall(BaseModel):
    caller_name: str = "Amit Sharma"
    caller_number: str = "+91 98765 43210"


@app.post("/api/calls")
async def start_call(body: StartCall):
    """Customer presses the green call button. A known caller with an existing claim -> Status Agent."""
    call_id = f"CALL-{datetime.now():%Y%m%d-%H%M%S}-{random.randint(100, 999)}"
    cust = bank.customer_by_phone(body.caller_number)
    existing = claims.for_customer(cust["id"]) if cust else None
    call = Call(
        call_id=call_id,
        room=f"room-{call_id}",
        caller_name=body.caller_name,
        caller_number=body.caller_number,
        created_at=time.time(),
        customer_id=cust["id"] if cust else None,
        mode="status" if existing else "intake",
        claim_id=existing["id"] if existing else None,
    )
    state.calls[call_id] = call
    await state.push(call)
    if existing:
        await claims.attach_call(existing["id"], call_id)
    if settings["auto_answer"]:
        spawn(_auto_answer(call_id, settings["ring_seconds"]))
    return {
        "call": state.public(call),
        "livekit_url": config.LIVEKIT_URL,
        "token": lk.token(f"customer-{call_id}", body.caller_name, call.room),
    }


async def _auto_answer(call_id: str, delay: float) -> None:
    await asyncio.sleep(delay)
    call = state.calls.get(call_id)
    if call and call.status == "ringing":
        await _dispatch_agent(call)


async def _dispatch_agent(call: Call) -> None:
    """v1 asked the LiveKit server to dispatch a job to the agent worker; v2 runs the agent in this process."""
    call.status = "connecting"
    await state.push(call)
    agent.start(call)


@app.post("/api/calls/{call_id}/answer")
async def answer_call(call_id: str):
    call = _get(call_id)
    if call.status != "ringing":
        raise HTTPException(409, f"call is {call.status}")
    await _dispatch_agent(call)
    return state.public(call)


@app.post("/api/calls/{call_id}/decline")
async def decline_call(call_id: str):
    call = _get(call_id)
    if call.status == "ringing":
        call.status, call.ended_at = "declined", time.time()
        await state.push(call)
        await lk.delete_room(call.room)
    return state.public(call)


@app.post("/api/calls/{call_id}/hangup")
async def hangup_call(call_id: str):
    call = _get(call_id)
    if call.status in ("ringing", "connecting"):
        call.status = "missed"
    elif call.status == "active":
        call.status = "wrapping_up"  # the agent reports call.ended, then we save + summarise
        spawn(_finalize_watchdog(call_id))
    call.ended_at = call.ended_at or time.time()
    await state.push(call)
    agent.stop(call_id, "hangup")
    await lk.delete_room(call.room)
    return state.public(call)


async def _finalize_watchdog(call_id: str) -> None:
    """If the agent never reports the end (crash), finalize anyway."""
    await asyncio.sleep(20)
    call = state.calls.get(call_id)
    if call and call.status == "wrapping_up" and not call.file:
        await finalize(call, {"end_reason": "watchdog"})


# ─────────────────────────── Step 2: specialist joins ───────────────────────────
@app.post("/api/calls/{call_id}/specialist")
async def specialist_join(call_id: str, name: str = "Fraud Specialist"):
    call = _get(call_id)
    if call.status != "active":
        raise HTTPException(409, f"call is {call.status}")
    call.phase = "interview"
    await state.push(call)
    if call.mode == "intake" and call.claim_id and (cl := claims.get(call.claim_id)):
        cl["status"] = "interview"
        cl["steps"]["interview"] = "in_progress"
        await claims.save(cl)
    call.specialist = {"mode": "human", "name": name, "status": "on call"}
    await state.push(call)
    await claims.audit("specialist", "interview.joined", f"{name} joined the call (warm handover)",
                       call_id=call_id, claim_id=call.claim_id)
    return {"livekit_url": config.LIVEKIT_URL, "token": lk.token(f"specialist-{call_id}", name, call.room),
            "call": state.public(call)}


@app.post("/api/calls/{call_id}/simulate-specialist")
async def simulate_specialist(call_id: str):
    """Demo helper: an AI plays the human specialist (Mistral Large + a second Voxtral voice)."""
    call = _get(call_id)
    if call.status != "active" or call.phase not in ("handover", "interview"):
        raise HTTPException(409, "the AI has not handed this call over yet")
    if call.specialist and call.specialist.get("mode") == "human":
        raise HTTPException(409, "a human specialist is already on the call")
    call.phase = "interview"
    call.specialist = {"mode": "simulated", "name": sim_specialist.NAME, "status": "joining"}
    await state.push(call)
    if call.mode == "intake" and call.claim_id and (cl := claims.get(call.claim_id)):
        cl["status"] = "interview"
        cl["steps"]["interview"] = "in_progress"
        await claims.save(cl)
    sim_specialist.start(call_id)
    return state.public(call)


@app.post("/api/calls/{call_id}/end-interview")
async def end_interview(call_id: str):
    call = _get(call_id)
    sim_specialist.stop(call_id)
    await claims.audit("specialist", "interview.ended", "Interview finished, call ended by specialist",
                       call_id=call_id, claim_id=call.claim_id)
    return await hangup_call(call_id)


# ─────────────────────────── finalize a call ───────────────────────────
async def finalize(call: Call, info: dict) -> None:
    if call.file:  # already finalized by the watchdog: still keep the recording / usage that arrived late
        if info.get("audio_file") and not call.audio_file:
            call.audio_file = info["audio_file"]
        if info.get("usage") and not call.metrics.get("usage"):
            call.metrics["usage"] = info["usage"]
            call.metrics["cost_usd"] = _cost(info["usage"])
        await state.push(call)
        return
    call.status = "wrapping_up"
    call.audio_file = info.get("audio_file") or call.audio_file
    if info.get("usage"):
        call.metrics["usage"] = info["usage"]
        call.metrics["cost_usd"] = _cost(info["usage"])
    call.ended_at = call.ended_at or time.time()
    call.file = f"{call.call_id}.json"
    await state.push(call)
    await _write_record(call)

    call.summary = await summarize(call.lines)
    call.status = "ended"
    await state.push(call)
    await _write_record(call)
    await claims.audit("system", "call.saved", f"Transcript and summary saved ({call.file})",
                       call_id=call.call_id, claim_id=call.claim_id)

    if call.claim_id and (cl := claims.get(call.claim_id)):
        if call.mode == "status":
            cl["followups"].append({"call_id": call.call_id, "time": bank.iso(bank.now()),
                                    "summary": (call.summary or {}).get("short_summary")})
            cl["steps"]["followup"] = "done"
            await claims.save(cl)
        else:
            if cl.get("intake_summary") is None:
                cl["intake_summary"] = call.summary
            if cl["steps"].get("interview") in ("in_progress", "waiting"):
                cl["steps"]["interview"] = "done"
            await claims.save(cl)
            spawn(brief.generate(cl["id"]))  # Step 3 starts by itself


async def _write_record(call: Call) -> None:
    d = state.public(call)
    path = config.CALLS_DIR / f"{call.call_id}.json"
    await asyncio.to_thread(path.write_text, json.dumps(d, indent=2, ensure_ascii=False, default=str), "utf-8")


def _cost(u: dict) -> dict:
    stt = (u.get("stt_audio_seconds") or 0) / 60 * PRICES["stt_realtime_per_min"]
    tts = (u.get("tts_characters") or 0) / 1000 * PRICES["tts_per_1k_chars"]
    llm = (u.get("llm_prompt_tokens") or 0) / 1e6 * PRICES["llm_input_per_m"] + \
          (u.get("llm_completion_tokens") or 0) / 1e6 * PRICES["llm_output_per_m"]
    return {"stt": round(stt, 4), "tts": round(tts, 4), "llm": round(llm, 4), "total": round(stt + tts + llm, 4),
            "note": "estimate at list prices (LLM tokens of the managed voice session are not reported)"}


# ─────────────────────────── agent -> backend events ───────────────────────────
async def handle_event(call_id: str, t: str, d: dict) -> None:
    """Everything the voice agent reports (in-process now; v1 received these over HTTP)."""
    call = state.calls.get(call_id)
    if call is None:
        return

    if t == "agent.joined":
        call.status, call.answered_at = "active", time.time()
        await claims.audit("ai-agent", "call.answered", f"AI agent answered ({call.mode} mode)", call_id=call.call_id,
                           claim_id=call.claim_id)
    elif t.endswith(".live"):  # customer.live | ai.live | specialist.live
        who = t.split(".")[0]
        text = d.get("text", "") if who == "ai" else claims.mask_pii(d.get("text", ""))
        call.live[who] = text
        await hub.broadcast({"type": "live", "call_id": call.call_id, "speaker": who, "text": text})
        return
    elif t == "line":
        # mask what PEOPLE say (OTPs, card/phone numbers); the AI never speaks secrets
        text = d.get("text", "") if d.get("speaker") == "ai" else claims.mask_pii(d.get("text", ""))
        line = {**d, "text": text, "index": len(call.lines)}
        line.setdefault("phase", "interview" if call.phase == "interview" else "ai")
        call.lines.append(line)
        call.live.pop(line["speaker"], None)
        state.persist(call)
        await hub.broadcast({"type": "line", "call_id": call.call_id, "line": line})
        if call.phase == "interview" and call.mode == "intake":
            assist.request_update(call.call_id)
        return
    elif t in ("agent.state", "user.state"):
        # frequent + only needed live: broadcast, don't write to the database each time
        if t == "agent.state":
            call.agent_state = d.get("state")
        else:
            call.user_state = d.get("state")
        await hub.broadcast({"type": "call.updated", "call": state.public(call)})
        return
    elif t == "metrics.turn":
        call.metrics.setdefault("turns", []).append(d)
    elif t == "metrics.turns":  # v2: the whole list, re-read from the Mistral session's transcript
        call.metrics["turns"] = d.get("turns", [])
    elif t == "agent.error":
        call.error = d.get("message")
    elif t == "agent.warning":
        await hub.broadcast({"type": "warning", "call_id": call.call_id, "message": d.get("message")})
        return
    elif t == "call.ended":
        spawn(finalize(call, d))
        return
    await state.push(call)


@app.post("/internal/calls/{call_id}/otp")
async def internal_send_otp(call_id: str):
    """Kept for compatibility: the agent now texts the OTP itself the moment it answers."""
    return await issue_otp(_get(call_id), reason="sent automatically when the call connected")


class AgentEvent(BaseModel):
    call_id: str
    type: str
    data: dict = {}


@app.post("/internal/events")
async def agent_event(ev: AgentEvent):
    """Kept for compatibility (external tools / tests can still post agent events)."""
    if ev.call_id not in state.calls:
        return {"ok": False}
    await handle_event(ev.call_id, ev.type, ev.data)
    return {"ok": True}


# ─────────────────────────── claims (Step 3) ───────────────────────────
def _claim_view(cl: dict) -> dict:
    v = claims.with_clock(cl)
    v["call_details"] = [state.public(state.calls[c]) for c in cl["calls"] if c in state.calls]
    v["audit"] = sorted(claims.audit_for(claim_id=cl["id"]) +
                        [a for c in cl["calls"] for a in claims.audit_for(call_id=c) if not a.get("claim_id")],
                        key=lambda a: a["time"])
    v["security_log"] = bank.security_log_for(cl["customer_id"])
    turns = [t for c in v["call_details"] for t in c["metrics"].get("turns", []) if t.get("total")]
    v["metrics"] = _latency(turns)
    v["metrics"]["cost_usd"] = round(sum((c["metrics"].get("cost_usd") or {}).get("total", 0)
                                         for c in v["call_details"]), 4)
    return v


def _latency(turns: list[dict]) -> dict:
    vals = sorted(t["total"] for t in turns)
    if not vals:
        return {"turns": 0, "p50": None, "p95": None}
    pick = lambda q: vals[min(len(vals) - 1, int(round(q * (len(vals) - 1))))]  # noqa: E731
    return {"turns": len(vals), "p50": round(pick(0.5), 2), "p95": round(pick(0.95), 2)}


@app.get("/api/claims")
async def list_claims():
    return [claims.with_clock(c) for c in claims.list_all()]


@app.get("/api/claims/{claim_id}")
async def get_claim(claim_id: str):
    cl = claims.get(claim_id)
    if not cl:
        raise HTTPException(404, "unknown claim")
    return _claim_view(cl)


@app.post("/api/claims/{claim_id}/brief")
async def make_brief(claim_id: str):
    if not claims.get(claim_id):
        raise HTTPException(404, "unknown claim")
    spawn(brief.generate(claim_id))
    return {"ok": True}


class Decision(BaseModel):
    outcome: str  # refund_approved | declined | more_info
    notes: str = ""
    analyst: str = "Fraud Analyst"


@app.post("/api/claims/{claim_id}/decision")
async def decide(claim_id: str, body: Decision):
    async with _decide_locks.setdefault(claim_id, asyncio.Lock()):
        return await _decide(claim_id, body)


_decide_locks: dict[str, asyncio.Lock] = {}


async def _decide(claim_id: str, body: Decision):
    cl = claims.get(claim_id)
    if not cl:
        raise HTTPException(404, "unknown claim")
    if cl.get("refund"):
        raise HTTPException(409, "This claim is already refunded; the decision is final.")
    if (cl.get("decision") or {}).get("outcome") == "declined" and body.outcome != "more_info":
        raise HTTPException(409, "This claim was already declined; the decision is final.")
    cl["decision"] = {**body.model_dump(), "time": bank.iso(bank.now())}
    cl["status"] = "decided" if body.outcome != "more_info" else "investigation"
    cl["steps"]["investigate"] = "decided" if body.outcome != "more_info" else "brief_ready"
    await claims.save(cl)
    await claims.audit("analyst", "decision.recorded", f"{body.outcome}: {body.notes}", claim_id=claim_id)
    words = {"refund_approved": "has been approved for a refund", "declined": "has been reviewed and declined",
             "more_info": "needs a little more information"}
    await send_sms(cl["phone"], f"Northbridge Bank: your claim {cl['reference']} {words.get(body.outcome, 'was updated')}. "
                   "Call us any time for details.", claim_id=claim_id)
    if body.outcome == "refund_approved":
        # simulate the money actually going back (approval alone does not move money)
        r = bank.credit_refund(cl["customer_id"], int(cl["total_disputed"]), cl["reference"],
                               [t["id"] for t in cl["disputed_transactions"]])
        cl["refund"] = r
        cl["status"] = "refunded"
        cl["steps"]["followup"] = cl["steps"].get("followup", "pending")
        await claims.save(cl)
        await claims.audit("system", "refund.credited",
                           f"INR {r['amount']:,} credited to account ending {r['account_last4']}", claim_id=claim_id)
        await send_sms(cl["phone"], f"Northbridge Bank: INR {r['amount']:,} has been credited to your account ending "
                       f"{r['account_last4']} (refund for claim {cl['reference']}). Available balance: INR {r['new_balance']:,}.",
                       claim_id=claim_id)
    return _claim_view(cl)


# ─────────────────────────── misc ───────────────────────────
@app.get("/api/calls")
async def list_calls():
    return [state.public(c) for c in sorted(state.calls.values(), key=lambda c: -c.created_at)]


@app.get("/api/calls/{call_id}/record")
async def call_record(call_id: str):
    return state.public(_get(call_id))


@app.get("/api/calls/{call_id}/audio")
async def call_audio(call_id: str):
    call = _get(call_id)
    if not call.audio_file or not (config.AUDIO_DIR / call.audio_file).exists():
        raise HTTPException(404, "no recording")
    return FileResponse(config.AUDIO_DIR / call.audio_file, media_type="audio/wav")


@app.get("/api/sms")
async def list_sms(phone: str = ""):
    key = bank.norm_phone(phone)
    return [s for s in db.all("sms") if not key or bank.norm_phone(s["phone"]) == key]


@app.get("/api/bank/customers")
async def customers():
    return db.all("customers")


@app.get("/api/bank/customers/{customer_id}/transactions")
async def customer_transactions(customer_id: str):
    """For the customer's phone banking app (synthetic data)."""
    return bank.transactions_for(customer_id, hours=24 * 7)


@app.get("/api/playbook")
async def playbook(jurisdiction: str = config.JURISDICTION):
    return playbooks.get(jurisdiction)


class Settings(BaseModel):
    auto_answer: bool | None = None
    auto_specialist: bool | None = None  # Step 2: the simulated specialist joins by itself after the handover
    ring_seconds: float | None = None


@app.post("/api/settings")
async def update_settings(body: Settings):
    for k, v in body.model_dump(exclude_none=True).items():
        settings[k] = v
    await hub.broadcast({"type": "settings", "settings": settings})
    return settings


@app.post("/api/admin/reset")
async def reset_demo():
    """Wipe calls, claims, SMS and audit; re-seed the synthetic bank."""
    for cid, call in list(state.calls.items()):
        sim_specialist.stop(cid)
        agent.stop(cid, "reset")  # end live calls too, so no old AI agent keeps running after a reset
        if call.status in ("ringing", "connecting", "active", "wrapping_up"):
            await lk.delete_room(call.room)
    db.wipe()
    state.calls.clear()
    bank.seed()
    await hub.broadcast({"type": "reset"})
    return {"ok": True}


@app.get("/api/health")
async def health():
    return {"ok": True, "mistral_key_loaded": bool(config.MISTRAL_API_KEY), "jurisdiction": config.JURISDICTION}


@app.websocket("/ws/console")
async def console_ws(ws: WebSocket):
    await ws.accept()
    hub.clients.add(ws)
    await ws.send_text(json.dumps({
        "type": "snapshot",
        "settings": settings,
        "calls": [state.public(c) for c in state.calls.values()],
        "claims": [claims.with_clock(c) for c in claims.list_all()],
        "sms": db.all("sms")[-50:],
    }, default=str))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)
