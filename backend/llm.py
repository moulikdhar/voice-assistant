
from __future__ import annotations

import json
import logging
from pathlib import Path

from mistralai.client import Mistral

from . import config

log = logging.getLogger("llm")
_client: Mistral | None = None


def client() -> Mistral:
    global _client
    if _client is None:
        _client = Mistral(api_key=config.MISTRAL_API_KEY)
    return _client


async def json_chat(system: str, user: str, *, model: str | None = None, temperature: float = 0.1) -> dict:
    """Mistral Large in JSON mode. Raises on failure; callers decide how to degrade."""
    res = await client().chat.complete_async(
        model=model or config.SUMMARY_MODEL,
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    content = res.choices[0].message.content
    if isinstance(content, list):
        content = "".join(getattr(c, "text", "") for c in content)
    return json.loads(content)


async def transcribe_file(path: Path, context_bias: list[str]) -> dict:
    """Voxtral Mini Transcribe V2: speaker diarisation + segment timestamps + context biasing."""
    with open(path, "rb") as f:
        res = await client().audio.transcriptions.complete_async(
            model=config.BATCH_STT_MODEL,
            file={"content": f.read(), "file_name": path.name},
            diarize=True,
            timestamp_granularities=["segment"],
            context_bias=context_bias[:100],
        )
    segs = []
    for s in res.segments or []:
        segs.append({
            "speaker": getattr(s, "speaker_id", None),
            "start": round(s.start, 2) if s.start is not None else None,
            "end": round(s.end, 2) if s.end is not None else None,
            "text": s.text,
        })
    usage = getattr(res, "usage", None)
    return {"text": res.text, "segments": segs, "language": res.language,
            "audio_seconds": getattr(usage, "prompt_audio_seconds", None) if usage else None}
