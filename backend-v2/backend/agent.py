"""
The AI voice agent, v2: no LiveKit Agents framework, no plugins, no Silero, no separate worker process.

Per call, a CallAgent inside the backend process BRIDGES two rooms:
  browser phone  <->  local LiveKit room (call.room)  <->  CallAgent  <->  Mistral-managed voice session
The Mistral session (client.audio.sessions.init) does turn-taking, barge-in, Voxtral STT, Mistral LLM and
Voxtral TTS in Mistral's cloud, and calls our bank tools (mcp_tools.make_tools) over its RPC channel.
In the local room the CallAgent is an AGENT-kind participant, so the existing frontend (LiveKit JS SDK)
works unchanged: it hears the AI, sees "AI speaking/listening", and gets captions.

Why a bridge (and not the browser joining Mistral's room directly): a session has ONE participant token,
and the SDK serves @function_tool calls from whoever joins with it. If the browser took the token, no
tool (OTP, card block, claim) could ever run on the backend.
"""

from __future__ import annotations

from . import winssl  # noqa: F401  (Windows: cache the certificate store, see winssl.py)

import asyncio
import logging
import re
import time
import wave
from collections import deque
from typing import AsyncIterator

import numpy as np
from livekit import rtc
from mistralai.client.models import (
    AudioFormat,
    RealtimeTranscriptionError,
    TranscriptionStreamDone,
    TranscriptionStreamTextDelta,
)
from mistralai.extra.voice_agents import AudioInterface, VoiceAgentSession

from . import claims, config, lk, llm, state
from .mcp_tools import issue_otp, make_tools
from .prompts import greeting, intake_instructions, otp_status, otp_turn_instructions, status_instructions

log = logging.getLogger("claims-agent")

SR = 16000        # recorder + interview transcription (= Voxtral Realtime's rate, no extra resampling)
VOICE_SR = 48000  # the Mistral voice session speaks 48 kHz mono signed 16-bit PCM

_agents: dict[str, "CallAgent"] = {}


# ─────────────────────────── public API (used by server.py) ───────────────────────────
def start(call: state.Call) -> bool:
    """Answer the call: the AI joins the call's room and starts talking."""
    if call.call_id in _agents:
        return False
    agent = CallAgent(call)
    _agents[call.call_id] = agent
    agent.task = asyncio.create_task(agent.run())
    return True


def stop(call_id: str, reason: str = "stopped") -> None:
    agent = _agents.get(call_id)
    if agent:
        agent.end(reason)


def running(call_id: str) -> bool:
    return call_id in _agents


def stop_all(reason: str) -> list[asyncio.Task]:
    tasks = [a.task for a in _agents.values() if a.task]
    for a in list(_agents.values()):
        a.end(reason)
    return tasks


# ─────────────────────────── event reporter ───────────────────────────
class Reporter:
    """Hands events to the backend in order, without ever blocking the conversation.
    (v1 posted them over HTTP from the agent process; now it is a function call in the same process.)"""

    def __init__(self, call_id: str):
        self.call_id = call_id
        self._q: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()
        self._task = asyncio.create_task(self._run())

    def emit(self, type_: str, **data) -> None:
        self._q.put_nowait((type_, data))

    async def _run(self) -> None:
        from . import server  # late import: server imports this module
        while (item := await self._q.get()) is not None:
            try:
                await server.handle_event(self.call_id, item[0], item[1])
            except Exception:
                log.exception("could not handle %s", item[0])

    async def aclose(self) -> None:
        self._q.put_nowait(None)
        await self._task


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
        tok = lk.token(f"recorder-{self.call_id}", "recorder", self.room_name, hidden=True, can_publish=False)

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
        # Close the native audio streams FIRST: a stream that is only "cancelled" keeps receiving frames
        # in the LiveKit engine after the call ends ("error putting to queue: Event loop is closed").
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
    """Remove things that must never be shown as spoken: *stage directions*, **bold** markers, list dashes."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)        # **bold** -> bold
    text = re.sub(r"\*[^*\n]*\*", "", text)               # *silence until transfer* -> (nothing)
    text = text.replace("*", "")
    text = re.sub(r"(^|\s)-\s+(?=\w)", r"\1", text)       # " - Ten thousand" -> " Ten thousand"
    return re.sub(r"[ \t]{2,}", " ", text)


# ─────────────────────────── audio: room <-> Mistral session ───────────────────────────
class RoomAudio(AudioInterface):
    """The session's "microphone" is the caller's track in the local room; its "speaker" is our
    published track. Both are 48 kHz mono s16le, the session's native format."""

    MAX_BACKLOG = 25  # 10 ms frames: never queue more than ~250 ms (late audio = slow turn-taking)

    def __init__(self, source: rtc.AudioSource):
        self._source = source
        self._q: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._running = False
        self.output_enabled = True

    async def start(self) -> None:
        self._running = True

    def feed(self, pcm: bytes) -> None:
        if self._q.qsize() > self.MAX_BACKLOG:
            try:
                self._q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._q.put_nowait(pcm)

    def input_audio(self) -> AsyncIterator[bytes]:
        return self._read()

    async def _read(self) -> AsyncIterator[bytes]:
        while self._running:
            chunk = await self._q.get()
            if chunk is None:
                return
            yield chunk

    async def output_audio(self, audio: bytes) -> None:
        if self.output_enabled and audio:
            await self._source.capture_frame(rtc.AudioFrame(audio, VOICE_SR, 1, len(audio) // 2))

    async def stop(self) -> None:
        self._running = False
        self._q.put_nowait(None)


# ─────────────────────────── Step 2: interview transcription ───────────────────────────
class _SpeechGate:
    """A tiny energy-based speech detector (replaces Silero VAD for the two interview channels): send audio
    to Voxtral only while someone speaks (plus a little pre-roll), and flush after a pause so Voxtral
    finalises the sentence."""

    MIN_RMS = 300.0      # int16 RMS; browser mics run with noise suppression + AGC
    END_SILENCE_S = 1.0  # shorter splits "No, | I didn't make that payment" at the comma
    PREROLL_FRAMES = 50  # ~500 ms of 10 ms frames

    def __init__(self) -> None:
        self.floor = 200.0
        self.speaking = False
        self.quiet = 0.0
        self.preroll: deque[bytes] = deque(maxlen=self.PREROLL_FRAMES)

    def push(self, pcm: bytes, seconds: float) -> tuple[list[bytes], bool]:
        """-> (chunks to send now, end of speech)"""
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
        loud = rms > max(self.MIN_RMS, self.floor * 3)
        if not loud:
            self.floor = 0.95 * self.floor + 0.05 * rms  # follow the background noise level
        if self.speaking:
            self.quiet = 0.0 if loud else self.quiet + seconds
            if self.quiet >= self.END_SILENCE_S:
                self.speaking = False
                return [pcm], True
            return [pcm], False
        if loud:
            self.speaking, self.quiet = True, 0.0
            out = [*self.preroll, pcm]
            self.preroll.clear()
            return out, False
        self.preroll.append(pcm)
        return [], False


class _VoxtralChannel:
    """One Voxtral Realtime stream that survives drops. The audio of every utterance is kept until Voxtral has
    returned its transcript; if the stream drops (e.g. "1011 Upstream connection error") or stalls (no
    transcript several seconds after the pause), a fresh connection is opened and that audio is sent again,
    so nothing the caller said is lost. (v1's voxtral_stt.py had the same stall watchdog.)"""

    STALL_S = 6.0      # flushed, but no transcript after this long -> reconnect + resend
    MAX_WAITING = 5    # utterances kept for resending

    def __init__(self, speaker: str, reporter: Reporter, started_at: float, usage: dict):
        self.speaker, self.reporter, self.started_at, self.usage = speaker, reporter, started_at, usage
        self.conn = None
        self.recv: asyncio.Task | None = None
        self.current: list[bytes] = []                     # utterance being spoken
        self.waiting: deque[tuple[list[bytes], float]] = deque()  # flushed, transcript not back yet
        self.retry_at = 0.0  # after a failed connect, wait before trying again (not on every 10 ms frame)

    def _alive(self) -> bool:
        return self.conn is not None and not self.conn.is_closed and self.recv is not None and not self.recv.done()

    async def _drop(self) -> None:
        conn, self.conn = self.conn, None
        if self.recv:
            self.recv.cancel()
            self.recv = None
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass

    async def _open(self) -> bool:
        if time.monotonic() < self.retry_at:
            return False
        try:
            conn = await llm.client().audio.realtime.connect(
                model=config.STT_MODEL, audio_format=AudioFormat(encoding="pcm_s16le", sample_rate=SR),
                target_streaming_delay_ms=config.STT_DELAY_MS)
        except Exception as e:
            log.warning("could not open Voxtral realtime (%s): %s", self.speaker, e)
            self.reporter.emit("agent.warning", message="Transcription hiccup, retrying")
            self.retry_at = time.monotonic() + 2.0
            return False
        self.conn = conn
        self.recv = asyncio.create_task(self._receive(conn))
        try:
            if self.waiting:  # what Voxtral never transcribed on the old connection
                log.info("Voxtral (%s): re-sending %d utterance(s) on a new connection", self.speaker, len(self.waiting))
                now = time.monotonic()
                self.waiting = deque((utt, now) for utt, _ in self.waiting)
                for utt, _ in self.waiting:
                    for c in utt:
                        await conn.send_audio(c)
                    await conn.flush_audio()
            for c in self.current:
                await conn.send_audio(c)
        except Exception as e:
            log.warning("Voxtral realtime resend (%s) failed: %s", self.speaker, e)
            await self._drop()
            return False
        return True

    async def _receive(self, c) -> None:
        text = ""
        try:
            async for ev in c.events():
                if isinstance(ev, TranscriptionStreamTextDelta):
                    text += ev.text
                    self.reporter.emit(f"{self.speaker}.live", text=text)
                elif isinstance(ev, TranscriptionStreamDone):
                    if self.waiting:
                        self.waiting.popleft()
                    final, text = (ev.text or text).strip(), ""
                    if final:
                        self.reporter.emit("line", speaker=self.speaker, text=final, phase="interview",
                                           t=round(time.time() - self.started_at, 2))
                    else:
                        self.reporter.emit(f"{self.speaker}.live", text="")  # noise/filler: clear "still talking"
                elif isinstance(ev, RealtimeTranscriptionError):
                    log.warning("Voxtral realtime error (%s): %s", self.speaker, ev)
                    return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("Voxtral realtime stream (%s) dropped: %s", self.speaker, e)

    async def push(self, chunks: list[bytes], ended: bool) -> None:
        self.current.extend(chunks)
        self.usage["stt_audio_seconds"] += sum(len(c) for c in chunks) / 2 / SR
        try:
            if self._alive():
                for c in chunks:
                    await self.conn.send_audio(c)
            else:
                await self._drop()
                if not await self._open():  # (a successful open already sent self.current)
                    if ended:
                        self._park()
                    return
            if ended:
                await self.conn.flush_audio()
                self._park()
        except Exception as e:
            log.warning("Voxtral realtime send (%s) failed: %s", self.speaker, e)
            if ended:
                self._park()
            await self._drop()

    def _park(self) -> None:
        self.waiting.append((self.current, time.monotonic()))
        self.current = []
        while len(self.waiting) > self.MAX_WAITING:
            self.waiting.popleft()

    async def watchdog(self) -> None:
        """Called on every frame, also in silence: recover a dropped or stalled stream right away."""
        if not self.waiting:
            return
        stalled = time.monotonic() - self.waiting[0][1] > self.STALL_S
        if stalled and self._alive():
            log.warning("Voxtral (%s): no transcript %.0fs after the pause: reconnecting", self.speaker, self.STALL_S)
            await self._drop()
        if not self._alive():
            await self._drop()
            await self._open()

    async def close(self) -> None:
        await self._drop()


async def transcribe_channel(track: rtc.Track, speaker: str, reporter: Reporter, started_at: float,
                             usage: dict) -> None:
    """One participant = one audio channel = one Voxtral Realtime stream (no diarisation needed)."""
    audio = rtc.AudioStream.from_track(track=track, sample_rate=SR, num_channels=1)
    gate = _SpeechGate()
    channel = _VoxtralChannel(speaker, reporter, started_at, usage)
    try:
        async for ev in audio:
            frame = ev.frame
            chunks, ended = gate.push(frame.data.tobytes(), frame.samples_per_channel / SR)
            if chunks:
                await channel.push(chunks, ended)
            else:
                await channel.watchdog()
    finally:
        await channel.close()
        try:
            await audio.aclose()  # release the native stream (see Recorder.stop)
        except Exception:
            pass


# ─────────────────────────── the agent of ONE call ───────────────────────────
def _ms(v) -> float:
    return float(v) / 1000 if isinstance(v, (int, float)) else 0.0


def _otp_note(mode: str, ending: str | None) -> str:
    """The code is texted the moment the AI answers. The managed session cannot inject a system message into
    one specific turn (v1 did that after the caller's first answer), so the first-reply task lives in the
    instructions, marked as one-time."""
    first = otp_turn_instructions(mode, ending).replace("THIS IS YOUR FIRST REPLY OF THE CALL. ", "")
    if ending:
        status = (f"\n\nOTP STATUS: when the call connected, the system texted a 6-digit code to the caller's mobile "
                  f"ending {ending}. Do NOT call send_otp unless they say they did not receive it. "
                  "To verify, ask them to read it and call verify_otp.")
    else:
        status = otp_status(None)
    return f"{status}\nYOUR FIRST REPLY, right after the caller answers your greeting (only then, never again): {first}"


class CallAgent:
    def __init__(self, call: state.Call):
        self.call_id = call.call_id
        self.mode = call.mode
        self.room_name = call.room
        self.caller_name, self.caller_number = call.caller_name, call.caller_number
        self.claim_ref = (claims.get(call.claim_id) or {}).get("reference") if call.claim_id else None
        self.customer_identity = f"customer-{call.call_id}"
        self.started_at = time.time()
        self.task: asyncio.Task | None = None

        self.reporter = Reporter(call.call_id)
        self.room = rtc.Room()
        self.source = rtc.AudioSource(VOICE_SR, 1, queue_size_ms=200)
        self.audio = RoomAudio(self.source)
        self.session: VoiceAgentSession | None = None
        self.recorder = Recorder(call.room, call.call_id) if config.RECORD_CALLS else None

        self.handed_over = False
        self.transferring = False
        self.closing = False
        self.ended = asyncio.Event()
        self.end_reason = ""
        self.ai_started_at: float | None = None
        self.ai_ended_at: float | None = None
        self.usage = {"stt_audio_seconds": 0.0, "tts_characters": 0, "llm_prompt_tokens": 0, "llm_completion_tokens": 0}
        self.user_state: str | None = None
        self.last_ai_live = 0.0
        self.ai_unfinished = ""  # the AI's sentence still being spoken (not final yet)
        self.final_ids: set[str] = set()
        self.user_turn: list[tuple[str, str]] = []  # (text, segment id) of the caller's current turn
        self.metrics_task: asyncio.Task | None = None
        self.close_task: asyncio.Task | None = None  # closing the Mistral session at the handover
        self.mic_tracks: set[str] = set()
        self.tasks: set[asyncio.Task] = set()
        self.channel_tasks: list[asyncio.Task] = []

    # ── lifecycle ──
    def end(self, reason: str) -> None:
        if not self.ended.is_set():
            self.end_reason = reason
            self.ended.set()

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)
        return t

    async def run(self) -> None:
        try:
            await self._start()
            await self.ended.wait()
        except asyncio.CancelledError:
            self.end_reason = self.end_reason or "stopped"
        except Exception as e:
            log.exception("voice agent failed for %s", self.call_id)
            self.reporter.emit("agent.error", message=str(e)[:300])
            self.end_reason = "agent_error"
        finally:
            await self._shutdown()

    async def _start(self) -> None:
        room = self.room
        room.on("track_subscribed", self._on_track)
        room.on("participant_disconnected", self._on_participant_left)
        room.on("disconnected", lambda *_: self.end("room_closed"))
        await room.connect(config.LIVEKIT_URL,
                           lk.token(f"agent-{self.call_id}", config.AGENT_PERSONA, self.room_name, agent=True))
        track = rtc.LocalAudioTrack.create_audio_track("agent-voice", self.source)
        await room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        self._spawn(self._set_lk_state("initializing"))
        # start the recorder BEFORE the greeting, so the recording includes the AI + recording disclosures (QA)
        if self.recorder:
            try:
                await self.recorder.start()
            except Exception as e:
                log.warning("recorder failed to start: %s", e)
                self.recorder = None

        call = state.calls[self.call_id]
        otp = await issue_otp(call, reason="sent automatically when the call connected")
        ending = otp.get("sent_to_mobile_ending") if otp.get("ok") else None
        base = (status_instructions(self.caller_name, self.claim_ref) if self.mode == "status"
                else intake_instructions(self.caller_name, self.caller_number))

        session = await llm.client().audio.sessions.init(
            instructions=base + _otp_note(self.mode, ending),
            # fixed opening line: always includes the "you're talking to an AI" + recording disclosures
            welcome_message=greeting(self.mode),
            tools=make_tools(self.call_id, self.mode),
            audio_interface=self.audio,
            turn={"allow_interruptions": True, "inactivity": {
                "timeout_ms": int(config.SILENCE_CHECK_SECONDS * 1000), "max_reminders": 2,
                "messages": ["Are you still there? Take your time.",
                             "I can't hear you at the moment. If you're having trouble, stay on the line and "
                             "I'll connect you to one of our fraud specialists."]}},
            **_model_config(),
        )
        self.session = session
        session.on("transcript", self._on_transcript)
        session.on("agent_state_changed", self._on_agent_state)
        session.on("tool_call_ended", self._on_tool_ended)
        session.on("unhandled_tool", lambda ev: log.warning("agent called an unknown tool: %s", ev.name))
        session.on("error", self._on_error)
        session.on("disconnected", self._on_session_closed)
        await session.connect()
        self.ai_started_at = time.time()
        self.reporter.emit("agent.joined", mode=self.mode)
        for p in room.remote_participants.values():  # the caller is usually in the room already
            for pub in p.track_publications.values():
                if pub.track and pub.kind == rtc.TrackKind.KIND_AUDIO:
                    self._on_track(pub.track, pub, p)

    async def _shutdown(self) -> None:
        if self.closing:
            return
        self.closing = True
        if not self.handed_over:
            self._flush_user_turn()
        for t in self.channel_tasks:
            t.cancel()
        await asyncio.gather(*self.channel_tasks, return_exceptions=True)
        if self.close_task is not None:
            await asyncio.gather(self.close_task, return_exceptions=True)
        elif self.session is not None:
            await self._close_session()
        audio_file = None
        if self.recorder:
            try:
                audio_file = await self.recorder.stop()
            except Exception as e:
                log.warning("recorder failed to save: %s", e)
        for t in list(self.tasks):
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        try:
            await self.room.disconnect()
        except Exception:
            pass
        try:
            await self.source.aclose()
        except Exception:
            pass
        self.usage["stt_audio_seconds"] += max(0.0, (self.ai_ended_at or time.time()) - (self.ai_started_at or time.time()))
        self.reporter.emit("call.ended", end_reason=self.end_reason or None, audio_file=audio_file,
                           usage={k: round(v, 2) for k, v in self.usage.items()})
        await self.reporter.aclose()
        _agents.pop(self.call_id, None)
        await lk.delete_room(self.room_name)  # the caller's phone hangs up too (v1: delete_room_on_close)

    async def _close_session(self) -> None:
        """Stop the Mistral session (and its costs). Our participant stays in the local room."""
        session = self.session
        if session is None:
            return
        self.ai_ended_at = self.ai_ended_at or time.time()
        await self._collect_metrics()
        try:
            await session.close()
        except Exception as e:
            log.info("session close: %s", e)

    # ── room events ──
    def _on_track(self, track: rtc.Track, _pub, participant: rtc.RemoteParticipant) -> None:
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        if participant.identity == self.customer_identity:
            if self.handed_over:
                self._start_channel(track, "customer")
            elif track.sid not in self.mic_tracks:
                self.mic_tracks.add(track.sid)
                self._spawn(self._pump_mic(track))
        elif participant.identity.startswith("specialist-"):
            if self.handed_over:
                self._start_channel(track, "specialist")
            else:
                self._enter_interview(participant)

    def _on_participant_left(self, participant: rtc.RemoteParticipant) -> None:
        if participant.identity == self.customer_identity:
            self.end("customer_left")  # caller closed the tab -> the call is saved + summarised

    async def _pump_mic(self, track: rtc.Track) -> None:
        stream = rtc.AudioStream.from_track(track=track, sample_rate=VOICE_SR, num_channels=1)
        try:
            async for ev in stream:
                if self.handed_over:
                    return
                self.audio.feed(ev.frame.data.tobytes())
        finally:
            try:
                await stream.aclose()
            except Exception:
                pass

    # ── Step 2: when the specialist joins, the AI goes quiet and transcribes both channels ──
    def _enter_interview(self, specialist: rtc.RemoteParticipant) -> None:
        if self.handed_over:
            return
        self._flush_user_turn()
        self._end_ai_speech()
        self.handed_over = True
        self.audio.output_enabled = False
        self.close_task = asyncio.create_task(self._close_session())
        self.reporter.emit("agent.state", state="assisting")
        self._spawn(self._set_lk_state("listening"))
        for p in self.room.remote_participants.values():
            if p.identity == self.customer_identity:
                for pub in p.track_publications.values():
                    if pub.track and pub.kind == rtc.TrackKind.KIND_AUDIO:
                        self._start_channel(pub.track, "customer")
        for pub in specialist.track_publications.values():
            if pub.track and pub.kind == rtc.TrackKind.KIND_AUDIO:
                self._start_channel(pub.track, "specialist")

    def _start_channel(self, track: rtc.Track, speaker: str) -> None:
        self.channel_tasks.append(asyncio.create_task(
            transcribe_channel(track, speaker, self.reporter, self.started_at, self.usage)))

    # ── Mistral session events ──
    def _t(self) -> float:
        return round(max(0.0, time.time() - self.started_at), 2)

    def _set_user_state(self, st: str) -> None:
        if st != self.user_state:
            self.user_state = st
            self.reporter.emit("user.state", state=st)

    def _on_transcript(self, ev) -> None:
        if self.handed_over or ev.id in self.final_ids:
            return
        if ev.role == "user":
            # The session finalises the caller's speech in segments ("No." + "I didn't make that payment").
            # Like v1, the transcript gets ONE line per caller turn: segments are collected until the AI answers.
            text = (ev.content or "").strip()
            done = " ".join(s for s, _ in self.user_turn)
            if ev.final:
                self.final_ids.add(ev.id)
                if text:
                    self.user_turn.append((text, ev.id))
                    self.reporter.emit("customer.live", text=f"{done} {text}".strip())
                elif not done:
                    self.reporter.emit("customer.live", text="")
                self._set_user_state("listening")
            elif text:
                self.reporter.emit("customer.live", text=f"{done} {text}".strip())
                self._set_user_state("speaking")
            return
        self._flush_user_turn()
        text = clean_spoken(ev.content or "").strip()
        if ev.final:
            self.final_ids.add(ev.id)
            self.ai_unfinished = ""
            if text:
                self.usage["tts_characters"] += len(text)
                self.reporter.emit("line", speaker="ai", text=text, phase="ai", t=self._t())
                self._spawn(self._caption(ev.id, text, None))
                self._refresh_metrics_soon()
            return
        self.ai_unfinished = text
        if text and time.monotonic() - self.last_ai_live > 0.25:  # ~4 updates/s, not one per token
            self.last_ai_live = time.monotonic()
            self.reporter.emit("ai.live", text=text)

    def _end_ai_speech(self) -> None:
        """The AI was cut off mid-sentence (the specialist joined): keep what it said as an interrupted line
        and clear its live "AI assistant" bubble, which would otherwise stay on the console all interview."""
        text, self.ai_unfinished = self.ai_unfinished, ""
        if text:
            self.usage["tts_characters"] += len(text)
            self.reporter.emit("line", speaker="ai", text=text, phase="ai", t=self._t(), interrupted=True)
        else:
            self.reporter.emit("ai.live", text="")

    def _flush_user_turn(self) -> None:
        if not self.user_turn:
            return
        text = " ".join(s for s, _ in self.user_turn)
        seg_id = self.user_turn[0][1]
        self.user_turn = []
        self.reporter.emit("line", speaker="customer", text=text, phase="ai", t=self._t())
        self._spawn(self._caption(seg_id, text, self.customer_identity))

    def _on_agent_state(self, ev) -> None:
        if self.handed_over:
            return
        if ev.state in ("thinking", "speaking"):  # the caller's turn is over
            self._flush_user_turn()
        st = "listening" if ev.state == "idle" else ev.state
        self._spawn(self._set_lk_state(st))
        if self.transferring and ev.old_state == "speaking" and st == "listening":
            # transfer sentence finished: stop answering the caller ("okay", "thanks"...) until the specialist joins
            self._spawn(self._mute_for_transfer())
            self.reporter.emit("agent.state", state="transferring")
            return
        self.reporter.emit("agent.state", state="transferring" if self.transferring and st != "speaking" else st)

    def _on_tool_ended(self, ev) -> None:
        log.info("tool %s -> %s", ev.name, ev.status)
        if ev.name == "transfer_to_specialist" and ev.status == "done":
            self.transferring = True  # the AI says ONE transfer sentence, then stops replying

    def _on_error(self, ev) -> None:
        log.warning("voice session error (fatal=%s, %s/%s): %s", ev.fatal, ev.source, ev.code, ev.message[:300])
        if ev.fatal:
            self.reporter.emit("agent.error", message=ev.message[:300])
        elif ev.code != "function_handler_configuration_error":
            part = {"stt": "Transcription", "tts": "Voice", "llm": "AI"}.get(ev.source, "Model")
            self.reporter.emit("agent.warning", message=f"{part} hiccup, recovered automatically")

    def _on_session_closed(self, ev) -> None:
        if not self.handed_over and not self.closing:
            self.end(ev.reason or "session_ended")

    async def _mute_for_transfer(self) -> None:
        if self.session is None:
            return
        try:
            await self.session.set_input_enabled(False)
            await self.session.update(turn={"inactivity": None})  # no "are you still there?" while waiting
        except Exception as e:
            log.info("could not mute the AI for the transfer: %s", e)

    # ── what the browser phone shows (LiveKit agent conventions used by @livekit/components-react) ──
    async def _set_lk_state(self, st: str) -> None:
        try:
            await self.room.local_participant.set_attributes({"lk.agent.state": st})
        except Exception:
            pass

    async def _caption(self, segment_id: str, text: str, sender: str | None) -> None:
        try:
            w = await self.room.local_participant.stream_text(
                topic="lk.transcription", sender_identity=sender,
                attributes={"lk.segment_id": segment_id, "lk.transcription_final": "true"})
            await w.write(text)
            await w.aclose()
        except Exception:
            pass

    # ── latency per turn, from the session's own transcript (end of turn + LLM first token + TTS first audio) ──
    def _refresh_metrics_soon(self) -> None:
        if self.metrics_task is None or self.metrics_task.done():
            async def later() -> None:
                await asyncio.sleep(2.0)
                await self._collect_metrics()
            self.metrics_task = self._spawn(later())

    async def _collect_metrics(self) -> None:
        if self.session is None:
            return
        try:
            snap = await llm.client().audio.sessions.get_async(session_id=self.session.id)
        except Exception as e:
            log.info("session snapshot failed: %s", e)
            return
        turns, user_lat = [], None
        for tr in getattr(snap, "transcripts", None) or []:
            role = getattr(tr, "role", None)
            if role == "user":
                user_lat = getattr(tr, "latency", None)
            elif role == "assistant" and user_lat is not None:  # the greeting has no caller turn before it
                lat = getattr(tr, "latency", None)
                eou = _ms(getattr(user_lat, "end_of_turn_delay_ms", None))
                ttft = _ms(getattr(lat, "llm_ttft_ms", None))
                ttfb = _ms(getattr(lat, "tts_ttfb_ms", None))
                turns.append({"eou": round(eou, 3), "llm_ttft": round(ttft, 3), "tts_ttfb": round(ttfb, 3),
                              "total": round(eou + ttft + ttfb, 3)})
                user_lat = None
        if turns:
            self.reporter.emit("metrics.turns", turns=turns)


def _model_config() -> dict:
    """Model overrides for the managed session; unset = Mistral's defaults."""
    out: dict = {"tts": {"voice_id": config.AGENT_VOICE}}
    if config.VOICE_TTS_MODEL:
        out["tts"]["model"] = config.VOICE_TTS_MODEL
    if config.VOICE_STT_MODEL:
        out["stt"] = {"model": config.VOICE_STT_MODEL}
    if config.VOICE_LLM_MODEL:
        out["llm"] = {"model": config.VOICE_LLM_MODEL}
    return out
