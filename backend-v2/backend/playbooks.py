
from __future__ import annotations

from datetime import datetime, timedelta

LIABILITY_CHECKLIST = [
    {"id": "how", "question": "How did the fraud happen (call, SMS link, app, lost card)?"},
    {"id": "impersonation", "question": "Did anyone contact the customer pretending to be the bank or another organisation?"},
    {"id": "otp_shared", "question": "Did the customer share an OTP, PIN or password? With whom and when?"},
    {"id": "warning_seen", "question": "Did the customer see or ignore any bank warning or alert?"},
    {"id": "card_possession", "question": "Is the card still with the customer?"},
    {"id": "remote_app", "question": "Was any app installed or screen shared at someone's request?"},
    {"id": "first_noticed", "question": "When did the customer first notice and report the payments?"},
    {"id": "transactions_confirmed", "question": "Are the disputed transactions confirmed (amount, payee, time)?"},
    {"id": "vulnerability", "question": "Any vulnerability cues (distress, illness, age, confusion) handled with care?"},
]

DISCLOSURES = [
    {"id": "timeline", "text": "Explain the investigation timeline and the regulatory deadline."},
    {"id": "no_promise", "text": "Do not promise a refund before the investigation is complete."},
    {"id": "safety_advice", "text": "Give safety advice: the bank never asks for an OTP or PIN."},
    {"id": "next_contact", "text": "Tell the customer how and when they will be contacted next."},
]

QA_RUBRIC = [
    {"id": "ai_disclosure", "text": "Caller told they are speaking with an AI (AI calls only)"},
    {"id": "recording_disclosure", "text": "Caller told the call is recorded"},
    {"id": "identity_otp", "text": "Identity verified with OTP or account data, never by voice"},
    {"id": "readback_block", "text": "Card block read back and confirmed before acting"},
    {"id": "readback_claim", "text": "Claim details read back and confirmed before creation"},
    {"id": "no_liability_promise", "text": "No refund promised and no liability decided on the call"},
    {"id": "reference_given", "text": "Reference number given to the customer"},
    {"id": "liability_facts", "text": "Liability facts captured (impersonation, OTP shared, warnings)"},
    {"id": "vulnerability_handled", "text": "Vulnerability cues checked and handled"},
    {"id": "safety_advice", "text": "Safety advice given (bank never asks for OTP/PIN)"},
]

JURISDICTIONS = {
    "IN_RBI": {
        "name": "India (RBI, unauthorised electronic banking transactions)",
        "clock_business_days": 10,
        "clock_rule": "Credit (shadow reversal) within 10 working days of the customer's notification.",
        "currency": "INR",
    },
    "US_REG_E": {
        "name": "United States (Regulation E, 12 CFR 1005.11)",
        "clock_business_days": 10,
        "clock_rule": "Investigate within 10 business days, or give provisional credit and up to 45 days.",
        "currency": "USD",
    },
    "UK_PSR": {
        "name": "United Kingdom (PSR APP scam reimbursement)",
        "clock_business_days": 5,
        "clock_rule": "Reimburse within 5 business days of the claim, unless an exception applies.",
        "currency": "GBP",
    },
}


def get(jurisdiction: str) -> dict:
    j = JURISDICTIONS.get(jurisdiction, JURISDICTIONS["IN_RBI"])
    return {
        "code": jurisdiction if jurisdiction in JURISDICTIONS else "IN_RBI",
        **j,
        "checklist": LIABILITY_CHECKLIST,
        "disclosures": DISCLOSURES,
        "qa_rubric": QA_RUBRIC,
    }


def add_business_days(start: datetime, days: int) -> datetime:
    d = start
    added = 0
    while added < days:
        d += timedelta(days=1)
        if d.weekday() < 5:  # Mon-Fri
            added += 1
    return d


def business_days_left(now: datetime, deadline: datetime) -> int:
    if now >= deadline:
        return 0
    d, n = now, 0
    while d.date() < deadline.date():
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def as_text(jurisdiction: str) -> str:
    """Playbook as plain text for model prompts."""
    p = get(jurisdiction)
    lines = [f"Jurisdiction: {p['name']}", f"Clock: {p['clock_rule']}", "Liability checklist:"]
    lines += [f"- [{c['id']}] {c['question']}" for c in p["checklist"]]
    lines += ["Required disclosures:"] + [f"- [{d['id']}] {d['text']}" for d in p["disclosures"]]
    return "\n".join(lines)
