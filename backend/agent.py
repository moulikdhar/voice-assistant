

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import AsyncIterable

import httpx

from . import config  # noqa: F401  (loads .env + LiveKit defaults first)
from .prompts import greeting, intake_instructions
from .summarizer import summarize
from .voxtral_stt import GatedVoxtralSTT

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    AudioConfig,
    BackgroundAudioPlayer,
    BuiltinAudioClip,
    ChatMessage,
    JobContext,
    JobProcess,
    ModelSettings,
    cli,
)
from livekit.agents.voice.events import (
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
)
from livekit.plugins import mistralai, silero

log = logging.getLogger("claims-agent")


# ─────────────────────────── event reporter ───────────────────────────
class Reporter:
    """Sends events to the backend in order, without ever blocking the conversation."""

    def __init__(self, call_id: str):
        self.call_id = call_id
        self._q: asyncio.Queue[dict | None] = asyncio.Queue()
        self._client = httpx.AsyncClient(base_url=config.BACKEND_URL, timeout=5)
        self._task = asyncio.create_task(self._run())

    def emit(self, type_: str, **data) -> None:
        self._q.put_nowait({"call_id": self.call_id, "type": type_, "data": data})

    async def _run(self) -> None:
        while (item := await self._q.get()) is not None:
            try:
                await self._client.post("/internal/events", json=item)
            except Exception as e:
                log.warning("could not report %s: %s", item["type"], e)

    async def aclose(self) -> None:
        self._q.put_nowait(None)
        await self._task
        await self._client.aclose()


# ─────────────────────────── the agent ───────────────────────────
class ClaimIntakeAgent(Agent):
    def __init__(self, reporter: Reporter, caller_name: str | None, caller_number: str | None):
        super().__init__(instructions=intake_instructions(caller_name, caller_number))
        self._reporter = reporter
        self._caller_name = caller_name

    async def on_enter(self) -> None:
        # Fixed greeting: fast, and always includes the "you're talking to an AI" disclosure.
        self.session.say(greeting(self._caller_name), allow_interruptions=False)

    async def transcription_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncIterable[str]:
        """Intercept the AI's words as they are produced -> live 'typing' on the console."""
        buf = ""
        async for chunk in text:
            buf += str(chunk)
            self._reporter.emit("ai.live", text=buf)
            yield chunk


# ─────────────────────────── worker setup ───────────────────────────
def prewarm(proc: JobProcess) -> None:
    # Load the VAD model once per process (it is used by both STT flushing and turn-taking).
    proc.userdata["vad"] = silero.VAD.load(min_silence_duration=0.45)


# shutdown_process_timeout: give the post-call summary time to finish before the job exits.
# http_proxy=None for a local LiveKit server: LiveKit Agents otherwise routes even localhost
# through HTTPS_PROXY (common on office laptops). Mistral API calls still honour the proxy.
_local_lk = any(h in config.LIVEKIT_URL for h in ("localhost", "127.0.0.1"))
server = AgentServer(
    setup_fnc=prewarm,
    shutdown_process_timeout=90.0,
    log_level="INFO",  # DEBUG in dev mode is very noisy
    **({"http_proxy": None} if _local_lk else {}),
)


@server.rtc_session(agent_name=config.AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    meta = json.loads(ctx.job.metadata or "{}")
    call_id: str = meta.get("call_id", ctx.room.name)
    caller_name = meta.get("caller_name")
    caller_number = meta.get("caller_number")
    started_at = time.time()

    reporter = Reporter(call_id)
    vad = ctx.proc.userdata["vad"]

    session = AgentSession(
        vad=vad,
        stt=GatedVoxtralSTT(  # Voxtral Realtime, sends audio only while the caller speaks
            model=config.STT_MODEL,
            target_streaming_delay_ms=config.STT_DELAY_MS,
            vad=vad,
        ),
        llm=mistralai.LLM(model=config.LLM_MODEL, temperature=0.3),
        tts=mistralai.TTS(
            model=config.TTS_MODEL,
            voice=config.TTS_VOICE,
            response_format="pcm",  # raw audio = lowest latency
        ),
    )

    # Transcript lines we save at the end: {speaker, text, t (seconds from start), ...}
    lines: list[dict] = []
    live_user = {"final": "", "interim": ""}

    @session.on("user_input_transcribed")
    def _on_user_transcribed(ev: UserInputTranscribedEvent) -> None:
        if ev.is_final:
            live_user["final"] = (live_user["final"] + " " + ev.transcript).strip()
            live_user["interim"] = ""
        else:
            live_user["interim"] = ev.transcript
        text = (live_user["final"] + " " + live_user["interim"]).strip()
        if text:
            reporter.emit("customer.live", text=text)

    @session.on("conversation_item_added")
    def _on_item(ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        if not isinstance(item, ChatMessage) or item.role not in ("user", "assistant"):
            return
        text = (item.text_content or "").strip()
        if not text:
            return
        line = {
            "index": len(lines),
            "speaker": "customer" if item.role == "user" else "ai",
            "text": text,
            "t": round(max(0.0, item.created_at - started_at), 2),
            "interrupted": bool(item.interrupted),
        }
        lines.append(line)
        if item.role == "user":
            live_user["final"] = live_user["interim"] = ""
        reporter.emit("line", **line)

    @session.on("agent_state_changed")
    def _on_agent_state(ev: AgentStateChangedEvent) -> None:
        reporter.emit("agent.state", state=ev.new_state)  # listening | thinking | speaking

    @session.on("user_state_changed")
    def _on_user_state(ev: UserStateChangedEvent) -> None:
        reporter.emit("user.state", state=ev.new_state)  # speaking | listening | away

    @session.on("error")
    def _on_error(ev) -> None:
        err = ev.error
        # STT streams are re-created by LiveKit even after an "unrecoverable" error, so they
        # are always just a notice. LLM/TTS errors that LiveKit gives up on are shown in red.
        recoverable = bool(getattr(err, "recoverable", False)) or getattr(err, "type", "") == "stt_error"
        raw = str(getattr(err, "error", err))
        if getattr(getattr(err, "error", None), "status_code", None) in (401, 403, 404):
            recoverable = False  # wrong key / model not enabled: retrying will never fix it
        log.warning("model error (recoverable=%s): %s", recoverable, raw[:300])
        if recoverable:
            # LiveKit retries/reconnects by itself: show a short notice, not a red error.
            part = {"stt_error": "Transcription", "tts_error": "Voice", "llm_error": "AI"}.get(
                getattr(err, "type", ""), "Model"
            )
            reporter.emit("agent.warning", message=f"{part} hiccup, recovered automatically")
        else:
            # e.g. wrong API key, model not enabled on the workspace, no network
            reporter.emit("agent.error", message=raw[:300])

    # Save everything when the call ends (customer hangs up, or console ends the call).
    async def _save(reason: str = "") -> None:
        ended_at = time.time()
        path = config.CALLS_DIR / f"{call_id}.json"
        record = {
            "call_id": call_id,
            "caller": {"name": caller_name, "number": caller_number},
            "started_at": datetime.fromtimestamp(started_at, timezone.utc).isoformat(),
            "ended_at": datetime.fromtimestamp(ended_at, timezone.utc).isoformat(),
            "duration_seconds": round(ended_at - started_at, 1),
            "end_reason": str(reason) or None,
            "models": {
                "stt": config.STT_MODEL,
                "llm": config.LLM_MODEL,
                "tts": f"{config.TTS_MODEL} ({config.TTS_VOICE})",
                "summary": config.SUMMARY_MODEL,
            },
            "transcript": lines,
            "summary": None,
        }

        def write() -> None:
            path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

        await asyncio.to_thread(write)  # 1) save the transcript immediately, so it is never lost
        reporter.emit("call.summarizing", file=path.name)
        record["summary"] = await summarize(lines)  # 2) then add the summary
        summary = record["summary"]
        await asyncio.to_thread(write)
        log.info("saved %s", path)
        reporter.emit("call.saved", file=str(path.name), summary=summary)
        await reporter.aclose()

    ctx.add_shutdown_callback(_save)

    await session.start(agent=ClaimIntakeAgent(reporter, caller_name, caller_number), room=ctx.room)
    reporter.emit("agent.joined")

    # Call-centre realism: quiet office ambience + keyboard typing while the AI "thinks".
    if config.AMBIENT_SOUND:
        background = BackgroundAudioPlayer(
            ambient_sound=AudioConfig(BuiltinAudioClip.OFFICE_AMBIENCE, volume=0.25),
            thinking_sound=[
                AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.5),
                AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=0.4),
            ],
        )
        await background.start(room=ctx.room, agent_session=session)


if __name__ == "__main__":
    if not config.MISTRAL_API_KEY:
        raise SystemExit("MISTRAL_API_KEY is missing. Put it in the .env file in the project folder.")
    cli.run_app(server)
