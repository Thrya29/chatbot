# Design decisions

Each decision, why, and what was rejected.

## 1. A versioned contract with two layers

**Decision.** The handoff carries (a) a small set of flat routing attributes with an `hl_` prefix, validated by JSON
Schema, and (b) a human briefing in a fixed section order whose first line is a machine-readable header.

**Why.** Routing needs a few short, flat strings, and contact-centre flows read those. Humans need a briefing that
reads the same way every time, so the agent's eye knows where "DO NOT ASK AGAIN" is. The header line means routing
still works if the attributes are lost anywhere in transit. Validation at the boundary turns "the AI sent something
odd" into a dead-letter with a reason instead of a misrouted client.

**Rejected.** One big JSON blob in a single attribute (agents can't read it, flows can't route on it). Free-text-only
summaries (unroutable and inconsistent).

## 2. Recall for harm, precision for irritation

**Decision.** Complaint, vulnerability, self-harm, fraud and access signals are tuned so that a false alarm is
acceptable. `frustrated` is tuned for precision and never routes on its own.

**Why.** The costs are asymmetric. A missed vulnerable client or an unrecorded complaint is a conduct failure. A false
alarm costs a person a few seconds. But telling a calm client "you seem upset" is itself irritating, so irritation
only adjusts priority and wording.

**Consequence accepted.** "I'm not going to hurt myself, I just want my withdrawal" still reaches the
vulnerable-client team. Negation is allowed to relax the complaint family only, never self-harm, vulnerability
or fraud.

## 3. Order of precedence

self-harm → vulnerability → fraud → complaint → account access (P1 if time-critical or open positions) → advice
boundary → asked for a person / repeat contact (P2 if repeat, frustrated or possible complaint, else P3) → answer.
The most serious signal wins, and red-tier checks run **before** any answer is generated, so a complaint can never be
answered with an article. A request for a person is honoured on the first ask: the data pack shows clients who asked
early and were kept in the AI flow rated it 2.4.

## 4. Status first, from data, with code doing the dates

**Decision.** For "where is my withdrawal", the assistant looks up the withdrawal and a deterministic function decides
whether it is inside its window (working days, UK bank holidays). The model never does date arithmetic.

**Why.** "Withdrawal times" articles are the loop in the case's conversation 2. An answer from the client's own data
either resolves the chat or produces a handoff the human can act on immediately.

**Detail.** `UNDER_REVIEW` gets a neutral reply with no reason, because explaining a compliance review could be
tipping off. Unknown methods or statuses hand off with `data_unavailable` instead of guessing.

## 5. The model layer can only add

The model layer sees the message as data inside tags, must return JSON flags from an allowlist, and is merged
add-only. The router, not the model, picks queue and priority. So the failure modes are bounded: a wrong, slow or
prompt-injected model can at worst send a client to a person.

## 6. Bridge: boring and strict

HMAC verification with constant-time compare; idempotency on every inbound event id; a mapping table between the
two systems' conversation ids; no retries on 4xx; backoff then dead-letter on everything else; never a silent drop.

Retry alone is per request, so during an outage every client would wait through the full backoff and every call would
add load to a failing system. A circuit breaker (adapted from the per-tool breaker in my ATLAS project) detects the
outage once. After that, escalations fail fast, are dead-lettered and the client is told immediately. One trial call
after the cooldown decides whether to close it again. A 4xx doesn't count as a failure: it means our request was
wrong, not that the service is down.
The client always gets a true message about what is happening, including "I couldn't pass this to our team".

## 7. Tell the client a range, and check it held

**Decision.** When the client has to wait, the message gives a range ("6 to 15 minutes"), not one number and not
"I can't see the wait". The range is the P10–P90 of a Monte Carlo simulation of the live queue:
- residual handle time of each chat in progress, from a log-normal learned online from completed chats;
- people ahead, each of whom may abandon once;
- higher-priority chats arriving from an arrival profile per 15-minute interval learned on previous days, so the
  market-open surge is expected.

An online calibrator then nudges the bounds after every actual wait, so that about 10% of waits fall above the range.

**Why.** A single number is a median by nature, so about half of clients wait longer than they were told. And it
breaks completely when priority reorders the queue. In the backtest, 25% of clients waited more than 5 minutes longer
than a single number said (46% when the queue was reordered), against 2% with the calibrated range.

**Trade-offs accepted.** Ranges are wide when the queue is volatile (about 13 minutes in the surge). The calibrated
range is still slightly overconfident (13% above the top against a 10% target). Clients who abandon have no observed
wait, so all accuracy numbers exclude them.

**Rejected.** Monte Carlo with a flat recent arrival rate: it lags the surge (31% above the top during it). A fixed
safety margin: calibration learns the margin from data, and it can shrink it again when the world settles.

The method comes from my Adaptive Parallelism scheduler project, which predicts agent-workflow latency
distributions by simulation, calibrates online and reports P50/P95 coverage.

## 8. Measure what CSAT can't see

Containment is reported next to true resolution (no re-contact). CSAT is reported with its response rate, and next to
behavioural metrics (re-ask, re-contact, abandonment, time to first human reply), because abandoners are almost never
surveyed. Changes to handed-off CSAT are decomposed into mix and performance. And the sample size needed to detect a
change is computed before claiming one.

## 9. Evaluation discipline

Dev set for tuning; held-out set written before running; once a held-out set has been read and used to change the
rules, it is retired and a new blind set is written. Confidence intervals on every rate; the rule of three when a gate
shows zero failures. The first blind result (37%) is kept in `reports/baseline/` so the history can't be rewritten.
