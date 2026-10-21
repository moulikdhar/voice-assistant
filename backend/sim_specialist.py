
from __future__ import annotations

import asyncio
import base64
import logging
import time

import numpy as np
from livekit import api, rtc

from . import bank, claims, config, llm, state

log = logging.getLogger("sim-specialist")

NAME = config.SPECIALIST_NAME    # .env SPECIALIST_NAME  (default "Oliver")
VOICE = config.SPECIALIST_VOICE  # .env SPECIALIST_VOICE (default gb_oliver_confident), different from the AI's voice
AI = config.AGENT_PERSONA        # .env AGENT_PERSONA    (default "Asha")
SAMPLE_RATE = 24000
QUESTIONS = 3  # Oliver asks at most 3 critical questions, then closes
MAX_TURNS = QUESTIONS

_tasks: dict[str, asyncio.Task] = {}

SYSTEM = f"""You play {NAME}, a HUMAN fraud specialist at {config.BANK_NAME}. This is a synthetic demo.
CONTEXT: the bank's AI voice assistant, {AI}, has just handled the first part of this call: she verified the
customer by OTP, blocked the card, raised the fraud claim and transferred the call to you (warm handover).
You have {AI}'s full transcript, the claim record and the bank's security log in front of you.

YOUR JOB: ask at most {QUESTIONS} CRITICAL questions that {AI} did NOT already get clear answers to, then close.
Pick them from the open liability checklist items, most important first. Good critical questions are about:
whether the card is still with the customer, whether they saw/read the bank's warning in the OTP SMS, whether
they installed an app or shared their screen, when they first noticed, whether they told anyone else
their PIN or password, and anything in the security log that conflicts with what they said.

Rules:
- NEVER repeat or re-ask anything already in {AI}'s transcript or the claim (amounts, merchants, OTP, how it
  happened, card block, reference number). Never re-verify identity, never ask for an OTP or PIN.
- Spoken phone style: ONE short utterance per turn, at most 2 sentences, plain words, no lists, no markdown,
  no asterisks, no stage directions.
- Turn 1: "Hi <first name>, I'm {NAME} from the fraud team." Say {AI}, our AI assistant, has passed you
  everything, so they won't need to repeat themselves, and that you have just a few quick questions. Then ask
  question 1.
- Turns 2 and 3: briefly acknowledge the last answer (warmly), then ask the next critical question.
- CLOSING turn (when told it is the closing turn): thank them, explain the investigation runs to the deadline
  date, that no decision is made yet and you cannot promise a refund, give safety advice (the bank never asks
  for an OTP or PIN), and say they will get an SMS with the outcome and can call back any time.
- Never promise a refund or decide liability.

Return JSON only: {{"say": "<what you say>", "closing": <true|false>}}"""


# ───────────────────────────── public API ─────────────────────────────
def start(call_id: str) -> bool:
    if call_id in _tasks and not _tasks[call_id].done():
        return False
    _tasks[call_id] = asyncio.create_task(_run(call_id))
    return True


def stop(call_id: str) -> None:
    t = _tasks.pop(call_id, None)
    if t and not t.done():
        t.cancel()


def running(call_id: str) -> bool:
    t = _tasks.get(call_id)
    return bool(t and not t.done())


# ───────────────────────────── the loop ─────────────────────────────
async def _set(call: state.Call, status: str) -> None:
    call.specialist = {**(call.specialist or {}), "mode": "simulated", "name": NAME, "status": status}
    await state.push(call)


async def _run(call_id: str) -> None:
    call = state.calls[call_id]
    room = rtc.Room()
    source: rtc.AudioSource | None = None
    try:
        tok = (
            api.AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
            .with_identity(f"specialist-{call_id}")
            .with_name(f"{NAME} (simulated specialist)")
            .with_grants(api.VideoGrants(room_join=True, room=call.room))
            .to_jwt()
        )
        # Oliver only SPEAKS from here (the agent does the listening): don't subscribe to the others' audio
        await room.connect(config.LIVEKIT_URL, tok, rtc.RoomOptions(auto_subscribe=False))
        source = rtc.AudioSource(SAMPLE_RATE, 1)
        track = rtc.LocalAudioTrack.create_audio_track("specialist-voice", source)
        await room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        await claims.audit("specialist", "sim.joined", f"{NAME} (simulated specialist) joined the call",
                           call_id=call_id, claim_id=call.claim_id)
        await asyncio.sleep(1.5)  # let the agent go quiet and start the two transcription channels

        first = (call.caller_name or "").split(" ")[0]
        if call.mode == "status":
            # CALL-BACK about an existing claim: a free, human conversation about what the caller wants
            for turn in range(1, ESC_MAX_TURNS + 1):
                if call.status != "active":
                    return
                await _set(call, "thinking")
                say, end, flag = await _next_escalation(call, turn)
                if flag:
                    await claims.audit("specialist", "specialist.flag", f"{NAME}: {flag}",
                                       call_id=call_id, claim_id=call.claim_id)
                await _set(call, "speaking")
                mark = len(call.lines)
                await _speak(source, say)
                if end or call.status != "active":
                    break
                await _set(call, "listening")
                if not await _wait_for_customer(call, mark, timeout=40):
                    await _speak(source, f"{first}, are you still there?")
                    if not await _wait_for_customer(call, len(call.lines), timeout=20):
                        await _speak(source, "I'll let you go for now. You can call us back any time. Goodbye.")
                        break
        else:
            # STEP 2 after a new claim: 3 critical questions, then a closing turn
            closing = False
            for turn in range(1, MAX_TURNS + 2):
                if call.status != "active":
                    return
                await _set(call, "thinking")
                say, closing = await _next_utterance(call, turn)
                await _set(call, "speaking")
                mark = len(call.lines)
                await _speak(source, say)
                if call.status != "active":
                    return
                await _set(call, "listening")
                answered = await _wait_for_customer(call, mark, timeout=40 if not closing else 12)
                if closing or call.status != "active":
                    break
                if not answered:
                    await _set(call, "speaking")
                    await _speak(source, "Are you still there? Take your time.")
                    await _set(call, "listening")
                    await _wait_for_customer(call, len(call.lines), timeout=30)
            await _set(call, "speaking")
            await _speak(source, f"Thank you {first}, you did the right thing calling us so quickly. Take care, goodbye.")
        await asyncio.sleep(1.0)
        await _set(call, "done")
        await claims.audit("specialist", "interview.ended", f"Interview finished by {NAME} (simulated)",
                           call_id=call_id, claim_id=call.claim_id)
        from .server import hangup_call  # late import: server imports this module
        await hangup_call(call_id)
    except asyncio.CancelledError:
        pass
    except Exception as e:  # never crash the backend because of the demo helper
        log.exception("simulated specialist failed")
        await hub_warning(call_id, f"Simulated specialist stopped: {e}")
    finally:
        _tasks.pop(call_id, None)
        if source is not None:
            try:
                await source.aclose()
            except Exception:
                pass
        await room.disconnect()


async def hub_warning(call_id: str, message: str) -> None:
    from . import hub
    await hub.broadcast({"type": "warning", "call_id": call_id, "message": message})


# ───────────────────────────── brain ─────────────────────────────
async def _next_utterance(call: state.Call, turn: int) -> tuple[str, bool]:
    claim = claims.get(call.claim_id) if call.claim_id else None
    a = call.assist or {}
    checklist = "\n".join(
        f"- [{c.get('status')}] {c.get('question') or c.get('id')}" + (f" -> {c['answer']}" if c.get("answer") else "")
        for c in a.get("checklist", [])
    ) or "(not ready: use your own judgement from the transcript)"
    def lines(phase: str) -> str:
        who = {"ai": f"{AI.upper()} (AI)", "customer": "CUSTOMER", "specialist": NAME.upper()}
        return "\n".join(f"{who.get(l['speaker'], l['speaker'])}: {l['text']}" for l in call.lines
                         if (l.get("phase") or "ai") == phase)
    intake = lines("ai")[-6000:] or "(none)"
    interview = lines("interview")[-3000:] or "(nothing yet)"
    facts = (claim or {}).get("facts") or {}
    sec = "\n".join(f"- {e['time'][11:16]} {e['event']}: {e['detail']}"
                     for e in bank.security_log_for((claim or {}).get("customer_id") or call.customer_id or ""))
    closing_turn = turn > QUESTIONS
    user = f"""{'THIS IS THE CLOSING TURN: do not ask a question, close the call.' if closing_turn else f'Question turn {turn} of {QUESTIONS}.'}
Customer: {call.caller_name}
Claim: {(claim or {}).get('reference', 'n/a')} · {(claim or {}).get('total_disputed', '')} {(claim or {}).get('currency', '')} · decision deadline {(claim or {}).get('deadline_at', 'n/a')[:10]}
Disputed payments: {', '.join(f"{t['merchant']} {t['amount']}" for t in (claim or {}).get('disputed_transactions', []))}
Facts {AI} captured: {facts}
Bank security log:
{sec or '(none)'}
Liability checklist (answered/partial/open):
{checklist}
Suggested next question: {a.get('next_question') or 'n/a'}

{AI.upper()}'S TRANSCRIPT (already covered, never repeat):
{intake}

YOUR INTERVIEW SO FAR:
{interview}"""
    try:
        out = await llm.json_chat(SYSTEM, user, model=config.LLM_MODEL, temperature=0.3)
        say = str(out.get("say") or "").replace("*", "").strip()
        if say:
            return say, closing_turn  # exactly 3 questions, then a proper closing turn
    except Exception as e:
        log.warning("sim specialist LLM failed: %s", e)
    if closing_turn:
        return ("Thank you, that's everything I need. We'll investigate before the deadline and text you the outcome; "
                "remember, the bank will never ask for your OTP or PIN.", True)
    return ("Thank you. Is your card still with you, or has anyone else had access to it?", False)


ESCALATION_SYSTEM = f"""You are {NAME}, a senior HUMAN fraud specialist at {config.BANK_NAME}, on a live phone call.
This is a synthetic demo, but behave exactly like an experienced, calm, honest human bank employee.

The bank's AI voice assistant, {AI}, has just transferred this caller to you. You have the full history:
the claim record and its CURRENT STAGE (refunded / declined / more info needed / under investigation),
the analyst's decision and reason, the refund details, earlier calls, and {AI}'s transcript of THIS call
including WHY she transferred it. Read the caller's real intent from all of it before you speak.

How you behave:
- Speak like a real person: use their first name, short natural sentences, at most 2 per turn, react to what
  they actually just said. No lists, no markdown, no asterisks, no stage directions. Never say you are an AI.
- Turn 1: introduce yourself, show you already know the situation in one line (so they don't repeat it),
  and address the reason they asked for you.
- Be honest and firm about policy, kind in tone:
  * REFUNDED: the money was credited (say amount, account ending, date). It cannot be paid twice. If they say
    they can't see it, explain where to look (the account credit line in their app / statement) and offer a
    balance check. If they push for more money, or ask you to do something "so management doesn't know" or
    anything unethical or off the record: refuse clearly and politely, say you can't do that and the call is
    recorded, and don't lecture. Do not accuse them of fraud.
  * DECLINED: explain the analyst's reason in plain words with empathy. You cannot overturn it on the call.
    Offer the formal route: they can ask for a review by writing to the bank's grievance team, and if still
    unhappy, escalate to the RBI Integrated Ombudsman. Offer to log their disagreement for review.
  * MORE INFO / UNDER INVESTIGATION: say what is needed or when the decision is due; promise nothing.
- Never promise money, never change a decision, never ask for an OTP, PIN or full card number.
- If the caller was transferred because they KEEP INSISTING on money that is not due (a second refund, or a
  refund after a decline), be direct and blunt, like a firm senior employee: the decision stands and will not
  change by insisting; repeated attempts to obtain money that is not owed are taken seriously, and if this
  continues the bank may refer it to its fraud team and raise a formal complaint against the account. Say
  this clearly once (calm, professional, no insults), then offer only the legitimate route (written review /
  RBI Ombudsman for a declined claim) and close. Always set "flag" in this case.
- When the matter is resolved, or after they have heard your answer clearly twice, close politely: summarise
  in one line what happens next, remind them the bank never asks for OTP or PIN, and say goodbye. Set
  "end_call": true on that closing turn. Keep the whole conversation short (about 4 to 6 of your turns).
- Set "flag" to a short note for the analyst if you saw a concern (e.g. asked for a second refund, asked to
  hide something from the bank), else null.

Return JSON only: {{"say": "<what you say>", "end_call": <true|false>, "flag": <string|null>}}"""

ESC_MAX_TURNS = 7


async def _next_escalation(call: state.Call, turn: int) -> tuple[str, bool, str | None]:
    claim = claims.get(call.claim_id) if call.claim_id else None
    who = {"ai": f"{AI.upper()} (AI)", "customer": "CUSTOMER", "specialist": NAME.upper()}
    this_call = "\n".join(f"{who['ai'] if l['speaker'] == 'ai' else who.get(l['speaker'], l['speaker'])}"
                          f"{'' if (l.get('phase') or 'ai') == 'ai' else ' [with you]'}: {l['text']}"
                          for l in call.lines)[-7000:]
    history = []
    for cid in (claim or {}).get("calls", []):
        c = state.calls.get(cid)
        if c and cid != call.call_id and c.summary and c.summary.get("short_summary"):
            history.append(f"- {c.mode} call {cid}: {c.summary['short_summary']}")
    st = claims.stage(claim) if claim else "no_claim"
    user = f"""{'CLOSE THE CALL NOW (last turn).' if turn >= ESC_MAX_TURNS else f'Your turn {turn}.'}
Caller: {call.caller_name}
Why {AI} transferred the call: {call.handover_reason or 'caller asked for a specialist'}
Claim {(claim or {}).get('reference', 'n/a')} · CURRENT STAGE: {st}
Disputed: {', '.join(f"{t['merchant']} {t['amount']}" for t in (claim or {}).get('disputed_transactions', []))} · total {(claim or {}).get('total_disputed', '')} {(claim or {}).get('currency', '')}
Analyst decision: {(claim or {}).get('decision')}
Refund: {(claim or {}).get('refund')}
Decision deadline: {(claim or {}).get('deadline_at', 'n/a')[:10]}
Earlier calls:
{chr(10).join(history) or '(none)'}

THIS CALL SO FAR ({AI} first, then you):
{this_call or '(nothing yet)'}"""
    try:
        out = await llm.json_chat(ESCALATION_SYSTEM, user, model=config.LLM_MODEL, temperature=0.4)
        say = str(out.get("say") or "").replace("*", "").strip()
        if say:
            return say, bool(out.get("end_call")) or turn >= ESC_MAX_TURNS, out.get("flag") or None
    except Exception as e:
        log.warning("sim specialist (escalation) LLM failed: %s", e)
    return ("I'm sorry, I can't help further with that today. You'll get any update by SMS. Goodbye.", True, None)


# ───────────────────────────── ears ─────────────────────────────
async def _wait_for_customer(call: state.Call, mark: int, timeout: float) -> bool:
    """Wait until the customer has answered (a new customer line) and then paused for ~2 s."""
    t0 = time.time()
    last_seen, last_change = -1, None
    while time.time() - t0 < timeout:
        if call.status != "active":
            return False
        cust = [i for i, l in enumerate(call.lines) if i >= mark and l.get("speaker") == "customer"]
        if cust:
            if cust[-1] != last_seen:
                last_seen, last_change = cust[-1], time.time()
            still_talking = bool(call.live.get("customer"))
            if not still_talking and last_change and time.time() - last_change > 2.0:
                return True
        await asyncio.sleep(0.25)
    return False


# ───────────────────────────── voice ─────────────────────────────
async def _speak(source: rtc.AudioSource, text: str) -> None:
    """Voxtral TTS (streamed PCM float32 24 kHz) → 20 ms frames into the LiveKit track."""
    stream = await llm.client().audio.speech.complete_async(
        model=config.TTS_MODEL, input=text, voice_id=VOICE, response_format="pcm", stream=True,
    )
    buf = np.zeros(0, dtype=np.int16)
    step = SAMPLE_RATE // 50  # 20 ms
    async for ev in stream:
        if ev.event != "speech.audio.delta":
            continue
        f32 = np.frombuffer(base64.b64decode(ev.data.audio_data), dtype="<f4")
        buf = np.concatenate([buf, (np.clip(f32, -1, 1) * 32767).astype(np.int16)])
        while len(buf) >= step:
            chunk, buf = buf[:step], buf[step:]
            await source.capture_frame(rtc.AudioFrame(chunk.tobytes(), SAMPLE_RATE, 1, step))
    if len(buf):
        pad = np.concatenate([buf, np.zeros(step - len(buf), dtype=np.int16)])
        await source.capture_frame(rtc.AudioFrame(pad.tobytes(), SAMPLE_RATE, 1, step))
    await source.wait_for_playout()
