"""A stand-in for the AI assistant's messaging platform (the role Intercom plays in a
Fin + Genesys stack): it owns the client's chat thread and announces escalations by
signed webhook. Human replies are posted back into the same thread through its API.
It is NOT Intercom; the shapes are simplified for testing.
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from typing import Callable

from .signatures import sign


@dataclass
class Thread:
    id: str
    client_id: str
    messages: list[tuple[str, str]] = field(default_factory=list)   # (sender, text)
    state: str = "bot"           # bot | with_human | closed
    csat_requested: bool = False


class MockAssistantPlatform:
    def __init__(self, webhook_secret: str, deliver: Callable[[bytes, dict], None]):
        self.secret = webhook_secret
        self.deliver = deliver
        self.threads: dict[str, Thread] = {}
        self._ids = itertools.count(1)
        self.posted_keys: set[str] = set()

    def open_thread(self, client_id: str) -> Thread:
        t = Thread(f"conv-{next(self._ids):04d}", client_id)
        self.threads[t.id] = t
        return t

    def client_says(self, thread_id: str, text: str, message_id: str) -> None:
        t = self.threads[thread_id]
        t.messages.append(("client", text))
        if t.state == "with_human":
            self._emit({"id": message_id, "type": "client.message", "conversation_id": t.id,
                        "client_id": t.client_id, "text": text})

    def bot_says(self, thread_id: str, text: str) -> None:
        self.threads[thread_id].messages.append(("assistant", text))

    def escalate(self, thread_id: str, handoff: dict, event_id: str) -> None:
        t = self.threads[thread_id]
        t.state = "with_human"
        self._emit({"id": event_id, "type": "conversation.escalated", "conversation_id": t.id,
                    "client_id": t.client_id, "handoff": handoff,
                    "transcript": [f"{s}: {m}" for s, m in t.messages]})

    # REST API used by the bridge ------------------------------------------
    def reply_as_admin(self, thread_id: str, text: str, idempotency_key: str, author: str = "support team") -> bool:
        if idempotency_key in self.posted_keys:
            return False
        self.posted_keys.add(idempotency_key)
        self.threads[thread_id].messages.append((f"human:{author}", text))
        return True

    def close_after_human(self, thread_id: str) -> None:
        t = self.threads[thread_id]
        t.state, t.csat_requested = "closed", True

    def _emit(self, event: dict) -> None:
        body = json.dumps(event).encode()
        self.deliver(body, {"X-Signature-256": sign(body, self.secret)})
