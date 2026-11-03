"""Status-first withdrawal handling: answer from data, never from a generic article.

The model never does date maths. This module decides, deterministically, whether a
withdrawal is inside its normal window, and what may be said about it. It mirrors
what a Fin Procedure plus a Python code block would do.
Timings are illustrative, modelled on public UK broker help content; a real
deployment takes them from the payments team's approved timings table.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

# Working days from "sent" to expected arrival, by method (illustrative).
WINDOW = {"FASTER_PAYMENTS": 1, "BACS": 3, "CARD": 5, "INTERNATIONAL": 3}
# Working days from "requested" to "sent" while approval is pending.
APPROVAL_DAYS = 1
UK_BANK_HOLIDAYS = {
    date(2026, 12, 25), date(2026, 12, 28), date(2027, 1, 1), date(2027, 3, 26),
    date(2027, 3, 29), date(2027, 5, 3), date(2027, 5, 31), date(2027, 8, 30),
}


def add_working_days(start: date, n: int, holidays: set[date] = UK_BANK_HOLIDAYS) -> date:
    d = start
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in holidays:
            n -= 1
    return d


@dataclass
class Withdrawal:
    id: str
    amount: str           # exact decimal string, never a float
    currency: str
    requested_on: date
    method: str
    account_last4: str
    status: str           # REQUESTED | SENT | FAILED | RETURNED | UNDER_REVIEW
    sent_on: date | None = None
    bank_reference: str = ""


@dataclass
class Assessment:
    outcome: str          # "resolve" or "handoff"
    category: str
    reply: str
    due_by: date | None = None
    priority: str | None = None
    extra_flags: tuple[str, ...] = ()


def money(amount: str, currency: str) -> str:
    value = f"{Decimal(amount):,.2f}"
    return f"£{value}" if currency == "GBP" else f"{value} {currency}"


def _fmt(d: date) -> str:
    return f"{d.day} {d.strftime('%B')}"


def assess(w: Withdrawal, today: date) -> Assessment:
    amount = money(w.amount, w.currency)
    if w.status == "UNDER_REVIEW":
        # Never give a reason: explaining a compliance review could be tipping off.
        return Assessment("handoff", "under_review",
                          "Your withdrawal is being reviewed by our team. I can't see more detail, "
                          "so I'm passing you to them now.", priority="P2", extra_flags=("restricted",))
    if w.status in ("FAILED", "RETURNED"):
        return Assessment("handoff", "failed_or_returned",
                          f"Your {amount} withdrawal didn't go through and the money is back in your account. "
                          "A person will check the bank details with you.", priority="P3")
    if w.method not in WINDOW:
        return Assessment("handoff", "unknown_method",
                          "I can't see your withdrawal details clearly right now, so I'm passing this to our "
                          "payments team.", priority="P2", extra_flags=("data_unavailable",))
    if w.status == "REQUESTED":
        sent_by = add_working_days(w.requested_on, APPROVAL_DAYS)
        if today <= sent_by:
            return Assessment("resolve", "requested_within_approval",
                              f"Your {amount} withdrawal requested on {_fmt(w.requested_on)} is being processed "
                              f"and should be sent by {_fmt(sent_by)}.", due_by=sent_by)
        return Assessment("handoff", "approval_late",
                          f"Your {amount} withdrawal requested on {_fmt(w.requested_on)} should have been sent by "
                          f"now, so I'm passing it to our payments team to check.", priority="P2")
    if w.status == "SENT" and w.sent_on:
        due = add_working_days(w.sent_on, WINDOW[w.method])
        method = w.method.replace("_", " ").title()
        facts = (f"Your {amount} withdrawal was sent on {_fmt(w.sent_on)} by {method} to your account ending "
                 f"{w.account_last4}. Bank reference: {w.bank_reference}.")
        if today <= due:
            return Assessment("resolve", "sent_in_window",
                              f"{facts} It should arrive by {_fmt(due)}. You can quote the reference to your "
                              "bank if you'd like them to look for it.", due_by=due)
        return Assessment("handoff", "sent_late",
                          f"{facts} It should have arrived by {_fmt(due)}, so I'm passing this to our payments "
                          "team to trace it.", due_by=due, priority="P2", extra_flags=("late_payment",))
    return Assessment("handoff", "unknown_status",
                      "I can't see your withdrawal details right now, so I'm passing this to our payments team.",
                      priority="P2", extra_flags=("data_unavailable",))
