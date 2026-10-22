
from __future__ import annotations

import asyncio
import json
import logging

from . import bank, claims, hub, llm, playbooks, state
from .summarizer import format_transcript, summarize

log = logging.getLogger("assist")

_running: dict[str, asyncio.Task] = {}
_dirty: set[str] = set()


def _claim_or_none(call: state.Call) -> dict | None:
    return claims.get(call.claim_id) if call.claim_id else None


async def prepare_handover(call_id: str) -> None:
    call = state.calls.get(call_id)
    if not call:
        return
    summary = await summarize([ln for ln in call.lines if ln.get("phase", "ai") == "ai"])
    call.summary = summary
    await state.push(call)
    cl = _claim_or_none(call)
    if cl and call.mode == "intake":  # a call-back's summary must not replace the original intake summary
        cl["intake_summary"] = summary
        await claims.save(cl)
    await hub.broadcast({"type": "handover.ready", "call_id": call_id, "claim_id": call.claim_id, "summary": summary})
    if call.mode == "intake":
        request_update(call_id)  # first checklist pass straight from the intake transcript


def request_update(call_id: str) -> None:
    """Coalesce updates: at most one evaluation in flight per call, re-run if new lines arrived."""
    _dirty.add(call_id)
    t = _running.get(call_id)
    if not t or t.done():
        _running[call_id] = asyncio.create_task(_loop(call_id))


async def _loop(call_id: str) -> None:
    while call_id in _dirty:
        _dirty.discard(call_id)
        try:
            await _evaluate(call_id)
        except Exception as e:
            log.warning("assist evaluation failed: %s", e)
            await hub.broadcast({"type": "warning", "call_id": call_id, "message": "Checklist update failed, retrying on next line"})
        await asyncio.sleep(1.5)


ASSIST_SYSTEM = """
You are the live assistant on a bank fraud specialist's screen during a customer interview.
You NEVER decide liability; you only help the specialist cover the checklist.
Return ONLY JSON:
{
  "checklist": [{"id": "<checklist id>", "status": "answered|partial|open",
                 "answer": "short answer or null", "evidence": "exact quote or null",
                 "source": "intake|interview|bank_log|null"}],
  "disclosures": [{"id": "<disclosure id>", "done": true|false, "evidence": "exact specialist quote or null"}],
  "next_question": "the single best next question for the specialist to ask, in plain words",
  "alerts": ["short alerts, e.g. a statement that conflicts with the intake call or the bank's security log"],
  "policy_hint": "one short line of playbook guidance relevant right now, or null"
}
Include EVERY checklist id and EVERY disclosure id from the playbook. Use only facts in the inputs.
""".strip()


async def _evaluate(call_id: str) -> None:
    call = state.calls.get(call_id)
    if not call:
        return
    cl = _claim_or_none(call)
    juris = cl["jurisdiction"] if cl else "IN_RBI"
    sec = bank.security_log_for(call.customer_id) if call.customer_id else []
    intake = [ln for ln in call.lines if ln.get("phase", "ai") == "ai"]
    interview = [ln for ln in call.lines if ln.get("phase") == "interview"]
    user = "\n\n".join([
        "PLAYBOOK\n" + playbooks.as_text(juris),
        "CLAIM RECORD\n" + json.dumps({k: cl.get(k) for k in ("reference", "facts", "disputed_transactions")} if cl else {}, default=str),
        "BANK SECURITY LOG\n" + json.dumps(sec, default=str),
        "INTAKE CALL WITH THE AI (already done)\n" + format_transcript(intake),
        "LIVE INTERVIEW SO FAR (specialist and customer)\n" + (format_transcript(interview) or "(not started)"),
    ])
    result = await llm.json_chat(ASSIST_SYSTEM, user)
    result["updated_at"] = bank.iso(bank.now())
    pb = playbooks.get(juris)
    # attach the question text so the UI needs no second lookup
    qmap = {c["id"]: c["question"] for c in pb["checklist"]}
    for item in result.get("checklist", []):
        item["question"] = qmap.get(item.get("id"), item.get("id"))
    dmap = {d["id"]: d["text"] for d in pb["disclosures"]}
    for d in result.get("disclosures", []):
        d["text"] = dmap.get(d.get("id"), d.get("id"))
    call.assist = result
    await state.push(call)
    if cl and call.mode == "intake":
        cl = claims.get(cl["id"])
        cl["assist"] = result
        await claims.save(cl)
    await hub.broadcast({"type": "assist.updated", "call_id": call_id, "claim_id": call.claim_id, "assist": result})
