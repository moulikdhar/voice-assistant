

from __future__ import annotations

import json
import logging

from mistralai.client import Mistral

from . import config
from .prompts import SUMMARY_SYSTEM

log = logging.getLogger("summarizer")


def _format_transcript(lines: list[dict]) -> str:
    return "\n".join(f"[{i}] {ln['speaker']}: {ln['text']}" for i, ln in enumerate(lines))


async def summarize(lines: list[dict]) -> dict:
    if not lines:
        return {"short_summary": "No conversation was captured.", "error": None}
    client = Mistral(api_key=config.MISTRAL_API_KEY)
    try:
        res = await client.chat.complete_async(
            model=config.SUMMARY_MODEL,
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM},
                {"role": "user", "content": "Transcript:\n" + _format_transcript(lines)},
            ],
        )
        content = res.choices[0].message.content
        if isinstance(content, list):  # content chunks
            content = "".join(getattr(c, "text", "") for c in content)
        return json.loads(content)
    except Exception as e:  # never lose the transcript because the summary failed
        log.exception("summary failed")
        return {"short_summary": None, "error": f"summary failed: {e}"}
