# Architecture

## Components

| Component | File | Real-world equivalent |
|---|---|---|
| AI assistant turn logic | `assistant.py`, `detection.py`, `router.py`, `withdrawal.py` | An AI agent's escalation rules, procedures and data connectors |
| Assistant platform (mock) | `mock_assistant_platform.py` | Chat platform: threads, outbound webhooks, REST API to post replies |
| Bridge | `bridge.py`, `store.py`, `server.py` | A small integration service you own and run |
| Contact centre (mock) | `mock_genesys.py` | Genesys Cloud Open Messaging + Architect inbound message flow + ACD |
| Contract | `contracts/handoff-contract.v1.schema.json`, `contract.py` | The agreed interface between the two teams |

The bridge is the only component that talks to both systems. Neither vendor needs to know about the other.

## Handoff sequence

```
Client            Assistant platform        Bridge                      Contact centre
  │ message ─────────▶│                         │                             │
  │                   │ detect → decide         │                             │
  │◀── "passing you…" │                         │                             │
  │                   │ conversation.escalated ─▶ verify HMAC (401 if bad)     │
  │                   │   (signed)              │ dedupe event id (200 dup)    │
  │                   │                         │ validate contract            │
  │                   │                         │ POST inbound/open/message ──▶ Architect flow:
  │                   │                         │   channel.metadata             Get Participant Data
  │                   │                         │   .customAttributes            → header fallback
  │                   │                         │   + briefing text              → complaint override
  │                   │                         │◀── conversationId, est. wait   → queue + priority
  │                   │                         │ store mapping                  ACD: priority, then age;
  │◀── honest wait ───│◀── post as admin ───────│                                skills; language;
  │                   │                         │                                widen after 5 min
  │                   │                         │◀── outbound Text (signed) ── agent replies
  │◀── agent reply ───│◀── post (idempotent) ───│ verify, dedupe, map         │
  │ reply ───────────▶│ client.message ────────▶│ forward to same conversation ▶│
  │                   │                         │◀── conversation_ended ────── wrap-up (complaint Y/N
  │◀── CSAT survey ───│◀── close thread ────────│                                required if flagged)
```

## Failure handling

| Failure | Behaviour | Test |
|---|---|---|
| Bad or missing signature (either direction) | 401, nothing processed | `test_bad_signature_is_rejected`, `test_wrong_secret_is_401_over_http` |
| Same webhook delivered twice (at-least-once delivery) | Second copy ignored by event/message id | `test_duplicate_*` |
| Contract fails validation | Dead-lettered; sent as free text with `contract=invalid` header so a person still gets it | bridge `_escalate` |
| Contact centre returns 4xx | Not retried (retrying a bad request won't fix it); dead-lettered; client told | `test_top_level_custom_attributes_are_rejected` |
| Contact centre down / 5xx / timeout | Exponential backoff, then dead-letter, and the client is told plainly | `test_genesys_outage_retries_dead_letters_and_tells_the_client` |
| Outage continues | Circuit breaker opens after 3 consecutive failures: later escalations make **no call and no wait**, are dead-lettered, and the client is told at once. After 30 s one trial call is let through; success closes it | `test_outage_is_detected_once_then_later_clients_are_told_immediately`, `test_recovers_after_cooldown_when_genesys_is_back` |
| 4xx while the breaker is closed | Does **not** count towards opening it: the service answered, our request was wrong | `test_bad_requests_do_not_open_the_breaker` |
| Priority reorders the queue, so the contact centre gives no estimate | Bridge reads a queue snapshot and tells the client a forecast range; learns from the actual wait at the agent's first reply | `test_bridge_gives_a_range_when_priority_reorders_the_queue`, `test_bridge_learns_from_the_actual_wait_at_first_agent_reply` |
| Client at risk during an outage | Failure message still includes the crisis line | `test_client_at_risk_still_gets_crisis_line_during_an_outage` |
| Agent reply for an unknown conversation | 409 + dead-letter (never dropped silently) | `test_unmapped_reply_is_dead_lettered_not_lost` |
| Participant data stripped in transit | Architect falls back to the header line of the briefing | `test_free_text_only_platform_still_routes_by_header` |
| Client types a fake header | Ignored: only line 1 of the bridge-authored note is parsed; transcript lines are speaker-prefixed | `test_client_cannot_spoof_the_routing_header` |
| Deprecated endpoint used | Rejected | `test_deprecated_endpoint_is_refused` |
| Out of hours / no estimate | Wait message says so instead of inventing a number | `test_out_of_hours_wait_message_is_honest`, `test_wait_message_bands` |
| Model layer slow or down | Rules still run; turn flagged `llm_unavailable` so the rate is measurable | `test_model_failure_falls_back_to_rules_and_is_counted` |

## What production would add (not built here)

- Real clients for both APIs: OAuth client-credentials for Genesys, token refresh, rate-limit handling (429 + `Retry-After`).
- A real database (Postgres) and a durable queue in front of the outbound calls, so retries survive a restart.
  The SQLite store and in-process retry here show the logic, not the durability.
- Dead-letter replay when the breaker closes, and an alert when the breaker opens or the dead-letter count is above
  zero. Breaker state is already exposed on `/metrics`. In production it would be shared across instances (Redis),
  not held per process.
- Secrets from a vault and rotation with two valid secrets during the switch.
- PII handling: the briefing deliberately carries the last 4 digits of an account only. A redaction pass for card
  numbers and sort codes before anything is logged.
- Wait forecasting fed by real queue data: queue observation queries for waiting and interacting counts and their
  durations, analytics for completed handle times, and the workforce-management interval forecast for arrivals. The
  calibration state would be persisted per queue, not held in memory.
- Tracing: one correlation id (`hl_conversation_id`) across both systems' logs.
- The assistant's general answers via retrieval over approved help content. This lab only answers the one how-to
  question it needs and focuses on the handoff.
