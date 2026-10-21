

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import bank, claims, config, hub, llm, playbooks, state
from .summarizer import format_transcript

log = logging.getLogger("brief")

# Voxtral context biasing takes SINGLE words (no spaces or commas), otherwise the API returns 400
CONTEXT_BIAS = [
    "OTP", "PIN", "CVV", "UPI", "QuickPay", "ShopNow", "GiftCards", "CoinNest", "RBI", "chargeback", "KYC",
    *config.BANK_NAME.split()[:1], *config.AGENT_PERSONA.split()[:1], *config.SPECIALIST_NAME.split()[:1],
]

BRIEF_SYSTEM = """
You prepare an investigation brief for a bank fraud analyst. You NEVER decide liability:
every flag is a prompt for the analyst, not a finding. Use only facts in the inputs.
Return ONLY JSON:
{
  "decision_ready_summary": "4-6 sentences an analyst can act on",
  "timeline": [{"time": "time as known (ISO if available)", "event": "what happened", "source": "transaction|bank_log|call <call_id>"}],
  "key_facts": [{"fact": "e.g. Customer shared an OTP", "value": "yes|no|unknown or a short value",
                 "evidence": {"call_id": "...", "quote": "exact quote"}}],
  "conflicts": [{"title": "short title", "detail": "what does not match and why it matters",
                 "statements": [{"source": "call <call_id> | bank_log", "quote": "exact quote or log line"}],
                 "severity": "high|medium|low"}],
  "qa_scorecard": [{"call_id": "...", "criterion_id": "<rubric id>", "met": true|false|null, "evidence": "quote or null"}],
  "repeated_questions": ["facts the customer had to repeat across steps (should be empty if the hand-offs worked)"],
  "open_questions": ["what the analyst still needs to find out"],
  "recommended_next_steps": ["..."]
}
Score EVERY rubric criterion for EVERY call (null when not applicable, e.g. AI disclosure on a human-led part).
""".strip()


async def _progress(claim_id: str, stage: str) -> None:
    await hub.broadcast({"type": "brief.progress", "claim_id": claim_id, "stage": stage})


async def ensure_batch_transcript(call: state.Call) -> None:
    if call.batch_transcript or not call.audio_file:
        return
    path = config.AUDIO_DIR / call.audio_file
    if not Path(path).exists():
        return
    try:
        bias = CONTEXT_BIAS + [w for w in (call.caller_name or "").replace(",", " ").split() if w]
        t = await llm.transcribe_file(path, bias)
        for s in t["segments"]:
            s["text"] = claims.mask_pii(s["text"])
        t["text"] = claims.mask_pii(t["text"] or "")
        call.batch_transcript = t
        await state.push(call)
    except Exception as e:
        log.warning("batch transcription failed for %s: %s", call.call_id, e)
        call.batch_transcript = {"error": str(e), "segments": []}
        await state.push(call)


def _call_text(call: state.Call) -> str:
    bt = call.batch_transcript or {}
    if bt.get("segments"):
        body = "\n".join(f"[{s['start']}s] speaker {s['speaker']}: {s['text']}" for s in bt["segments"])
        src = "Voxtral Transcribe V2 (diarised; speaker numbers are anonymous)"
    else:
        body = format_transcript(call.lines)
        src = "live transcript (Voxtral Realtime; speakers = ai / customer / specialist)"
    return f"=== CALL {call.call_id} · mode {call.mode} · {src}\n{body}"


def _normalise(brief, call_ids: list[str]) -> dict:
    """The LLM does not always follow the JSON shape exactly (e.g. "qa_scorecard": [1, 0, 1] or
    {"CALL-1": [...]}, which crashed the scoring with "'int' object has no attribute 'get'").
    Bring every field into the shape the code and the UI expect; drop what cannot be used."""
    if not isinstance(brief, dict):
        return {"error": "brief failed: the model did not return a JSON object"}

    def dicts(v) -> list[dict]:
        if isinstance(v, dict):
            v = [v]
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []

    def texts(v) -> list[str]:
        if isinstance(v, str):
            v = [v]
        return [x if isinstance(x, str) else json.dumps(x, default=str) for x in v] if isinstance(v, list) else []

    # qa_scorecard -> [{"call_id", "criterion_id", "met", "evidence"}]
    raw = brief.get("qa_scorecard")
    rows: list[dict] = []
    if isinstance(raw, dict):  # {"CALL-1": [rows] | {"crit_id": true|row}}
        for cid, v in raw.items():
            if isinstance(v, dict):
                v = [dict(x, criterion_id=k) if isinstance(x, dict) else {"criterion_id": k, "met": x}
                     for k, x in v.items()]
            for r in dicts(v):
                rows.append({"call_id": cid, **r})
    else:
        rows = dicts(raw)
    clean = []
    for r in rows:
        if not r.get("criterion_id") and not r.get("criterion"):
            continue
        met = r.get("met")
        if isinstance(met, str):
            met = {"true": True, "yes": True, "met": True, "false": False, "no": False}.get(met.strip().lower())
        elif isinstance(met, (int, float)) and not isinstance(met, bool):
            met = bool(met)
        cid = r.get("call_id") or (call_ids[0] if len(call_ids) == 1 else "?")
        clean.append({"call_id": str(cid), "criterion_id": str(r.get("criterion_id") or r.get("criterion")),
                      "met": met if isinstance(met, bool) else None, "evidence": r.get("evidence")})
    brief["qa_scorecard"] = clean

    for k in ("timeline", "key_facts", "conflicts"):
        if k in brief:
            brief[k] = dicts(brief[k])
    for k in ("repeated_questions", "open_questions", "recommended_next_steps"):
        if k in brief:
            brief[k] = texts(brief[k])
    if "decision_ready_summary" in brief and not isinstance(brief["decision_ready_summary"], str):
        brief["decision_ready_summary"] = json.dumps(brief["decision_ready_summary"], default=str)
    return brief


async def generate(claim_id: str) -> dict | None:
    try:
        return await _generate(claim_id)
    finally:
        await _progress(claim_id, "done")  # never leave the "Regenerate brief" spinner stuck


async def _generate(claim_id: str) -> dict | None:
    cl = claims.get(claim_id)
    if not cl:
        return None
    await _progress(claim_id, "transcribing")
    calls = [state.calls[c] for c in cl["calls"] if c in state.calls]
    for c in calls:
        await ensure_batch_transcript(c)

    await _progress(claim_id, "analysing")
    pb = playbooks.get(cl["jurisdiction"])
    user = "\n\n".join([
        "QA RUBRIC\n" + "\n".join(f"- [{r['id']}] {r['text']}" for r in pb["qa_rubric"]),
        "CLAIM RECORD\n" + json.dumps({k: cl.get(k) for k in (
            "reference", "opened_at", "deadline_at", "facts", "disputed_transactions", "verification",
            "card_actions", "assist", "decision")}, default=str),
        "BANK SECURITY LOG (system truth)\n" + json.dumps(bank.security_log_for(cl["customer_id"]), default=str),
        "CALLS\n" + "\n\n".join(_call_text(c) for c in calls),
    ])
    try:
        brief = await llm.json_chat(BRIEF_SYSTEM, user)
    except Exception as e:
        log.exception("brief failed")
        brief = {"error": f"brief failed: {e}"}
    brief = _normalise(brief, [c.call_id for c in calls])

    # QA score per call, computed in code
    scores: dict[str, dict] = {}
    for row in brief.get("qa_scorecard", []) or []:
        s = scores.setdefault(row.get("call_id", "?"), {"met": 0, "missed": 0})
        if row.get("met") is True:
            s["met"] += 1
        elif row.get("met") is False:
            s["missed"] += 1
    for s in scores.values():
        total = s["met"] + s["missed"]
        s["score_pct"] = round(100 * s["met"] / total) if total else None
    brief["qa_scores"] = scores
    rubric_text = {r["id"]: r["text"] for r in pb["qa_rubric"]}
    for row in brief.get("qa_scorecard", []) or []:
        row["criterion"] = rubric_text.get(row.get("criterion_id"), row.get("criterion_id"))
    brief["generated_at"] = bank.iso(bank.now())

    cl = claims.get(claim_id)
    cl["brief"] = brief
    # never reopen a decided / refunded claim (e.g. "Regenerate brief" after the decision)
    if not cl.get("refund") and (cl.get("decision") or {}).get("outcome") in (None, "more_info"):
        cl["status"] = "investigation"
        cl["steps"]["investigate"] = "brief_failed" if brief.get("error") else "brief_ready"
    await claims.save(cl)
    await claims.audit("system", "brief.generated", "Investigation brief and QA scorecard generated", claim_id=claim_id)
    return brief
