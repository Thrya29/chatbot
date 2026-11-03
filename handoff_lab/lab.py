"""Wires the whole journey together in one process for demos and tests:

client -> MockAssistantPlatform -> (signed webhook) -> Bridge -> MockGenesys (Architect + ACD)
       <- MockAssistantPlatform <- (REST reply)     <- Bridge <- (signed webhook) <- agent
"""
from __future__ import annotations

import itertools
from datetime import date, datetime

from .assistant import Assistant
from .bridge import Bridge
from .mock_assistant_platform import MockAssistantPlatform
from .mock_genesys import Agent, MockGenesys
from .store import Store
from .withdrawal import Withdrawal

ASSISTANT_SECRET = "assistant-webhook-secret"   # demo values only; real secrets live in a vault
GENESYS_SECRET = "genesys-webhook-secret"
INTEGRATION_ID = "om-integration-001"

DEMO_TODAY = date(2026, 10, 28)
DEMO_NOW = datetime(2026, 10, 28, 10, 40)

DEMO_WITHDRAWALS = {
    "client-2": [Withdrawal("WD-55120", "2500.00", "GBP", date(2026, 10, 22), "FASTER_PAYMENTS", "4417",
                            "SENT", date(2026, 10, 23), "HLW-88213")],
    "client-6": [Withdrawal("WD-70031", "8000.00", "GBP", date(2026, 10, 31), "BACS", "9921", "REQUESTED")],
    "client-7": [Withdrawal("WD-61002", "640.00", "GBP", date(2026, 10, 27), "FASTER_PAYMENTS", "1180",
                            "SENT", date(2026, 10, 28), "HLW-90114")],
}
OPEN_POSITIONS = {"client-5"}


class Lab:
    def __init__(self, now: datetime = DEMO_NOW, today: date = DEMO_TODAY, free_text_only: bool = False,
                 genesys_down: bool = False):
        self.clock = [now]
        self.store = Store()
        self.platform = MockAssistantPlatform(ASSISTANT_SECRET, self._to_bridge_from_assistant)
        self.genesys = MockGenesys(INTEGRATION_ID, GENESYS_SECRET, self._to_bridge_from_genesys,
                                   now=lambda: self.clock[0], free_text_only=free_text_only)
        self.genesys_down = genesys_down
        self.genesys_attempts = 0     # every call the bridge actually makes to Genesys
        self.bridge = Bridge(self.store, self._genesys_post, self.platform, ASSISTANT_SECRET, GENESYS_SECRET,
                             INTEGRATION_ID, is_staffed=lambda: self.genesys.staffed(self.clock[0]),
                             sleep=lambda s: None, clock=lambda: self.clock[0].timestamp(),
                             queue_snapshot=self.genesys.queue_snapshot)
        self.assistant = Assistant(lambda c: DEMO_WITHDRAWALS.get(c, []), lambda c: c in OPEN_POSITIONS, today)
        self.bridge_log: list[tuple[str, int, dict]] = []
        self._ids = itertools.count(1)

    def _genesys_post(self, path: str, body: dict) -> dict:
        self.genesys_attempts += 1
        if self.genesys_down:
            raise ConnectionError("genesys unreachable")
        return self.genesys.post(path, body)

    def _to_bridge_from_assistant(self, body: bytes, headers: dict) -> None:
        self.bridge_log.append(("assistant", *self.bridge.on_assistant_webhook(body, headers)))

    def _to_bridge_from_genesys(self, body: bytes, headers: dict) -> None:
        self.bridge_log.append(("genesys", *self.bridge.on_genesys_webhook(body, headers)))

    def staff(self, *agents: Agent) -> None:
        for a in agents:
            self.genesys.add_agent(a)

    def client_turn(self, thread_id: str, text: str, history: list[str] | None = None,
                    same_issue_contacts: int = 0, details_given: list[str] | None = None,
                    ai_told_them: list[str] | None = None):
        t = self.platform.threads[thread_id]
        msg_id = f"cmsg-{next(self._ids):05d}"
        self.platform.client_says(thread_id, text, msg_id)
        if t.state != "bot":
            return None
        result = self.assistant.handle(thread_id, t.client_id, text, history, same_issue_contacts,
                                       ai_told_them, details_given)
        self.platform.bot_says(thread_id, result.reply)
        if result.handoff:
            self.platform.escalate(thread_id, result.handoff, f"evt-{next(self._ids):05d}")
        return result
