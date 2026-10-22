
from __future__ import annotations

import random
import re
from datetime import datetime

from . import bank, config, db, hub, playbooks

# ─────────────────────────── PII masking ───────────────────────────
_DIGIT_RUN = re.compile(r"(?<![\w])(\+?\d[\d\s\-]{4,}\d)(?![\w])")
_NUM_WORDS = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
_WORD_RUN = re.compile(rf"\b{_NUM_WORDS}(?:[\s,\-]+{_NUM_WORDS}){{3,}}\b", re.IGNORECASE)


def mask_pii(text: str) -> str:
    """Mask OTPs, card/account/phone numbers in transcripts (deck guardrail: 'personal data masked')."""

    def digits(m: re.Match) -> str:
        raw = m.group(1)
        d = re.sub(r"\D", "", raw)
        if len(d) < 6:
            return raw  # e.g. a year, an amount, last-four digits: keep
        if len(d) == 6:
            return "[code masked]"
        return "•••• " + d[-4:]

    text = _DIGIT_RUN.sub(digits, text or "")
    return _WORD_RUN.sub("[digits masked]", text)


# ─────────────────────────── audit log ───────────────────────────
async def audit(actor: str, action: str, detail: str = "", *, claim_id: str | None = None,
                call_id: str | None = None, data: dict | None = None) -> dict:
    entry = {
        "id": f"AUD-{int(datetime.now().timestamp() * 1000)}-{random.randint(100, 999)}",
        "time": bank.iso(bank.now()),
        "actor": actor,  # ai-agent | customer | specialist | analyst | system
        "action": action,
        "detail": detail,
        "claim_id": claim_id,
        "call_id": call_id,
        "data": data or {},
    }
    db.put("audit", entry["id"], entry)
    await hub.broadcast({"type": "audit", "entry": entry})
    return entry


def audit_for(claim_id: str | None = None, call_id: str | None = None) -> list[dict]:
    out = []
    for a in db.all("audit"):
        if (claim_id and a.get("claim_id") == claim_id) or (call_id and a.get("call_id") == call_id):
            out.append(a)
    return out


# ─────────────────────────── claims ───────────────────────────
def new_reference() -> str:
    return f"CLM-{datetime.now():%y%m%d}-{random.randint(1000, 9999)}"


def get(claim_id: str) -> dict | None:
    return db.get("claims", claim_id)


def list_all() -> list[dict]:
    return sorted(db.all("claims"), key=lambda c: c["created_at"], reverse=True)


def for_customer(customer_id: str) -> dict | None:
    """The customer's most recent claim (used to route a call-back to the Status Agent)."""
    mine = [c for c in db.all("claims") if c["customer_id"] == customer_id]
    return sorted(mine, key=lambda c: c["created_at"])[-1] if mine else None


def claim_covering(customer_id: str, txn_id: str) -> dict | None:
    """The claim (any status) that already disputes this payment, if any."""
    for c in db.all("claims"):
        if c["customer_id"] == customer_id and any(t["id"] == txn_id for t in c.get("disputed_transactions", [])):
            return c
    return None


def stage(claim: dict) -> str:
    """Where the claim is in the 4-step journey (used by the Status Agent and the screens)."""
    d = (claim.get("decision") or {}).get("outcome")
    if claim.get("refund"):
        return "refunded"
    if d == "declined":
        return "declined"
    if d == "more_info":
        return "more_info_needed"
    if claim["steps"].get("interview") != "done":
        return "interview_pending"
    return "under_investigation"


def with_clock(claim: dict) -> dict:
    deadline = datetime.fromisoformat(claim["deadline_at"])
    claim = dict(claim)
    claim["clock"] = {
        "opened_at": claim["opened_at"],
        "deadline_at": claim["deadline_at"],
        "business_days_left": playbooks.business_days_left(bank.now(), deadline),
        "rule": playbooks.get(claim["jurisdiction"])["clock_rule"],
        "breached": bank.now() > deadline and not claim.get("decision") and not claim.get("refund"),
    }
    return claim


async def save(claim: dict) -> dict:
    claim["updated_at"] = bank.iso(bank.now())
    db.put("claims", claim["id"], claim)
    await hub.broadcast({"type": "claim.updated", "claim": with_clock(claim)})
    return claim


async def create(*, customer: dict, call: dict, disputed: list[dict], facts: dict) -> dict:
    ref = new_reference()
    juris = config.JURISDICTION
    pb = playbooks.get(juris)
    # The clock starts at the customer's FIRST contact (the call start), not when the case is keyed in.
    opened = datetime.fromtimestamp(call["created_at"]).astimezone()
    deadline = playbooks.add_business_days(opened, pb["clock_business_days"])
    claim = {
        "id": ref,
        "reference": ref,
        "customer_id": customer["id"],
        "customer_name": customer["name"],
        "phone": customer["phone"],
        "jurisdiction": pb["code"],
        "status": "open",  # open -> interview -> investigation -> decided
        "steps": {"report": "done", "interview": "pending", "investigate": "pending", "followup": "pending"},
        "created_at": bank.iso(bank.now()),
        "opened_at": bank.iso(opened),
        "deadline_at": bank.iso(deadline),
        "disputed_transactions": disputed,
        "total_disputed": sum(t["amount"] for t in disputed),
        "currency": disputed[0]["currency"] if disputed else pb["currency"],
        "facts": facts,
        "verification": call.get("verification", {}),
        "card_actions": call.get("card_actions", []),
        "calls": [call["call_id"]],
        "intake_summary": None,
        "assist": None,
        "brief": None,
        "decision": None,
        "followups": [],
    }
    await save(claim)
    await audit("ai-agent", "claim.created", f"Claim {ref} created, deadline {claim['deadline_at']}",
                claim_id=ref, call_id=call["call_id"], data={"total": claim["total_disputed"]})
    return claim


async def attach_call(claim_id: str, call_id: str) -> None:
    c = get(claim_id)
    if c and call_id not in c["calls"]:
        c["calls"].append(call_id)
        await save(c)
