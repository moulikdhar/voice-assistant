
from __future__ import annotations

from . import winssl  # noqa: F401  (Windows: cache the certificate store, see winssl.py)

import asyncio
import json
import logging
import re
import sys
import time
import wave
from typing import AsyncIterable

import httpx
import numpy as np

from . import config  # noqa: F401  (loads .env + LiveKit defaults first)
from .prompts import greeting, intake_instructions, otp_status, otp_turn_instructions, status_instructions
from .voxtral_stt import GatedVoxtralSTT

from livekit import api, rtc
from livekit.agents import (
    Agent,
    inference,
    llm,
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
    metrics,
    room_io,
    stt,
)
from livekit.agents.llm.mcp import MCPServerHTTP
from livekit.agents.voice.events import (
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    MetricsCollectedEvent,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
)
from livekit.plugins import mistralai, silero
from livekit.plugins.mistralai import tts as _mistral_tts

# The plugin converts every TTS chunk float32 -> int16 with a pure-Python loop (~4 ms per second of audio,
# on the same loop that plays audio). numpy does the same in ~0.03 ms.
_mistral_tts._f32le_to_s16le = lambda b: (np.clip(np.frombuffer(b, "<f4"), -1, 1) * 32767).astype("<i2").tobytes()

log = logging.getLogger("claims-agent")


class _QuietHarmless(logging.Filter):
    """Hide LiveKit's harmless end-of-call noise (it tries to send a status event to a room that is
    already closed, and ignores a data stream we don't use). Real errors still show."""
    NOISE = ("failed to send session event", "ignoring byte stream with topic 'lk.agent.session'",
             "error putting to queue: Event loop is closed", "ignoring text stream with topic",
             "ignoring byte stream with topic", "data channel")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(n in msg for n in self.NOISE)


for _name in ("livekit", "livekit.agents", "root", ""):
    logging.getLogger(_name).addFilter(_QuietHarmless())

INTAKE_TOOLS = ["get_caller_profile", "send_otp", "verify_otp", "list_recent_transactions",
                "request_card_block", "confirm_card_block", "request_claim", "confirm_claim",
                "transfer_to_specialist"]
STATUS_TOOLS = ["get_caller_profile", "send_otp", "verify_otp", "get_claim_status",
                "log_question_for_analyst", "transfer_to_specialist"]
SR = 16000


# ─────────────────────────── event reporter ───────────────────────────
class Reporter:
    """Sends events to the backend in order, without ever blocking the conversation."""

    def __init__(self, call_id: str):
        self.call_id = call_id
        self._q: asyncio.Queue[dict | None] = asyncio.Queue()
        # plain http to localhost: skip loading TLS certificates (slow on Windows, blocks the audio loop)
        self._client = httpx.AsyncClient(base_url=config.BACKEND_URL, timeout=5, trust_env=False, verify=False)
        self._task = asyncio.create_task(self._run())

    async def send_otp(self) -> dict:
        """Ask the backend to text the OTP right now (instant SMS, no LLM round trip)."""
        try:
            r = await self._client.post(f"/internal/calls/{self.call_id}/otp")
            return r.json()
        except Exception as e:
            log.warning("could not send OTP at call start: %s", e)
            return {"ok": False}

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


# ─────────────────────────── call recorder (for Step 3) ───────────────────────────
class Recorder:
    """A HIDDEN participant that subscribes to every audio track (customer, AI, specialist)
    and mixes them on one timeline into a 16 kHz mono WAV."""

    def __init__(self, room_name: str, call_id: str):
        self.room_name, self.call_id = room_name, call_id
        self.room = rtc.Room()
        self.t0 = time.monotonic()
        self.buf = np.zeros(SR * 120, dtype=np.float32)
        self.end = 0
        self.tasks: list[asyncio.Task] = []
        self.streams: list[rtc.AudioStream] = []

    async def start(self) -> None:
        tok = (api.AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
               .with_identity(f"recorder-{self.call_id}").with_name("recorder")
               .with_grants(api.VideoGrants(room_join=True, room=self.room_name, hidden=True,
                                            can_publish=False, can_subscribe=True))
               .to_jwt())

        @self.room.on("track_subscribed")
        def _on_track(track: rtc.Track, _pub, participant: rtc.RemoteParticipant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                self.tasks.append(asyncio.create_task(self._capture(track)))

        await self.room.connect(config.LIVEKIT_URL, tok)

    def _add(self, pos: int, samples: np.ndarray) -> None:
        need = pos + len(samples)
        if need > len(self.buf):
            self.buf = np.concatenate([self.buf, np.zeros(max(need - len(self.buf), SR * 60), np.float32)])
        self.buf[pos:need] += samples
        self.end = max(self.end, need)

    async def _capture(self, track: rtc.Track) -> None:
        cursor = None
        stream = rtc.AudioStream.from_track(track=track, sample_rate=SR, num_channels=1)
        self.streams.append(stream)
        async for ev in stream:
            s = np.frombuffer(ev.frame.data, dtype=np.int16).astype(np.float32)
            expected = int((time.monotonic() - self.t0) * SR) - len(s)
            if cursor is None or abs(expected - cursor) > SR // 5:  # first frame or a gap > 200 ms
                cursor = max(expected, 0)
            self._add(cursor, s)
            cursor += len(s)

    async def stop(self) -> str | None:
        # Close the native audio streams FIRST. A stream that is only "cancelled" keeps receiving frames
        # in the LiveKit engine after the job ends -> "error putting to queue: Event loop is closed" floods
        # and wasted CPU (choppy voice on the next call).
        for st in self.streams:
            try:
                await st.aclose()
            except Exception:
                pass
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        try:
            await self.room.disconnect()
        except Exception:
            pass
        if self.end < SR:  # less than a second: nothing worth saving
            return None
        pcm = np.clip(self.buf[: self.end], -32768, 32767).astype(np.int16)
        name = f"{self.call_id}.wav"

        def write() -> None:
            with wave.open(str(config.AUDIO_DIR / name), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SR)
                w.writeframes(pcm.tobytes())

        await asyncio.to_thread(write)
        return name


# ─────────────────────────── spoken-text cleaner ───────────────────────────
def clean_spoken(text: str) -> str:
    """Remove things that must never be read aloud: *stage directions*, **bold** markers, list dashes."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)        # **bold** -> bold
    text = re.sub(r"\*[^*\n]*\*", "", text)               # *silence until transfer* -> (nothing)
    text = text.replace("*", "")
    text = re.sub(r"(^|\s)-\s+(?=\w)", r"\1", text)       # " - Ten thousand" -> " Ten thousand"
    return re.sub(r"[ \t]{2,}", " ", text)


async def clean_stream(text: AsyncIterable[str]) -> AsyncIterable[str]:
    """Streaming version: holds text back while an *...* block is still open."""
    buf = ""
    async for chunk in text:
        buf += str(chunk)
        if buf.count("*") % 2 == 0 or len(buf) > 200:  # a stray "*" must not hold the whole reply
            out, buf = clean_spoken(buf), ""
            if out:
                yield out
    if buf:
        out = clean_spoken(buf)
        if out:
            yield out


# ─────────────────────────── the agent ───────────────────────────
_OTP_PENDING = ("\n\nOTP STATUS: right after the caller's first answer the system texts a 6-digit code to their "
                "registered mobile; your first reply tells them. Do not call send_otp yourself.")


def _turn_detection():
    """End-of-turn detector, chosen in .env (TURN_DETECTION). The LiveKit default first tries LiveKit
    Cloud (we don't use it: '401 Unauthorized' on every call) and only then falls back to the local model."""
    mode = config.TURN_DETECTION
    if mode in ("vad", "stt"):
        return mode
    if mode == "cloud":
        return inference.TurnDetector()
    return inference.TurnDetector(version="v1-mini")  # local model, no cloud attempt


class ClaimsAgent(Agent):
    def __init__(self, reporter: Reporter, mode: str, instructions: str):
        super().__init__(instructions=instructions + _OTP_PENDING)
        self._reporter = reporter
        self._mode = mode
        self._base_instructions = instructions
        self._otp_done = False

    async def on_enter(self) -> None:
        # Fixed opening line: fast, and always includes the "you're talking to an AI" + recording disclosures.
        # It ends with "Who am I speaking with today?", so the caller answers first (usually with their name).
        self.session.say(greeting(self._mode), allow_interruptions=False)

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        """After the caller's FIRST answer: text the OTP now, and tell the AI to say so in this reply
        (so the SMS arrives while she is talking about it)."""
        if self._otp_done:
            return
        self._otp_done = True
        otp = await self._reporter.send_otp()
        ending = otp.get("sent_to_mobile_ending") if otp.get("ok") else None
        turn_ctx.add_message(role="system", content=otp_turn_instructions(self._mode, ending))
        # permanent note for the rest of the call (and the truth when the SMS could NOT be sent)
        await self.update_instructions(self._base_instructions + otp_status(ending))

    async def transcription_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncIterable[str]:
        """The AI's words as they are produced -> live 'typing' on the console."""
        buf, last = "", 0.0
        async for chunk in clean_stream(text):
            buf += chunk
            now = time.monotonic()
            if now - last > 0.25:  # ~4 updates/s instead of one HTTP call per token
                self._reporter.emit("ai.live", text=buf)
                last = now
            yield chunk
        if buf:
            self._reporter.emit("ai.live", text=buf)

    async def tts_node(self, text: AsyncIterable[str], model_settings: ModelSettings):
        """Same cleaning before the words reach Voxtral TTS, so nothing odd is pronounced."""
        async for frame in Agent.default.tts_node(self, clean_stream(text), model_settings):
            yield frame


# ─────────────────────────── Step 2: dual-channel transcription ───────────────────────────
async def transcribe_channel(track: rtc.Track, speaker: str, stt_engine: GatedVoxtralSTT,
                             reporter: Reporter, started_at: float) -> None:
    """One participant = one audio channel = one Voxtral Realtime stream (no diarisation needed)."""
    stream = stt_engine.stream()
    audio = rtc.AudioStream.from_track(track=track, sample_rate=SR, num_channels=1)

    async def push() -> None:
        async for ev in audio:
            stream.push_frame(ev.frame)
        stream.end_input()

    pusher = asyncio.create_task(push())
    try:
        async for se in stream:
            if se.type == stt.SpeechEventType.INTERIM_TRANSCRIPT and se.alternatives:
                reporter.emit(f"{speaker}.live", text=se.alternatives[0].text)
            elif se.type == stt.SpeechEventType.FINAL_TRANSCRIPT and se.alternatives:
                text = se.alternatives[0].text.strip()
                if text:
                    reporter.emit("line", speaker=speaker, text=text, phase="interview",
                                  t=round(time.time() - started_at, 2))
                else:
                    reporter.emit(f"{speaker}.live", text="")  # noise/filler: clear "still talking"
    finally:
        pusher.cancel()
        try:
            await audio.aclose()  # release the native stream (see Recorder.stop)
        except Exception:
            pass
        await stream.aclose()


# ─────────────────────────── worker ───────────────────────────
def prewarm(proc: JobProcess) -> None:
    """Runs BEFORE a call reaches this process: load the VAD model and build the Mistral clients here.
    (Building them inside the call blocked the audio loop ~3 s on Windows while TLS certificates loaded,
    which made the first seconds of the AI voice choppy.)"""
    # 8 kHz VAD = half the CPU of 16 kHz with the same accuracy for speech/silence; on a busy laptop the
    # 16 kHz model ran slower than real time ("VAD inference is slower than realtime"), which clipped the
    # start of answers (lost OTP digits) and slowed turn-taking.
    vad = silero.VAD.load(min_silence_duration=0.45, sample_rate=8000)
    proc.userdata["vad"] = vad
    proc.userdata["stt"] = GatedVoxtralSTT(model=config.STT_MODEL, target_streaming_delay_ms=config.STT_DELAY_MS, vad=vad)
    proc.userdata["llm"] = mistralai.LLM(model=config.LLM_MODEL, temperature=0.3)
    proc.userdata["tts"] = mistralai.TTS(model=config.TTS_MODEL, voice=config.AGENT_VOICE, response_format="pcm")


_local_lk = any(h in config.LIVEKIT_URL for h in ("localhost", "127.0.0.1"))
server = AgentServer(
    setup_fnc=prewarm,
    # Windows runs calls as threads of ONE process (LiveKit's default there): warming a spare runner would
    # load models on the same CPU/GIL while a call is speaking, so only keep a warm spare on Linux/macOS.
    num_idle_processes=0 if sys.platform == "win32" else 1,
    initialize_process_timeout=30.0,  # a slow laptop can need >10 s to load the VAD + clients
    shutdown_process_timeout=30.0,
    log_level="INFO",
    **({"http_proxy": None} if _local_lk else {}),
)


@server.rtc_session(agent_name=config.AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    meta = json.loads(ctx.job.metadata or "{}")
    call_id: str = meta.get("call_id", ctx.room.name)
    mode: str = meta.get("mode", "intake")
    caller_name, caller_number = meta.get("caller_name"), meta.get("caller_number")
    started_at = time.time()

    reporter = Reporter(call_id)
    vad = ctx.proc.userdata["vad"]
    stt_engine = ctx.proc.userdata.get("stt") or GatedVoxtralSTT(
        model=config.STT_MODEL, target_streaming_delay_ms=config.STT_DELAY_MS, vad=vad)

    bank_tools = MCPServerHTTP(
        config.MCP_URL,
        transport_type="streamable_http",
        headers={"X-Call-Id": call_id},
        allowed_tools=STATUS_TOOLS if mode == "status" else INTAKE_TOOLS,
        timeout=10,
        client_session_timeout_seconds=15,
    )
    session = AgentSession(
        vad=vad,
        stt=stt_engine,
        llm=ctx.proc.userdata.get("llm") or mistralai.LLM(model=config.LLM_MODEL, temperature=0.3),
        tts=ctx.proc.userdata.get("tts") or mistralai.TTS(model=config.TTS_MODEL, voice=config.AGENT_VOICE, response_format="pcm"),
        mcp_servers=[bank_tools],
        max_tool_steps=6,
        user_away_timeout=10.0,  # caller silent for 10 s -> we gently check in (see below)
        turn_handling={
            # barge-in with the local Silero VAD only. The default "adaptive" detector lives in
            # LiveKit Cloud (which we don't use); trying it first made interruptions slow.
            "interruption": {"mode": "vad"},
            # end of turn: local model or VAD (see _turn_detection), never a LiveKit Cloud attempt first
            "turn_detection": _turn_detection(),
        },
    )
    instructions = (status_instructions(caller_name, meta.get("claim_ref")) if mode == "status"
                    else intake_instructions(caller_name, caller_number))

    live_user = {"final": "", "interim": ""}
    usage = {"stt_audio_seconds": 0.0, "tts_characters": 0, "llm_prompt_tokens": 0, "llm_completion_tokens": 0}
    turn: dict[str, dict] = {}
    handed_over = {"done": False}

    @session.on("user_input_transcribed")
    def _on_user_transcribed(ev: UserInputTranscribedEvent) -> None:
        if handed_over["done"]:
            return
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
        if item.role == "assistant":
            text = clean_spoken(text).strip()
        if not text:
            return
        if item.role == "user":
            live_user["final"] = live_user["interim"] = ""
        reporter.emit("line", speaker="customer" if item.role == "user" else "ai", text=text, phase="ai",
                      t=round(max(0.0, item.created_at - started_at), 2), interrupted=bool(item.interrupted))

    transferring = {"on": False}

    @session.on("function_tools_executed")
    def _on_tools(ev) -> None:
        if any(c.name == "transfer_to_specialist" for c in ev.function_calls):
            transferring["on"] = True  # the AI says ONE transfer sentence, then stops replying

    @session.on("agent_state_changed")
    def _on_agent_state(ev: AgentStateChangedEvent) -> None:
        if transferring["on"] and not handed_over["done"] and ev.old_state == "speaking" and ev.new_state == "listening":
            # transfer sentence finished: stop answering the caller ("okay", "thanks"...) until the specialist joins
            session.input.set_audio_enabled(False)
            reporter.emit("agent.state", state="transferring")
            return
        reporter.emit("agent.state", state="assisting" if handed_over["done"] else
                      ("transferring" if transferring["on"] and ev.new_state != "speaking" else ev.new_state))

    away = {"count": 0}

    @session.on("user_state_changed")
    def _on_user_state(ev: UserStateChangedEvent) -> None:
        reporter.emit("user.state", state=ev.new_state)
        # never leave the call hanging in silence: check in twice, then offer a human
        if ev.new_state == "speaking":
            away["count"] = 0
        elif (ev.new_state == "away" and not handed_over["done"] and not transferring["on"]
              and session.agent_state == "listening"):
            away["count"] += 1
            if away["count"] <= 2:
                session.generate_reply(instructions=(
                    "The caller has gone quiet. In one short, warm sentence check they are still there "
                    "and briefly repeat your last question. Do not call any tools."))
            elif away["count"] == 3:
                session.say("I can't hear you at the moment. If you're having trouble, stay on the line "
                            "and I'll connect you to one of our fraud specialists.")

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        m = ev.metrics
        if isinstance(m, metrics.STTMetrics):
            usage["stt_audio_seconds"] += m.audio_duration or 0
        elif isinstance(m, metrics.TTSMetrics):
            usage["tts_characters"] += m.characters_count or 0
        elif isinstance(m, metrics.LLMMetrics):
            usage["llm_prompt_tokens"] += m.prompt_tokens or 0
            usage["llm_completion_tokens"] += m.completion_tokens or 0
        # latency per turn = end-of-utterance delay + LLM time-to-first-token + TTS time-to-first-byte
        sid = getattr(m, "speech_id", None)
        if not sid:
            return
        t = turn.setdefault(sid, {})
        if isinstance(m, metrics.EOUMetrics):
            t["eou"] = round(m.end_of_utterance_delay, 3)
        elif isinstance(m, metrics.LLMMetrics):
            t["llm_ttft"] = round(m.ttft, 3)
        elif isinstance(m, metrics.TTSMetrics):
            t["tts_ttfb"] = round(m.ttfb, 3)
        if {"eou", "llm_ttft", "tts_ttfb"} <= t.keys() and "sent" not in t:
            t["sent"] = True
            reporter.emit("metrics.turn", speech_id=sid, eou=t["eou"], llm_ttft=t["llm_ttft"],
                          tts_ttfb=t["tts_ttfb"], total=round(t["eou"] + t["llm_ttft"] + t["tts_ttfb"], 3))

    last_stt_apology = {"t": 0.0}

    @session.on("error")
    def _on_error(ev) -> None:
        err = ev.error
        recoverable = bool(getattr(err, "recoverable", False)) or getattr(err, "type", "") == "stt_error"
        raw = str(getattr(err, "error", err))
        if getattr(getattr(err, "error", None), "status_code", None) in (401, 403, 404):
            recoverable = False
        log.warning("model error (recoverable=%s): %s", recoverable, raw[:300])
        if recoverable:
            part = {"stt_error": "Transcription", "tts_error": "Voice", "llm_error": "AI"}.get(getattr(err, "type", ""), "Model")
            reporter.emit("agent.warning", message=f"{part} hiccup, recovered automatically")
            # the transcription stream dropped (e.g. Voxtral code 3804): what the caller said in that moment is
            # lost, so ask them to repeat it instead of leaving a long silence (at most once every 20 s)
            now = time.monotonic()
            if (getattr(err, "type", "") == "stt_error" and not handed_over["done"] and not transferring["on"]
                    and session.agent_state != "speaking" and now - last_stt_apology["t"] > 20):
                last_stt_apology["t"] = now
                try:
                    session.say("Sorry, the line broke up for a second and I missed that. Could you say it again?")
                except Exception:
                    pass
        else:
            reporter.emit("agent.error", message=raw[:300])
            # don't go silent on the caller if the brain/voice fails for good
            if getattr(err, "type", "") == "llm_error" and not handed_over["done"]:
                try:
                    session.say("Sorry, I'm having a small technical problem. Could you say that again?")
                except Exception:
                    pass

    # ── Step 2: when the specialist joins, the AI goes quiet and transcribes both channels ──
    channel_tasks: list[asyncio.Task] = []

    def start_channel(participant: rtc.RemoteParticipant, speaker: str) -> None:
        for pub in participant.track_publications.values():
            if pub.track and pub.kind == rtc.TrackKind.KIND_AUDIO:
                channel_tasks.append(asyncio.create_task(
                    transcribe_channel(pub.track, speaker, stt_engine, reporter, started_at)))

    def enter_interview(specialist: rtc.RemoteParticipant) -> None:
        if handed_over["done"]:
            return
        handed_over["done"] = True
        try:
            session.interrupt(force=True)
        except RuntimeError:
            pass
        session.input.set_audio_enabled(False)
        session.output.set_audio_enabled(False)
        reporter.emit("agent.state", state="assisting")
        for p in ctx.room.remote_participants.values():
            if p.identity.startswith("customer-"):
                start_channel(p, "customer")
        start_channel(specialist, "specialist")

    @ctx.room.on("track_subscribed")
    def _on_track(track: rtc.Track, _pub, participant: rtc.RemoteParticipant):
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        if participant.identity.startswith("specialist-"):
            if handed_over["done"]:
                channel_tasks.append(asyncio.create_task(
                    transcribe_channel(track, "specialist", stt_engine, reporter, started_at)))
            else:
                enter_interview(participant)

    # ── recording + clean shutdown ──
    recorder = Recorder(ctx.room.name, call_id) if config.RECORD_CALLS else None

    async def _shutdown(reason: str = "") -> None:
        for t in channel_tasks:
            t.cancel()
        await asyncio.gather(*channel_tasks, return_exceptions=True)
        audio_file = await recorder.stop() if recorder else None
        reporter.emit("call.ended", end_reason=str(reason) or None, audio_file=audio_file,
                      usage={k: round(v, 2) for k, v in usage.items()})
        await reporter.aclose()

    ctx.add_shutdown_callback(_shutdown)

    # connect to the room FIRST: with a fixed participant_identity, RoomIO needs the local participant
    await ctx.connect()
    # start the recorder BEFORE the greeting, so the recording includes the AI + recording disclosures (QA)
    if recorder:
        try:
            await recorder.start()
        except Exception as e:
            log.warning("recorder failed to start: %s", e)
    await session.start(
        agent=ClaimsAgent(reporter, mode, instructions),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            participant_identity=f"customer-{call_id}",
            delete_room_on_close=True,  # caller closes the tab -> room closes -> call is saved + summarised
            audio_input=room_io.AudioInputOptions(sample_rate=16000),  # = Voxtral's rate: no extra resampling
        ),
    )
    reporter.emit("agent.joined", mode=mode)

    if config.AMBIENT_SOUND:
        background = BackgroundAudioPlayer(
            ambient_sound=AudioConfig(BuiltinAudioClip.OFFICE_AMBIENCE, volume=0.25),
            thinking_sound=[AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.5)],
        )
        await background.start(room=ctx.room, agent_session=session)


if __name__ == "__main__":
    if not config.MISTRAL_API_KEY:
        raise SystemExit("MISTRAL_API_KEY is missing. Put it in the .env file in the project folder.")
    cli.run_app(server)
