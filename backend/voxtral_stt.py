

from __future__ import annotations

from collections import deque

from livekit.agents import utils, vad
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from livekit.plugins.mistralai import stt as mstt

CHUNK_MS = 100
PREROLL_MS = 500


class GatedSpeechStream(mstt.SpeechStream):
    async def _send_task(self, connection, vad_stream: vad.VADStream | None) -> None:  # type: ignore[override]
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
                    await connection.send_audio(chunk)
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
