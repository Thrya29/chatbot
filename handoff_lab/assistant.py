"""The assistant's turn logic: detect -> tier -> answer from data, or hand off with a briefing.

This stands in for what a configured AI agent (for example a Fin procedure with
escalation rules) does. General questions would be answered by retrieval from
approved help content; that part is out of scope here, because the lab's subject
is the handoff and the measurement around it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from .contract import Handoff
from .detection import Detection, detect
from .router import Decision, decide
from .withdrawal import Withdrawal, assess, money

PRIORITY_ORDER = {"P1": 1, "P2": 2, "P3": 3}

# Illustrative approved help content for green-tier how-to questions. In production these
# answers come from retrieval over approved articles (RAG), not from code.
HELP_CONTENT = {
    "withdrawal_howto": ("Withdrawals can only go to a bank account in your own name. Add the new account in the "
                         "app under Payments; once it's verified, you can choose it when you withdraw."),
}

NEXT_STEP = {
    "FundingPayments": "Trace the payment with the bank reference; confirm whether to log a complaint for the delay.",
    "Complaints": "Acknowledge the complaint and open the complaint record; do not debate the outcome in chat.",
    "PriorityVulnerable": "Follow the vulnerable-client process; offer support options and account controls.",
    "Security": "Secure the account first; hold any pending withdrawal; verify identity.",
    "AccountKYC": "Verify identity, then reset 2FA; offer phone dealing if positions need action now.",
    "TradingSupport": "Review the deal history with the client.",
    "PlatformTechnical": "Reproduce the issue and check known incidents.",
}


@dataclass
class TurnResult:
    reply: str
    detection: Detection
    decision: Decision
    handoff: dict | None = None
    data_pulled: list[str] = field(default_factory=list)


class Assistant:
    def __init__(self, withdrawals_for: Callable[[str], list[Withdrawal]],
                 has_open_positions: Callable[[str], bool] = lambda _c: False,
                 today: date | None = None):
        self.withdrawals_for = withdrawals_for
        self.has_open_positions = has_open_positions
        self.today = today or date.today()

    def handle(self, conversation_id: str, client_id: str, message: str, history: list[str] | None = None,
               same_issue_contacts: int = 0, ai_told_them: list[str] | None = None,
               details_given: list[str] | None = None, lang: str = "en") -> TurnResult:
        history = history or []
        det = detect(message, history, same_issue_contacts)
        if det.lang != "en":
            lang = det.lang
        dec = decide(det, self.has_open_positions(client_id))
        data: list[str] = []

        if dec.action == "answer" and det.topic == "withdrawal_not_received":
            items = self.withdrawals_for(client_id)
            if not items:
                return TurnResult("I can't see any withdrawals on your account in the last 14 days. Would you like "
                                  "me to pass this to a person?", det, dec)
            if len(items) > 1:
                options = ", ".join(f"{w.currency} {w.amount} requested {w.requested_on.isoformat()}" for w in items)
                return TurnResult(f"I can see more than one withdrawal. Which one is it? {options}", det, dec)
            w = items[0]
            a = assess(w, self.today)
            data.append(f"{w.id} | {w.currency} {w.amount} | requested {w.requested_on} | "
                        f"sent {w.sent_on or '-'} | {w.method} | ending {w.account_last4} | "
                        f"ref {w.bank_reference or '-'} | status {w.status} | due_by {a.due_by or '-'}")
            if a.outcome == "resolve":
                return TurnResult(a.reply + " Does that answer it, or would you like a person to look into it?",
                                  det, dec, data_pulled=data)
            dec = Decision("amber", "handoff", "FundingPayments", a.priority or "P2",
                           det.flags | set(a.extra_flags), f"withdrawal {a.category}", a.reply)
            for f in a.extra_flags:
                det.evidence.setdefault(f, f"{w.id} {a.category.replace('_', ' ')}"
                                        + (f", due by {a.due_by}" if a.due_by else ""))

        if dec.action == "answer" and det.topic in HELP_CONTENT:
            return TurnResult(HELP_CONTENT[det.topic] + " Does that answer it, or would you like a person?",
                              det, dec)
        if dec.action == "answer":
            reply = dec.client_message or "(answer from approved help content via retrieval)"
            return TurnResult(reply, det, dec, data_pulled=data)

        # Hand off: if the topic needs data, pull it so the human starts informed, and tell the
        # client exactly what is being passed on (so they know they won't have to repeat it).
        if det.topic == "withdrawal_not_received" and not data:
            items = self.withdrawals_for(client_id)[:3]
            for w in items:
                a = assess(w, self.today)
                data.append(f"{w.id} | {w.currency} {w.amount} | requested {w.requested_on} | "
                            f"sent {w.sent_on or '-'} | {w.method} | ending {w.account_last4} | "
                            f"ref {w.bank_reference or '-'} | status {w.status} | due_by {a.due_by or '-'}")
                if a.category == "sent_late":
                    dec.flags.add("late_payment")
                    det.evidence.setdefault("late_payment", f"{w.id} sent {w.sent_on}, due by {a.due_by}")
            if len(items) == 1 and items[0].status == "SENT":
                w = items[0]
                dec.client_message += (f" I'm sending them this: your {money(w.amount, w.currency)} withdrawal sent on "
                                       f"{w.sent_on.day} {w.sent_on.strftime('%B')} to your account ending "
                                       f"{w.account_last4}, bank reference {w.bank_reference}, not yet received.")
        payload = self._handoff(conversation_id, det, dec, message, data, ai_told_them or [],
                                details_given or [], lang)
        return TurnResult(dec.client_message, det, dec, payload, data)

    def _handoff(self, conversation_id: str, det: Detection, dec: Decision, message: str, data: list[str],
                 ai_told: list[str], details: list[str], lang: str) -> dict:
        signals = [f"{k} (\"{det.evidence.get(k, '')}\")" for k in sorted(dec.flags)]
        h = Handoff(
            queue=dec.queue, priority=dec.priority, flags=sorted(dec.flags), topic=det.topic,
            conversation_id=conversation_id, lang=lang,
            summary=f"{dec.reason.capitalize()}. Topic: {det.topic.replace('_', ' ')}. Latest message: \"{message[:160]}\"",
            client_wants=_client_wants(det.topic, dec),
            ai_told_them=ai_told, data_pulled=data, signals=signals,
            client_was_told=dec.client_message,
            do_not_ask_again=details,
            suggested_next_step=NEXT_STEP.get(dec.queue, "Review the conversation."),
            transcript_link=f"assistant://conversations/{conversation_id}",
            ref=(data[0].split(" | ")[0] if data else ""),
        )
        return h.to_payload()


def _client_wants(topic: str, dec: Decision) -> str:
    if dec.queue == "PriorityVulnerable":
        return "Support from a person."
    if dec.queue == "Complaints":
        return "To complain about how this was handled, and a resolution."
    return {
        "withdrawal_not_received": "To know where the money is and receive it.",
        "account_access": "To get back into the account.",
        "unauthorised_activity": "To stop activity they don't recognise.",
        "close_out_dispute": "An explanation of the close-out.",
    }.get(topic, "Help from a person.")
