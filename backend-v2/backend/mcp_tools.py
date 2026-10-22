"""
The bank's systems exposed as tools the Mistral voice agent can call
(deck: "Connectors: card / core banking, case management, SMS").

v2: no MCP server. Each call gets its own set of @function_tool handlers (closures over the call_id),
passed to client.audio.sessions.init(tools=...). Mistral's agent calls them over the session's RPC
channel and they run right here, in the backend process.

Guardrail "confirm before acting": card blocks and claim creation are TWO-PHASE.
  request_*  -> returns a read-back sentence + action_id   (nothing happens yet)
  confirm_*  -> only after the customer clearly says yes  (now it happens, and is audited)
"""

# no `from __future__ import annotations`: @function_tool builds the JSON schema from real type hints

from datetime import datetime

import asyncio
import logging
import random

from mistralai.extra.voice_agents import function_tool

from . import bank, claims, db, hub, state

log = logging.getLogger("bank-tools")

INTAKE_TOOLS = ["get_caller_profile", "send_otp", "verify_otp", "list_recent_transactions",
                "request_card_block", "confirm_card_block", "request_claim", "confirm_claim",
                "transfer_to_specialist"]
STATUS_TOOLS = ["get_caller_profile", "send_otp", "verify_otp", "get_claim_status", "log_question_for_analyst",
                "transfer_to_specialist"]
TIMEOUT_MS = 10000


# ─────────────────────────── helpers ───────────────────────────
def _get_call(call_id: str) -> state.Call:
    if call_id not in state.calls:
        raise ValueError("Unknown call.")
    return state.calls[call_id]


def _customer(call: state.Call) -> dict | None:
    if call.customer_id:
        return db.get("customers", call.customer_id)
    c = bank.customer_by_phone(call.caller_number)
    if c:
        call.customer_id = c["id"]
    return c


def _need_verified(call: state.Call) -> dict | None:
    if not call.verification.get("verified"):
        return {"ok": False, "error": "Customer is not verified yet. Send and verify an OTP first."}
    return None


async def send_sms(phone: str, text: str, *, call_id: str | None = None, claim_id: str | None = None) -> None:
    msg = {"id": f"SMS-{random.randint(10**6, 10**7)}", "phone": phone, "text": text, "time": bank.iso(bank.now())}
    db.put("sms", msg["id"], msg)
    await hub.broadcast({"type": "sms", "sms": msg})
    await claims.audit("system", "sms.sent", text if "code" not in text.lower() else "OTP SMS sent",
                       call_id=call_id, claim_id=claim_id)


def _action_id() -> str:
    return f"ACT-{random.randint(1000, 9999)}"


async def issue_otp(call: state.Call, *, reason: str = "requested by the AI agent") -> dict:
    """Send (or re-send) the OTP SMS. Re-sending keeps the SAME code for 5 minutes."""
    c = _customer(call)
    if not c:
        return {"ok": False, "error": "Customer not found."}
    prev = db.get("otps", call.call_id)
    if prev and (bank.now() - datetime.fromisoformat(prev["time"])).total_seconds() < 300:
        code = prev["code"]
    else:
        code = bank.new_otp()
        db.put("otps", call.call_id, {"code": code, "time": bank.iso(bank.now())})
    call.verification.update({"otp_sent": True, "mobile_ending": bank.norm_phone(c["phone"])[-4:]})
    await state.push(call)
    await send_sms(c["phone"], f"Northbridge Bank: your verification code is {code}. "
                   "We will never ask you to share it with anyone else.", call_id=call.call_id)
    await claims.audit("ai-agent", "otp.sent", f"OTP sent to registered mobile ({reason})", call_id=call.call_id)
    return {"ok": True, "sent_to_mobile_ending": call.verification["mobile_ending"]}


_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
         "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else "-" + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " and " + _words(n % 100))
    for size, name in ((10_000_000, "crore"), (100_000, "lakh"), (1000, "thousand")):
        if n >= size:
            return _words(n // size) + " " + name + ("" if n % size == 0 else " " + _words(n % size))
    return str(n)


def _rupees_words(amount: float) -> str:
    """25000 -> 'twenty-five thousand rupees' (Indian numbering: lakh, crore)."""
    return f"{_words(int(round(amount)))} rupees"


def _spoken_day(when: datetime, now: datetime) -> str:
    days = (now.date() - when.date()).days
    day = "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"
    return f"{day} at {when.strftime('%I:%M %p').lstrip('0')}"


async def _auto_specialist(call_id: str, delay: float = 3.0, max_wait: float = 25.0) -> None:
    """Demo flow: once the AI has FINISHED its transfer sentence (agent_state "transferring"), the simulated
    specialist joins the call. (v1 waited a fixed 7 s; the managed session can take longer to start speaking,
    and joining earlier cuts the AI off mid-sentence.)"""
    from . import server  # local import: server imports this module
    await asyncio.sleep(delay)
    t0 = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - t0 < max_wait:
        call = state.calls.get(call_id)
        if not call or call.status != "active" or call.phase != "handover" or call.agent_state == "transferring":
            break
        await asyncio.sleep(0.25)
    await asyncio.sleep(1.0)
    call = state.calls.get(call_id)
    if not server.settings.get("auto_specialist") or not call or call.status != "active" or call.phase != "handover":
        return
    if call.specialist:  # a human (or a simulation) already joined
        return
    try:
        await server.simulate_specialist(call_id)
    except Exception as e:
        log.warning("auto specialist failed: %s", e)


# ─────────────────────────── the tools of ONE call ───────────────────────────
def make_tools(call_id: str, mode: str) -> list:
    """The @function_tool handlers for this call (intake or status set), bound to its call_id."""

    def _call() -> state.Call:
        return _get_call(call_id)

    # ── Step 1: intake tools ──
    @function_tool(timeout_ms=TIMEOUT_MS)
    async def get_caller_profile() -> dict:
        """Look up the caller by caller ID. Use at the start of the call. Returns only safe details
        (first name, mobile ending) until the caller is verified."""
        call = _call()
        c = _customer(call)
        if not c:
            return {"ok": True, "found": False, "note": "No customer matches this caller ID. Ask for the registered mobile number."}
        await state.push(call)
        out = {"ok": True, "found": True, "first_name": c["name"].split()[0],
               "registered_mobile_ending": bank.norm_phone(c["phone"])[-4:], "verified": call.verification.get("verified")}
        if call.verification.get("verified"):
            out["cards"] = [{"last4": k["last4"], "type": k["type"], "status": k["status"]} for k in c["cards"]]
            out["segment"] = c.get("segment")
            if c.get("segment") == "Senior citizen":
                out["care_note"] = ("Senior citizen: speak slowly and simply, check they are okay, and offer a human "
                                    "specialist early (vulnerable-customer rule).")
        return out

    @function_tool(timeout_ms=TIMEOUT_MS)
    async def send_otp() -> dict:
        """RE-SEND the one-time passcode SMS (the same code) to the customer's REGISTERED mobile.
        A code is already sent automatically when the call connects: only use this if the customer
        says they did not receive it."""
        out = await issue_otp(_call(), reason="re-sent on request")
        if out.get("ok"):
            out["next"] = "Ask the customer to read the 6-digit code from the SMS, slowly, one digit at a time."
        return out

    @function_tool(timeout_ms=TIMEOUT_MS)
    async def verify_otp(code: str) -> dict:
        """Check the 6-digit code the customer read out. Pass only the digits."""
        call = _call()
        rec = db.get("otps", call.call_id)
        digits = "".join(ch for ch in code if ch.isdigit())
        if not rec:
            return {"ok": False, "error": "No OTP was sent yet. Call send_otp first."}
        if len(digits) != 6:  # usually a digit was lost in speech recognition: not a real attempt
            return {"ok": True, "verified": False, "heard_digits": len(digits),
                    "note": f"Only {len(digits)} digits were heard. Do NOT send a new code. Ask the customer to "
                            "read the same 6-digit code again, slowly, one digit at a time."}
        call.verification["attempts"] = call.verification.get("attempts", 0) + 1
        if call.verification["attempts"] > 3:
            await state.push(call)
            return {"ok": False, "verified": False, "error": "Too many attempts. Offer to transfer to a specialist."}
        ok = digits == rec["code"]
        if ok:
            call.verification.update({"verified": True, "method": "OTP to registered mobile", "verified_at": bank.iso(bank.now())})
        await state.push(call)
        await claims.audit("ai-agent", "otp.verified" if ok else "otp.failed",
                           "Identity verified by OTP" if ok else "Wrong OTP", call_id=call.call_id)
        if not ok:
            return {"ok": True, "verified": False, "note": "Code did not match. Do NOT send a new code; ask them to read the SAME code again, slowly."}
        c = _customer(call) or {}
        out = {"ok": True, "verified": True, "customer_first_name": (c.get("name") or "").split(" ")[0],
               "cards": [{"last4": k["last4"], "type": k["type"], "status": k["status"]} for k in c.get("cards", [])]}
        if c.get("segment") == "Senior citizen":
            out["care_note"] = ("Senior citizen: speak slowly and simply, check they are okay, and offer a human "
                                "specialist early (vulnerable-customer rule).")
        return out

    # no native `filler=`: the prompt already has the AI say "one moment" itself (as in v1); both = said twice
    @function_tool(timeout_ms=TIMEOUT_MS)
    async def list_recent_transactions() -> dict:
        """List the customer's recent card payments (last few days) so they can point out the ones they did
        NOT make. Requires a verified customer. Read amounts EXACTLY as given in `say` - never from memory."""
        call = _call()
        if err := _need_verified(call):
            return err
        c = _customer(call)
        now = bank.now()
        out = []
        for t in bank.transactions_for(c["id"]):
            if t.get("direction", "debit") != "debit":
                continue  # refunds / credits are not payments the customer could dispute
            when = datetime.fromisoformat(t["time"]).astimezone(now.tzinfo)
            row = {"id": t["id"], "time": t["time"], "merchant": t["merchant"], "amount": t["amount"],
                   "currency": t["currency"], "card_last4": t["card_last4"], "channel": t["channel"],
                   "say": f"{_spoken_day(when, now)}, {_rupees_words(t['amount'])} to {t['merchant']}, "
                          f"card ending {' '.join(t['card_last4'])}"}
            if (cov := claims.claim_covering(c["id"], t["id"])):
                row["already_in_claim"] = cov["reference"]
                row["claim_stage"] = claims.stage(cov)
            if t.get("status") == "refunded":
                row["refunded"] = True
            out.append(row)
        await claims.audit("ai-agent", "transactions.listed", f"{len(out)} recent card payments looked up",
                           call_id=call.call_id)
        return {"ok": True, "transactions": out,
                "rules": "Read merchants, amounts and days EXACTLY from `say`. These are the bank's records: if the "
                         "caller mentions a different amount, politely tell them what the record shows and ask them "
                         "to confirm. Never invent or round amounts. Payments marked already_in_claim are ALREADY "
                         "disputed: do not raise them again."}

    @function_tool(timeout_ms=TIMEOUT_MS)
    async def request_card_block(card_last4: str) -> dict:
        """Prepare to block a card. Nothing is blocked yet. Call this BEFORE asking the customer anything about
        the block: the returned read_back sentence is the one and only consent question. Read it back, wait for
        a clear yes, then call confirm_card_block."""
        call = _call()
        if err := _need_verified(call):
            return err
        c = _customer(call)
        last4 = "".join(ch for ch in card_last4 if ch.isdigit())[-4:]
        card = next((k for k in c["cards"] if k["last4"] == last4), None)
        if not card:
            return {"ok": False, "error": f"No card ending {last4}.", "cards_on_file": [k["last4"] for k in c["cards"]]}
        if card["status"] == "blocked":
            return {"ok": True, "already_blocked": True}
        aid = _action_id()
        read_back = f"I'll block your {card['type']} card ending {last4} now. Shall I go ahead?"
        call.pending[aid] = {"kind": "card_block", "read_back": read_back, "card_last4": last4}
        await state.push(call)
        return {"ok": True, "action_id": aid, "read_back": read_back,
                "note": "Say read_back word for word as your ONLY question about this action. Do not ask again."}

    @function_tool(timeout_ms=TIMEOUT_MS)
    async def confirm_card_block(action_id: str, customer_confirmed: bool) -> dict:
        """Execute a prepared card block ONLY if the customer clearly said yes to the read-back."""
        call = _call()
        p = call.pending.pop(action_id, None)
        if not p or p["kind"] != "card_block":
            return {"ok": False, "error": "Unknown action_id. Call request_card_block first."}
        if not customer_confirmed:
            await state.push(call)
            await claims.audit("customer", "card_block.declined", f"Customer declined block of card {p['card_last4']}",
                               call_id=call.call_id)
            return {"ok": True, "blocked": False}
        c = _customer(call)
        card = bank.block_card(c["id"], p["card_last4"])
        call.card_actions.append({"action": "blocked", "card_last4": p["card_last4"], "time": card["blocked_at"],
                                  "confirmed_by_readback": True})
        await state.push(call)
        await claims.audit("ai-agent", "card.blocked", f"Card ending {p['card_last4']} blocked after read-back",
                           call_id=call.call_id, claim_id=call.claim_id)
        await send_sms(c["phone"], f"Northbridge Bank: your card ending {p['card_last4']} has been blocked as requested.",
                       call_id=call.call_id)
        return {"ok": True, "blocked": True, "card_last4": p["card_last4"]}

    @function_tool(timeout_ms=TIMEOUT_MS)
    async def request_claim(
        transaction_ids: list[str],
        how_it_happened: str,
        contacted_by_impersonator: bool | None = None,
        shared_otp_or_pin: bool | None = None,
        saw_bank_warning: bool | None = None,
        vulnerability_notes: str = "",
    ) -> dict:
        """Prepare a fraud/dispute claim for the transactions the customer did NOT make. Nothing is created
        yet. Call this BEFORE asking the customer to confirm the payments: the returned read_back sentence is the
        one and only consent question. Read it back, wait for a clear yes, then call confirm_claim.
        Use null for facts the customer has not stated."""
        call = _call()
        if err := _need_verified(call):
            return err
        c = _customer(call)
        mine = {t["id"]: t for t in bank.transactions_for(c["id"], hours=24 * 30)}
        picked = [mine[i] for i in transaction_ids if i in mine and mine[i].get("direction", "debit") == "debit"]
        if not picked:
            return {"ok": False, "error": "None of those transaction ids belong to this customer. Use list_recent_transactions."}
        # one payment can only ever be claimed once (no double refunds)
        covered = {t["id"]: claims.claim_covering(c["id"], t["id"]) for t in picked}
        dup = {k: v for k, v in covered.items() if v}
        if dup:
            ref = next(iter(dup.values()))
            return {"ok": False, "already_claimed": True, "reference": ref["reference"], "stage": claims.stage(ref),
                    "say": f"Explain kindly that these payments are already covered by claim {ref['reference']} "
                           f"(current stage: {claims.stage(ref).replace('_', ' ')}), so a new claim is not needed and the "
                           "same money cannot be refunded twice. Offer to tell them the claim status."}
        total = sum(t["amount"] for t in picked)
        aid = _action_id()
        read_back = (f"I'll raise a fraud claim for {len(picked)} payment{'s' if len(picked) > 1 else ''} "
                     f"totalling {total:,} {picked[0]['currency']}: " + ", ".join(f"{t['merchant']} {t['amount']:,}" for t in picked)
                     + ". Is that correct?")
        call.pending[aid] = {
            "kind": "claim", "read_back": read_back,
            "disputed": picked,
            "facts": {
                "how_it_happened": how_it_happened,
                "contacted_by_impersonator": contacted_by_impersonator,
                "shared_otp_or_pin": shared_otp_or_pin,
                "saw_bank_warning": saw_bank_warning,
                "vulnerability_notes": vulnerability_notes or None,
            },
        }
        await state.push(call)
        return {"ok": True, "action_id": aid, "read_back": read_back,
                "note": "Say read_back word for word as your ONLY question about this action. Do not ask again."}

    @function_tool(timeout_ms=TIMEOUT_MS)
    async def confirm_claim(action_id: str, customer_confirmed: bool) -> dict:
        """Create the prepared claim ONLY if the customer clearly said yes. Returns the reference number
        to read out, and sends it by SMS. The regulatory clock starts from the start of this call."""
        call = _call()
        p = call.pending.pop(action_id, None)
        if not p or p["kind"] != "claim":
            return {"ok": False, "error": "Unknown action_id. Call request_claim first."}
        if not customer_confirmed:
            await state.push(call)
            return {"ok": True, "created": False, "note": "Ask what needs correcting, then call request_claim again."}
        c = _customer(call)
        claim = await claims.create(customer=c, call=call.model_dump(), disputed=p["disputed"], facts=p["facts"])
        call.claim_id = claim["id"]
        await state.push(call)
        spoken_ref = " ".join(claim["reference"].replace("-", " "))
        await send_sms(c["phone"], f"Northbridge Bank: your fraud claim {claim['reference']} is registered. "
                       "A specialist will review it. We will never ask for your OTP or PIN.",
                       call_id=call.call_id, claim_id=claim["id"])
        return {"ok": True, "created": True, "reference": claim["reference"], "say_reference_as": spoken_ref,
                "decision_deadline": claim["deadline_at"]}

    @function_tool(timeout_ms=TIMEOUT_MS)
    async def transfer_to_specialist(reason: str) -> dict:
        """Warm handover to a human fraud specialist. Use after the claim is created, when the caller asks
        for a human, or when the caller sounds vulnerable. The specialist sees your summary, so the
        customer will not be asked everything again. In `reason`, say honestly what the caller wants and
        any concern (e.g. "asks for the refunded money again; hinted at hiding it from the bank")."""
        call = _call()
        call.phase = "handover"
        call.handover_reason = reason
        await state.push(call)
        await claims.audit("ai-agent", "handover.requested", reason, call_id=call.call_id, claim_id=call.claim_id)
        if call.claim_id and call.mode == "intake":  # a call-back escalation must not reopen the claim
            cl = claims.get(call.claim_id)
            if cl:
                cl["status"] = "interview"
                cl["steps"]["interview"] = "waiting"
                await claims.save(cl)
        await hub.broadcast({"type": "handover", "call_id": call.call_id, "claim_id": call.claim_id, "reason": reason})
        from .assist import prepare_handover  # local import: avoids a cycle

        from .server import spawn  # local import: avoids a cycle
        spawn(prepare_handover(call.call_id))
        spawn(_auto_specialist(call.call_id))
        return {"ok": True, "say": "In ONE short sentence tell the caller you are connecting them to a fraud specialist "
                "who can already see everything discussed, and to stay on the line. After that sentence you are "
                "finished: say nothing else."}

    # ── Step 4: status tools (read-only) ──
    @function_tool(timeout_ms=TIMEOUT_MS)
    async def get_claim_status() -> dict:
        """Read-only: the caller's claim status, investigation summary, the analyst's decision and any
        refund, plus exactly what to tell the caller next."""
        call = _call()
        if err := _need_verified(call):
            return err
        c = _customer(call)
        cl = claims.for_customer(c["id"]) if c else None
        if not cl:
            return {"ok": True, "found": False}
        cl = claims.with_clock(cl)
        brief = cl.get("brief") or {}
        st = claims.stage(cl)
        dec = cl.get("decision") or {}
        refund = cl.get("refund")
        amount = f"{int(cl['total_disputed']):,} rupees"
        guide = {
            "interview_pending": "The claim is registered but the specialist interview was not completed. Offer to "
                                 "connect them to a fraud specialist now (transfer_to_specialist).",
            "under_investigation": f"The claim is being investigated. A decision is due by {cl['deadline_at'][:10]} "
                                   f"({cl['clock']['business_days_left']} business days left). No decision yet; promise nothing.",
            "more_info_needed": f"The analyst needs more information: {dec.get('notes') or 'details to be confirmed'}. "
                                "Offer to connect them to a specialist to provide it.",
            "declined": f"The claim was reviewed and declined. Reason given by the analyst: {dec.get('notes') or 'not stated'}. "
                        "Be kind. You cannot change or re-open it. If they disagree, log their question for the analyst.",
            "refunded": (f"The claim was approved and {amount} was credited to their account ending "
                         f"{(refund or {}).get('account_last4', '')} on {(refund or {}).get('time', '')[:10]}. These payments are "
                         "fully settled: there is nothing more to claim for them and the same money cannot be refunded twice."),
        }[st]
        return {
            "ok": True, "found": True, "reference": cl["reference"], "stage": st, "status": cl["status"],
            "opened_at": cl["opened_at"], "decision_deadline": cl["deadline_at"],
            "business_days_left": cl["clock"]["business_days_left"],
            "disputed_total": cl["total_disputed"], "currency": cl["currency"],
            "disputed_payments": [f"{t['merchant']} {t['amount']:,}" for t in cl["disputed_transactions"]],
            "investigation_summary": brief.get("decision_ready_summary"),
            "decision": dec or None, "refund": refund,
            "tell_the_caller": guide,
        }

    @function_tool(timeout_ms=TIMEOUT_MS)
    async def log_question_for_analyst(question: str) -> dict:
        """Record a question about the DECISION for the fraud analyst to answer (the AI never argues
        or changes a decision)."""
        call = _call()
        await claims.audit("customer", "followup.question", question, call_id=call.call_id, claim_id=call.claim_id)
        return {"ok": True, "say": "Tell the caller the analyst will call them back about this."}

    every = {t.name: t for t in (
        get_caller_profile, send_otp, verify_otp, list_recent_transactions, request_card_block, confirm_card_block,
        request_claim, confirm_claim, transfer_to_specialist, get_claim_status, log_question_for_analyst)}
    return [every[n] for n in (STATUS_TOOLS if mode == "status" else INTAKE_TOOLS)]
