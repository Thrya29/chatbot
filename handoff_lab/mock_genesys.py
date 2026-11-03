"""An in-memory stand-in for a Genesys Cloud org, just enough to test the handoff.

What it models (from Genesys public documentation):
  * Open Messaging inbound: POST /api/v2/conversations/messages/{integrationId}/inbound/open/message
    (the replacement for the catch-all /inbound/open endpoint planned for retirement on 16 Nov 2026).
    Only channel.metadata.customAttributes become participant data; customAttributes at the
    top level are rejected with 400, as reported by Genesys developers.
  * An Inbound Message flow ("Architect"): Get Participant Data -> decisions -> Transfer to ACD.
  * ACD: priority first, then waiting time; bullseye-style widening from language-matched agents
    to any queue member after a timeout; per-agent chat utilisation.
  * Outbound webhook: agent messages POSTed to the integration's URL, signed with
    X-Hub-Signature-256.
  * Mandatory wrap-up: a possible_complaint chat can't close without a complaint yes/no.
It is NOT Genesys. Names and behaviour are simplified for testing.
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from .contract import parse_header
from .signatures import sign
from .wait_forecast import QueueSnapshot

INBOUND_PATH = "/api/v2/conversations/messages/{integrationId}/inbound/open/message"
DEPRECATED_PATH = "/api/v2/conversations/messages/inbound/open"
PRIORITY_RANK = {"P1": 1, "P2": 2, "P3": 3}
LANG_WIDEN_AFTER = timedelta(minutes=5)


class BadRequest(Exception):
    pass


@dataclass
class Agent:
    name: str
    queues: set[str]
    languages: set[str]
    capacity: int = 3
    active: list[str] = field(default_factory=list)


@dataclass
class Conversation:
    id: str
    from_id: str
    participant_data: dict[str, str]
    queue: str
    priority: str
    lang: str
    arrived: datetime
    routed_by: str
    messages: list[tuple[str, str]] = field(default_factory=list)   # (sender, text)
    agent: str | None = None
    connected_at: datetime | None = None
    state: str = "queued"          # queued | connected | ended
    wrap_up: dict | None = None


class MockGenesys:
    def __init__(self, integration_id: str, webhook_secret: str, deliver: Callable[[bytes, dict], None],
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc).replace(tzinfo=None), free_text_only: bool = False,
                 staffed: Callable[[datetime], bool] | None = None):
        self.integration_id = integration_id
        self.secret = webhook_secret
        self.deliver = deliver                  # how outbound webhooks reach the bridge
        self.now = now
        self.free_text_only = free_text_only    # simulates "platform passes only free text"
        self.staffed = staffed or (lambda t: t.weekday() < 5 and 7 <= t.hour < 22)
        self.agents: list[Agent] = []
        self.conversations: dict[str, Conversation] = {}
        self.by_from: dict[str, str] = {}
        self._ids = itertools.count(1)
        self._msg_ids = itertools.count(1)
        self.requests: list[str] = []

    # ---------------------------------------------------------------- inbound
    def post(self, path: str, body: dict) -> dict:
        self.requests.append(path)
        if path == DEPRECATED_PATH:
            raise BadRequest("410 Gone: the catch-all inbound endpoint is retired; use "
                             "/{integrationId}/inbound/open/message")
        if path != INBOUND_PATH.format(integrationId=self.integration_id):
            raise BadRequest("404 unknown endpoint")
        if "customAttributes" in body:
            raise BadRequest("400 bad.request: customAttributes must be under channel.metadata")
        ch = body.get("channel") or {}
        frm = (ch.get("from") or {}).get("id")
        if not frm or "text" not in body or not ch.get("messageId"):
            raise BadRequest("400 bad.request: channel.from.id, channel.messageId and text are required")

        if frm in self.by_from and self.conversations[self.by_from[frm]].state != "ended":
            conv = self.conversations[self.by_from[frm]]
            conv.messages.append(("client", body["text"]))
            return {"conversationId": conv.id, "routed": False}

        attrs = {} if self.free_text_only else dict(((ch.get("metadata") or {}).get("customAttributes") or {}))
        conv = self._architect_inbound_flow(frm, attrs, body["text"])
        self.conversations[conv.id] = conv
        self.by_from[frm] = conv.id
        self._acd()
        return {"conversationId": conv.id, "routed": True, "queue": conv.queue, "priority": conv.priority,
                "estimatedWaitSeconds": self.estimated_wait(conv.id), "routedBy": conv.routed_by}

    def _architect_inbound_flow(self, frm: str, attrs: dict, text: str) -> Conversation:
        """Rule FP-REPEAT-01 as an Inbound Message flow."""
        routed_by = "participant_data"
        queue, priority = attrs.get("hl_queue"), attrs.get("hl_priority")
        flags, lang = attrs.get("hl_flags", ""), attrs.get("hl_lang", "en")
        if not queue:                                   # attributes missing: parse the note header
            hdr = parse_header(text)
            if hdr:
                routed_by = "header_string_match"
                queue, priority = hdr.get("queue"), hdr.get("priority")
                flags, lang = hdr.get("flags", ""), hdr.get("lang", "en")
                attrs = {"hl_queue": queue, "hl_priority": priority, "hl_flags": flags, "hl_lang": lang}
            else:
                routed_by = "default_queue"
                queue, priority = "General", "P3"
        flag_set = set(filter(None, flags.split(",")))
        if "complaint" in flag_set and queue not in ("PriorityVulnerable", "Security"):
            queue, priority = "Complaints", min(priority or "P2", "P2", key=PRIORITY_RANK.get)
        conv = Conversation(f"gconv-{next(self._ids):04d}", frm, attrs, queue, priority or "P3", lang,
                            self.now(), routed_by)
        conv.messages.append(("client", text))
        return conv

    # -------------------------------------------------------------------- ACD
    def add_agent(self, agent: Agent) -> None:
        self.agents.append(agent)
        self._acd()

    def _acd(self) -> None:
        waiting = sorted((c for c in self.conversations.values() if c.state == "queued"),
                         key=lambda c: (PRIORITY_RANK[c.priority], c.arrived))
        for conv in waiting:
            members = [a for a in self.agents if conv.queue in a.queues and len(a.active) < a.capacity]
            ring1 = [a for a in members if conv.lang in a.languages]
            widened = self.now() - conv.arrived >= LANG_WIDEN_AFTER
            pool = ring1 or (members if widened else [])
            if pool:
                agent = min(pool, key=lambda a: len(a.active))
                agent.active.append(conv.id)
                conv.agent, conv.state, conv.connected_at = agent.name, "connected", self.now()

    def tick(self) -> None:
        """Re-run the ACD, e.g. after time passes (bullseye widening)."""
        self._acd()

    def estimated_wait(self, conv_id: str) -> int | None:
        """Rough estimate. Returns None when priority reordering makes it unreliable."""
        conv = self.conversations[conv_id]
        if conv.state != "queued":
            return 0
        if not self.staffed(self.now()):
            return None
        ahead = [c for c in self.conversations.values() if c.state == "queued" and c.queue == conv.queue
                 and (PRIORITY_RANK[c.priority], c.arrived) < (PRIORITY_RANK[conv.priority], conv.arrived)]
        if any(c.priority != conv.priority for c in ahead):
            return None
        members = max(1, sum(a.capacity for a in self.agents if conv.queue in a.queues))
        return int((len(ahead) + 1) / members * 14 * 60)

    def queue_snapshot(self, conv_id: str, window_s: int = 1800) -> QueueSnapshot | None:
        """What a real deployment reads from queue observation and analytics APIs: slots on the queue,
        how long each in-progress chat has run, who is ahead, and recent higher-priority arrivals."""
        conv = self.conversations[conv_id]
        now = self.now()
        if conv.state != "queued" or not self.staffed(now):
            return None
        members = [a for a in self.agents if conv.queue in a.queues]
        if not members:
            return None
        ages = [(now - self.conversations[c].connected_at).total_seconds()
                for a in members for c in a.active if self.conversations[c].connected_at]
        rank = (PRIORITY_RANK[conv.priority], conv.arrived)
        ahead = sum(1 for c in self.conversations.values() if c.state == "queued" and c.queue == conv.queue
                    and (PRIORITY_RANK[c.priority], c.arrived) < rank)
        higher = sum(1 for c in self.conversations.values() if c.queue == conv.queue
                     and PRIORITY_RANK[c.priority] < PRIORITY_RANK[conv.priority]
                     and (now - c.arrived).total_seconds() <= window_s)
        return QueueSnapshot(sum(a.capacity for a in members), ages, ahead, higher / window_s)

    # ------------------------------------------------------------- outbound
    def agent_reply(self, conv_id: str, text: str) -> None:
        conv = self.conversations[conv_id]
        conv.messages.append(("agent", text))
        body = json.dumps({
            "id": f"gmsg-{next(self._msg_ids):05d}", "conversationId": conv.id,
            "channel": {"to": {"id": conv.from_id}, "time": self.now().isoformat() + "Z"},
            "type": "Text", "text": text, "direction": "Outbound",
        }).encode()
        self.deliver(body, {"X-Hub-Signature-256": sign(body, self.secret)})

    def wrap_up(self, conv_id: str, code: str, complaint: bool | None = None) -> None:
        conv = self.conversations[conv_id]
        if "possible_complaint" in conv.participant_data.get("hl_flags", "") and complaint is None:
            raise BadRequest("wrap-up blocked: answer 'Is this a complaint? Yes/No' first")
        conv.wrap_up = {"code": code, "complaint": complaint}
        conv.state = "ended"
        for a in self.agents:
            if conv_id in a.active:
                a.active.remove(conv_id)
        body = json.dumps({"id": f"gevt-{next(self._msg_ids):05d}", "conversationId": conv.id,
                           "type": "Event", "event": "conversation_ended", "wrapUp": conv.wrap_up}).encode()
        self.deliver(body, {"X-Hub-Signature-256": sign(body, self.secret)})
        self._acd()
