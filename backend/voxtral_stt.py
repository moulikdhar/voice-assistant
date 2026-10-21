

from __future__ import annotations

import logging
import os
from collections import deque
from datetime import datetime, timezone

from livekit.agents import utils, vad
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from livekit.plugins.mistralai import stt as mstt

CHUNK_MS = 100
PREROLL_MS = 1500  # generous: if the VAD lags on a busy laptop, the first words are still sent
# Stall watchdog: if we have sent this many seconds of SPEECH and Mistral has sent back nothing at all
# (no text, no event), the stream is stuck. Reconnect at once instead of waiting for Mistral's own
# "Timeout waiting for response from streaming transcription" (code 3804), which came much later.
STALL_SPEECH_S = float(os.environ.get("STT_STALL_SECONDS", "5"))

log = logging.getLogger("voxtral-stt")


class _WatchedConnection:
    """Wraps the Mistral realtime connection: every event from the server resets the stall counter."""

    def __init__(self, conn, on_event) -> None:
        self._conn, self._on_event = conn, on_event

    async def events(self):
        async for ev in self._conn.events():
            self._on_event()
            yield ev

    def __getattr__(self, name):
        return getattr(self._conn, name)


class GatedSpeechStream(mstt.SpeechStream):
    _unanswered_s = 0.0  # seconds of speech sent since the server last sent anything
    _rid = ""            # Mistral request id of the current realtime session (for support tickets)

    def _server_spoke(self) -> None:
        self._unanswered_s = 0.0

    async def _recv_task(self, connection) -> None:  # type: ignore[override]
        self._rid = getattr(connection, "request_id", None) or self._request_id  # survives pooled re-use
        try:
            await super()._recv_task(_WatchedConnection(connection, self._server_spoke))
        except Exception as e:
            # everything Mistral support needs to find this session in their logs
            log.error("Voxtral realtime error: %s | request_id=%s model=%s delay_ms=%s utc=%s",
                      e, self._rid or "?", self._opts.model, self._opts.target_streaming_delay_ms,
                      datetime.now(timezone.utc).isoformat(timespec="seconds"))
            raise

    def _stalled(self, connection) -> bool:
        if self._unanswered_s < STALL_SPEECH_S:
            return False
        log.warning("Voxtral stream sent %.1fs of speech with no reply: reconnecting now | request_id=%s utc=%s",
                    self._unanswered_s, self._rid or "?",
                    datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self._unanswered_s = 0.0
        self._speaking = False          # the new connection gets a new VAD stream: let it detect speech again
        self._pool.remove(connection)  # never reuse the stuck connection
        self._reconnect_event.set()     # the plugin's _run loop opens a fresh connection
        return True

    async def _send_task(self, connection, vad_stream: vad.VADStream | None) -> None:  # type: ignore[override]
        self._unanswered_s = 0.0
        rate, ch = mstt.SAMPLE_RATE, mstt.NUM_CHANNELS
        bstream = utils.audio.AudioByteStream(
            sample_rate=rate, num_channels=ch, samples_per_channel=rate * CHUNK_MS // 1000
        )
        preroll: deque[bytes] = deque(maxlen=PREROLL_MS // CHUNK_MS)
        was_speaking = False

        async for data in self._input_ch:
            if isinstance(data, self._FlushSentinel):
                for frame in bstream.flush():
                    if self._speaking:
                        await connection.send_audio(frame.data.tobytes())
                await connection.flush_audio()
                continue

            if vad_stream is not None:
                vad_stream.push_frame(data)
            self._audio_duration += len(data.data.tobytes()) / (rate * ch * 2)

            for frame in bstream.write(data.data.tobytes()):
                chunk = frame.data.tobytes()
                speaking = self._speaking or vad_stream is None
                if speaking:
                    if not was_speaking:  # speech just started: send the pre-roll first
                        while preroll:
                            await connection.send_audio(preroll.popleft())
                            self._unanswered_s += CHUNK_MS / 1000
                    await connection.send_audio(chunk)
                    self._unanswered_s += CHUNK_MS / 1000
                    if self._stalled(connection):
                        return
                else:
                    preroll.append(chunk)  # keep a little audio, send nothing
                was_speaking = speaking

        if vad_stream is not None:
            vad_stream.end_input()
        await connection.end_audio()


class GatedVoxtralSTT(mstt.STT):
    """Drop-in replacement for `mistralai.STT` (realtime model)."""

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options=DEFAULT_API_CONNECT_OPTIONS,
    ) -> GatedSpeechStream:
        stream = GatedSpeechStream(
            stt=self,
            pool=self._pool,
            conn_options=conn_options,
            language=language,
            vad_instance=self._vad,
        )
        self._streams.add(stream)
        return stream
