"""The bridge: keeps the assistant's chat thread and the Genesys conversation in step.

Inbound to Genesys:  assistant escalation webhook -> Open Messaging inbound message,
                     with the contract's attributes in channel.metadata.customAttributes
                     and the fixed-order briefing as the first message text.
Outbound to client:  Genesys signed webhook -> assistant REST reply in the same thread.

Reliability rules:
  * every webhook signature is verified (constant-time)
  * every message ID is processed once (at-least-once delivery is assumed)
  * 4xx from Genesys is not retried (the request is wrong); other failures are
    retried with exponential backoff, then dead-lettered and alerted
  * a circuit breaker stops calling Genesys during an outage: after 3 consecutive
    failures, escalations are dead-lettered and the client is told immediately, instead of
    every client waiting through backoff (see circuit.py)
  * the client is always told something true about the wait
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Callable

import jsonschema

from .circuit import CircuitBreaker, State
from .contract import render_note, validate
from .mock_genesys import INBOUND_PATH, BadRequest
from .router import CRISIS_LINE
from .signatures import verify
from .store import Store
from .wait_forecast import OnlineCalibrator, QueueSnapshot, WaitForecaster, range_message

log = logging.getLogger("handoff_lab.bridge")


def wait_message(seconds: int | None, staffed: bool, ref: str = "") -> str:
    if seconds is None and not staffed:
        msg = ("Our team is smaller at this time, so the wait is longer than usual. You can close this window: "
               "the reply will appear here and by email.")
        return msg + (f" Your bank can also trace the payment now using reference {ref}." if ref else "")
    if seconds is None:
        return ("I can't see the current wait. Your case is in the team's queue with all the details, and the "
                "reply will come here and by email.")
    if seconds == 0:
        return "Someone from the team has your conversation now and will reply shortly."
    minutes = max(1, round(seconds / 60))
    if seconds <= 300:
        return f"Someone from the team should reply in about {minutes} minute{'s' if minutes > 1 else ''}."
    if seconds <= 1200:
        return (f"The team is busy right now. The current wait is about {minutes} minutes. You don't need to keep "
                "this open: the reply will appear here and we'll email you.")
    return (f"It's our busiest time of day. The current wait is about {minutes} minutes. You can close this "
            "window: we'll notify you in the app and by email.")


class Bridge:
    def __init__(self, store: Store, genesys_post: Callable[[str, dict], dict], assistant_api,
                 assistant_secret: str, genesys_secret: str, integration_id: str,
                 is_staffed: Callable[[], bool] = lambda: True, retries: int = 3,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 breaker: CircuitBreaker | None = None,
                 queue_snapshot: Callable[[str], QueueSnapshot | None] | None = None,
                 forecaster: WaitForecaster | None = None, calibrator: OnlineCalibrator | None = None):
        self.store, self.genesys_post, self.assistant = store, genesys_post, assistant_api
        self.assistant_secret, self.genesys_secret = assistant_secret, genesys_secret
        self.path = INBOUND_PATH.format(integrationId=integration_id)
        self.is_staffed, self.retries, self.sleep = is_staffed, retries, sleep
        self.breaker = breaker or CircuitBreaker(clock=clock)
        self.clock = clock
        # Wait-range forecasting (wait_forecast.py). Without a queue snapshot source the bridge falls
        # back to the contact centre's own estimate and the fixed messages.
        self.queue_snapshot = queue_snapshot
        self.forecaster = forecaster or WaitForecaster()
        self.calibrator = calibrator or OnlineCalibrator()
        self._pending_waits: dict[str, tuple] = {}    # genesys conversation -> (raw forecast, handoff time)

    # ------------------------------------------------- assistant -> Genesys
    def on_assistant_webhook(self, body: bytes, headers: dict) -> tuple[int, dict]:
        if not verify(body, headers.get("X-Signature-256"), self.assistant_secret):
            log.warning("rejected assistant webhook: bad signature")
            return 401, {"error": "bad signature"}
        event = json.loads(body)
        if not self.store.first_time(event["id"]):
            return 200, {"status": "duplicate ignored"}
        if event["type"] == "conversation.escalated":
            return self._escalate(event)
        if event["type"] == "client.message":
            return self._forward_client_message(event)
        return 200, {"status": "ignored"}

    def _escalate(self, event: dict) -> tuple[int, dict]:
        conv_id, handoff = event["conversation_id"], event["handoff"]
        if self.store.by_assistant(conv_id):
            return 200, {"status": "already linked"}
        attrs, contract_ok = handoff.get("attributes", {}), True
        try:
            validate(handoff)
        except jsonschema.ValidationError as e:
            contract_ok = False
            self.store.dead_letter("contract_invalid", handoff, e.message)
            attrs = {}
        text = render_note(handoff) if contract_ok else "[HANDOFF v1] contract=invalid\n" + json.dumps(handoff)[:1500]
        transcript = event.get("transcript", [])[-8:]
        if transcript:
            text += "\nRECENT TRANSCRIPT:\n" + "\n".join(transcript)
        body = {
            "channel": {
                "from": {"id": f"assistant-{conv_id}", "idType": "Opaque", "nickname": "Client"},
                "messageId": event["id"],
                "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "metadata": {"customAttributes": attrs},
            },
            "text": text,
        }
        result = self._post_with_retry(body, "escalation")
        if result is None:
            msg = ("I'm sorry, I couldn't pass this to our team just now. Your details are saved, so you won't "
                   "need to repeat them. Please try again in a few minutes, or call us.")
            if "self_harm_risk" in attrs.get("hl_flags", "").split(","):
                msg += " " + CRISIS_LINE     # never leave a client at risk with only "try again later"
            self.assistant.reply_as_admin(conv_id, msg, f"fail-{event['id']}", "system")
            return 502, {"error": "genesys unavailable; dead-lettered", "breaker": self.breaker.state().value}
        self.store.link(conv_id, result["conversationId"], event["client_id"])
        ref = attrs.get("hl_ref", "") if attrs.get("hl_topic") == "withdrawal_not_received" else ""
        self.assistant.reply_as_admin(conv_id, self._wait_text(result, ref), f"wait-{event['id']}", "system")
        return 200, {"status": "linked", **result}

    def _wait_text(self, result: dict, ref: str) -> str:
        est, staffed = result.get("estimatedWaitSeconds"), self.is_staffed()
        if est != 0 and staffed and self.queue_snapshot:
            snap = self.queue_snapshot(result["conversationId"])
            if snap is not None:
                raw = self.forecaster.forecast(snap)
                self._pending_waits[result["conversationId"]] = (raw, self.clock())
                return range_message(self.calibrator.apply(raw))
        return wait_message(est, staffed, ref)

    def _forward_client_message(self, event: dict) -> tuple[int, dict]:
        link = self.store.by_assistant(event["conversation_id"])
        if not link:
            self.store.dead_letter("unmapped_client_message", event, "no Genesys conversation for this thread")
            return 409, {"error": "no mapping"}
        body = {"channel": {"from": {"id": f"assistant-{event['conversation_id']}", "idType": "Opaque"},
                            "messageId": event["id"],
                            "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
                "text": event["text"]}
        result = self._post_with_retry(body, "client_message")
        return (200, {"status": "forwarded"}) if result else (502, {"error": "dead-lettered"})

    def _post_with_retry(self, body: dict, kind: str) -> dict | None:
        delay = 0.5
        for attempt in range(1, self.retries + 1):
            if not self.breaker.allow():                # outage already known: fail fast
                self.store.dead_letter(kind, body, "circuit open: genesys not called")
                log.warning("genesys circuit open; %s dead-lettered without a call", kind)
                return None
            try:
                result = self.genesys_post(self.path, body)
            except BadRequest as e:                     # our request is wrong: don't retry, and the
                self.breaker.record_success()           # service answered, so it is not an outage
                self.store.dead_letter(kind, body, str(e))
                log.error("genesys rejected %s: %s", kind, e)
                return None
            except Exception as e:                      # transient: back off and retry
                self.breaker.record_failure()
                log.warning("genesys %s attempt %d failed: %s", kind, attempt, e)
                if attempt == self.retries or self.breaker.state() is State.OPEN:
                    self.store.dead_letter(kind, body, f"{type(e).__name__}: {e}")
                    return None
                self.sleep(delay)
                delay *= 2
                continue
            self.breaker.record_success()
            return result
        return None

    # ------------------------------------------------- Genesys -> assistant
    def on_genesys_webhook(self, body: bytes, headers: dict) -> tuple[int, dict]:
        if not verify(body, headers.get("X-Hub-Signature-256"), self.genesys_secret):
            log.warning("rejected genesys webhook: bad signature")
            return 401, {"error": "bad signature"}
        msg = json.loads(body)
        if not self.store.first_time(msg["id"]):
            return 200, {"status": "duplicate ignored"}
        link = self.store.by_genesys(msg["conversationId"])
        if not link:
            self.store.dead_letter("unmapped_genesys_message", msg, "unknown conversation")
            return 409, {"error": "no mapping"}
        assistant_id = link[0]
        if msg.get("type") == "Event" and msg.get("event") == "conversation_ended":
            self.store.set_status(assistant_id, "closed")
            self.assistant.close_after_human(assistant_id)
            return 200, {"status": "closed"}
        pending = self._pending_waits.pop(msg["conversationId"], None)
        if pending:                                   # first agent reply: the real wait is now known
            raw, t0 = pending
            self.calibrator.observe(raw, self.clock() - t0)
        self.assistant.reply_as_admin(assistant_id, msg["text"], idempotency_key=msg["id"], author="agent")
        return 200, {"status": "delivered"}
