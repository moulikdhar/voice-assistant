
from __future__ import annotations

import logging

from . import llm
from .prompts import SUMMARY_SYSTEM

log = logging.getLogger("summarizer")


def format_transcript(lines: list[dict]) -> str:
    return "\n".join(f"[{i}] {ln['speaker']}: {ln['text']}" for i, ln in enumerate(lines))


async def summarize(lines: list[dict]) -> dict:
    if not lines:
        return {"short_summary": "No conversation was captured.", "error": None}
    try:
        return await llm.json_chat(SUMMARY_SYSTEM, "Transcript:\n" + format_transcript(lines))
    except Exception as e:  # never lose the transcript because the summary failed
        log.exception("summary failed")
        return {"short_summary": None, "error": f"summary failed: {e}"}
