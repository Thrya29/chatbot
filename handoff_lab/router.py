"""Tiering and routing: which queue, which priority, what the client is told.

Order matters: the most serious signal wins. Red-tier checks run BEFORE any
retrieval or answer, so a complaint or a distressed client never gets an article.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .detection import Detection

TOPIC_QUEUE = {
    "withdrawal_not_received": "FundingPayments",
    "withdrawal_howto": "FundingPayments",
    "deposit": "FundingPayments",
    "close_out_dispute": "TradingSupport",
    "account_access": "AccountKYC",
    "unauthorised_activity": "Security",
    "kyc": "AccountKYC",
    "platform": "PlatformTechnical",
    "other": "FundingPayments",
}

CRISIS_LINE = ("If you're thinking about harming yourself, please contact Samaritans on 116 123 "
               "(free, 24 hours) or call 999 if you're in immediate danger.")


@dataclass
class Decision:
    tier: str                    # green | amber | red
    action: str                  # answer | handoff
    queue: str | None = None
    priority: str | None = None
    flags: set[str] = field(default_factory=set)
    reason: str = ""
    client_message: str = ""


def decide(det: Detection, has_open_positions: bool = False) -> Decision:
    f = set(det.flags)

    if "self_harm_risk" in f:
        return Decision("red", "handoff", "PriorityVulnerable", "P1", f, "self-harm language",
                        "Thank you for telling me. I'd like to get you to someone on our team who can talk "
                        "this through with you now. " + CRISIS_LINE)
    if "possible_vulnerability" in f:
        return Decision("red", "handoff", "PriorityVulnerable", "P1", f, "possible vulnerability",
                        "Thank you for telling me. I'd like to get you to someone on our team who can talk "
                        "this through with you now.")
    if "suspected_unauthorised" in f:
        return Decision("red", "handoff", "Security", "P1", f, "suspected unauthorised activity",
                        "I'm getting this to our security team now. If you can, change your password in the app "
                        "straight away.")
    if "complaint" in f:
        return Decision("red", "handoff", "Complaints", "P2", f, "complaint or regulator/legal mention",
                        "I've noted that you're unhappy with how this has been handled, and I've flagged it as a "
                        "complaint so our team reviews it properly. They'll explain the next steps and timescale.")
    if "account_access" in f:
        urgent = "time_critical" in f or has_open_positions
        if urgent:
            f.add("time_critical")
        return Decision("red", "handoff", "AccountKYC", "P1" if urgent else "P2", f, "identity or 2FA reset",
                        "A person needs to verify your identity for this, so I'm passing you to our account "
                        "team now." + (" Your positions and stops stay in place in the meantime." if has_open_positions else ""))
    if "non_english" in f:
        return Decision("amber", "handoff", TOPIC_QUEUE.get(det.topic, "FundingPayments"), "P3", f,
                        f"message not in English ({det.lang}); rules are English-only",
                        "I'm passing you to our team, who can help you in your language.")
    if "advice_request" in f:
        return Decision("amber", "answer", None, None, f, "advice boundary",
                        "I can't advise on what to do with your positions, but I can show you the facts, or "
                        "connect you with our team.")
    if "asked_for_human" in f or "repeat_contact" in f:
        queue = TOPIC_QUEUE.get(det.topic, "FundingPayments")
        repeat_or_frustrated = bool({"repeat_contact", "frustrated", "possible_complaint"} & f)
        opener = ("You've asked about this more than once and I haven't been able to help, so I'm passing you "
                  "to our team now" if "repeat_contact" in f else "Of course. I'm passing you to our team now")
        return Decision("amber", "handoff", queue, "P2" if repeat_or_frustrated else "P3", f,
                        "client asked for a person" if "asked_for_human" in f else "repeat contact",
                        opener + " with everything you've told me, so you won't need to repeat yourself.")
    return Decision("green", "answer", None, None, f, "no red or amber signal")
