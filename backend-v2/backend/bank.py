

from __future__ import annotations

import random
from datetime import datetime, timedelta

from . import db


def now() -> datetime:
    return datetime.now().astimezone()


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def norm_phone(p: str | None) -> str:
    return "".join(ch for ch in (p or "") if ch.isdigit())[-10:]


DEFAULT_ACCOUNTS = {
    "CUS-1001": {"last4": "5521", "type": "Savings account", "balance": 82450},
    "CUS-1002": {"last4": "3307", "type": "Senior Savings account", "balance": 146300},
}


def _migrate_accounts() -> None:
    """Older demo databases had no bank account: add it (and keep any refund already credited)."""
    for c in db.all("customers"):
        acct = c.get("account")
        base = DEFAULT_ACCOUNTS.get(c["id"])
        if not base or (acct and acct.get("last4") != "0000"):
            continue
        credited = sum(t["amount"] for t in db.find("transactions", customer_id=c["id"]) if t.get("direction") == "credit")
        c["account"] = {**base, "balance": base["balance"] + credited}
        db.put("customers", c["id"], c)
        for cl in db.find("claims", customer_id=c["id"]):  # fix "credited to ••0000" on old refunds
            if (cl.get("refund") or {}).get("account_last4") == "0000":
                cl["refund"]["account_last4"] = base["last4"]
                cl["refund"]["new_balance"] = c["account"]["balance"]
                db.put("claims", cl["id"], cl)


def seed() -> None:
    if db.all("customers"):
        _migrate_accounts()
        db.replace_text("Riya", "Amit")  # the demo customer was renamed Riya -> Amit Sharma
        return
    last_night = (now() - timedelta(days=1)).replace(hour=22, minute=0, second=0, microsecond=0)

    customers = [
        {
            "id": "CUS-1001",
            "name": "Amit Sharma",
            "phone": "+91 98765 43210",
            "dob": "1991-04-12",
            "segment": "Retail",
            "account": {"last4": "5521", "type": "Savings account", "balance": 82450},
            "cards": [
                {"last4": "4321", "type": "Debit (Visa)", "status": "active"},
                {"last4": "9087", "type": "Credit (Mastercard)", "status": "active"},
            ],
        },
        {
            "id": "CUS-1002",
            "name": "Arjun Mehta",
            "phone": "+91 91234 56789",
            "dob": "1958-09-30",
            "segment": "Senior citizen",
            "account": {"last4": "3307", "type": "Senior Savings account", "balance": 146300},
            "cards": [{"last4": "7788", "type": "Debit (RuPay)", "status": "active"}],
        },
    ]
    for c in customers:
        db.put("customers", c["id"], c)

    txns = [
        # legitimate history
        ("CUS-1001", "4321", -2, 14, 5, "BigBasket Groceries", 2340, "card_present"),
        ("CUS-1001", "4321", -1, 9, 40, "Metro Card Recharge", 500, "online"),
        ("CUS-1001", "9087", -1, 13, 15, "Cafe Coffee Day", 310, "card_present"),
        # the fraud last night, minutes after a new device was registered with an OTP
        ("CUS-1001", "4321", 0, 22, 31, "QuickPay Wallet Top-up", 20000, "online"),
        ("CUS-1001", "4321", 0, 22, 34, "ShopNow Electronics", 15000, "online"),
        ("CUS-1001", "4321", 0, 22, 38, "GiftCards Online", 10000, "online"),
        ("CUS-1002", "7788", -3, 11, 0, "Apollo Pharmacy", 860, "card_present"),
        ("CUS-1002", "7788", -2, 10, 20, "Reliance Fresh", 1240, "card_present"),
        ("CUS-1002", "7788", -1, 18, 45, "Electricity Bill - BESCOM", 2150, "online"),
        # Arjun's fraud yesterday afternoon: a fake "KYC update" caller made him install a screen-sharing
        # app, then raised his online limit and spent on his card while he watched
        ("CUS-1002", "7788", 0, 16, 5, "UPI Transfer - Rahul Traders", 25000, "online"),
        ("CUS-1002", "7788", 0, 16, 9, "Electronics Hub Online", 18500, "online"),
        ("CUS-1002", "7788", 0, 16, 14, "CoinNest Crypto Top-up", 12000, "online"),
        ("CUS-1002", "7788", 0, 16, 20, "Gift Voucher Store", 5000, "online"),
    ]
    for i, (cid, last4, day, hh, mm, merchant, amount, channel) in enumerate(txns, start=1):
        t = last_night + timedelta(days=day)
        t = t.replace(hour=hh, minute=mm)
        db.put(
            "transactions",
            f"TXN-{5000 + i}",
            {
                "id": f"TXN-{5000 + i}",
                "customer_id": cid,
                "card_last4": last4,
                "time": iso(t),
                "merchant": merchant,
                "amount": amount,
                "currency": "INR",
                "channel": channel,
                "status": "posted",
            },
        )

    # Bank-side security log (what the system really did) - used by the Step 3 brief.
    db.put(
        "security_log",
        "SEC-1",
        {
            "id": "SEC-1",
            "customer_id": "CUS-1001",
            "time": iso(last_night.replace(hour=22, minute=27)),
            "event": "OTP sent for NEW DEVICE REGISTRATION",
            "detail": "OTP sent to registered mobile ending 3210; warning text: 'Never share this OTP. "
            "The bank will never ask for it.'",
        },
    )
    db.put(
        "security_log",
        "SEC-2",
        {
            "id": "SEC-2",
            "customer_id": "CUS-1001",
            "time": iso(last_night.replace(hour=22, minute=29)),
            "event": "New device registered",
            "detail": "Android device, IP geolocated outside the customer's usual city.",
        },
    )
    # Arjun Mehta (senior citizen): remote-access / fake KYC scam
    for sid, hh, mm, event, detail in [
        ("SEC-3", 15, 48, "Mobile banking login", "Login from the customer's own phone, but during an active screen-sharing session (remote-access app signature)."),
        ("SEC-4", 15, 58, "Online card limit raised", "Card ending 7788 daily online limit changed from 10,000 to 1,00,000 INR in the app; confirmation SMS said: 'If this was not you, call us immediately.'"),
        ("SEC-5", 16, 3, "OTP sent for HIGH-VALUE PAYMENT", "OTP sent to registered mobile ending 6789; warning text: 'Never share this OTP. The bank will never ask for it.'"),
    ]:
        db.put("security_log", sid, {"id": sid, "customer_id": "CUS-1002",
                                     "time": iso(last_night.replace(hour=hh, minute=mm)), "event": event, "detail": detail})


def customer_by_phone(phone: str) -> dict | None:
    key = norm_phone(phone)
    for c in db.all("customers"):
        if norm_phone(c["phone"]) == key:
            return c
    return None


def transactions_for(customer_id: str, hours: int = 120) -> list[dict]:
    cutoff = now() - timedelta(hours=hours)
    out = [
        t
        for t in db.find("transactions", customer_id=customer_id)
        if datetime.fromisoformat(t["time"]) >= cutoff
    ]
    return sorted(out, key=lambda t: t["time"], reverse=True)


def security_log_for(customer_id: str) -> list[dict]:
    return db.find("security_log", customer_id=customer_id)


def block_card(customer_id: str, last4: str) -> dict | None:
    c = db.get("customers", customer_id)
    if not c:
        return None
    for card in c["cards"]:
        if card["last4"] == last4:
            card["status"] = "blocked"
            card["blocked_at"] = iso(now())
            db.put("customers", customer_id, c)
            return card
    return None


def credit_refund(customer_id: str, amount: int, reference: str, txn_ids: list[str]) -> dict:
    """Credit a fraud refund to the customer's account and mark the disputed payments as refunded."""
    c = db.get("customers", customer_id)
    acct = c.get("account") or dict(DEFAULT_ACCOUNTS.get(customer_id, {"last4": "0000", "type": "Savings account", "balance": 0}))
    c["account"] = acct
    acct["balance"] = acct.get("balance", 0) + amount
    db.put("customers", customer_id, c)
    tid = f"TXN-R{random.randint(1000, 9999)}"
    txn = {"id": tid, "customer_id": customer_id, "card_last4": "", "time": iso(now()),
           "merchant": f"Fraud refund - claim {reference}", "amount": amount, "currency": "INR",
           "channel": "credit", "direction": "credit", "status": "posted"}
    db.put("transactions", tid, txn)
    for t_id in txn_ids:
        t = db.get("transactions", t_id)
        if t:
            t["status"] = "refunded"
            db.put("transactions", t_id, t)
    return {"txn_id": tid, "amount": amount, "time": txn["time"], "account_last4": acct["last4"],
            "new_balance": acct["balance"]}


def new_otp() -> str:
    # digits 1-9 only: "zero" / "oh" is the digit speech recognition gets wrong most often
    return "".join(random.choice("123456789") for _ in range(6))
