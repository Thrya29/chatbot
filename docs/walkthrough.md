# Walkthrough and likely questions

## A 5-minute demo

1. `make demo`, then open `reports/agent_view.html`. Show conversation 2: queue, priority, flags, the briefing,
   "DO NOT ASK AGAIN". Then open `reports/demo_transcripts.md` and show what the client was told.
2. Show conversation 6 (Saturday 02:00): Security P1, queued, and the out-of-hours message that doesn't invent a wait.
3. `make test`: 76 tests. Name three failure tests: duplicate webhook, Genesys outage with the circuit breaker, client
   spoofing the header.
4. Open `reports/index.html`, section 1. Say the honest number first: 43% on blind data, and 1 of 4 self-harm
   messages. Explain what that changed in the design.
5. Sections 2–5: containment vs true resolution; survey bias; mix shift; months needed to detect a CSAT change.

## Questions to expect, with short answers

**Why not just use an LLM for detection?** It may well be the better detector. That's what the eval harness is for.
But it's slower, costs money, can be prompt-injected and is harder to audit. So it sits on top of rules that can't
be talked out of a flag, and it can only add flags. I would ship it only after it clears the self-harm and fraud
gates on blind, real, double-labelled data.

**Your rules only get 43%. Isn't that a failure?** For the rules, yes, and I'm showing it rather than the 96% dev
number. It's the reason the design doesn't depend on detection alone: "talk to a person" is offered on every turn,
a request for a person is honoured on the first ask, and red-tier checks run before any answer.

**How do you know the 43% is honest?** The set was written after rules v2 were frozen and before I ran them on it.
The first blind set was retired once I'd used its misses, and its result (37%) is kept in `reports/baseline/`. The
weakness is that I wrote the cases and the rules, so the next step is real transcripts labelled by two people.

**What happens if Genesys is down?** The first escalation retries with backoff. After 3 consecutive failures a circuit
breaker opens. Every escalation after that makes no call and doesn't wait: it is dead-lettered with its full payload and
the client is told at once, with the crisis line if they're at risk. After 30 seconds one trial call goes through; if it
succeeds the breaker closes. 4xx responses aren't retried and don't open the breaker, because they mean our request was
wrong, not that Genesys is down. The breaker is adapted from the one in my ATLAS project.

**What if the same webhook arrives twice?** Every event id is recorded the first time. The second copy returns 200
"duplicate ignored", so the sender stops retrying and nothing happens twice.

**Why put routing data in two places?** Attributes are what the flow routes on. The header line in the briefing is
a fallback if the attributes are lost, and it is what a human sees first. Only line 1 of the bridge-authored note is
parsed, so a client typing a fake header changes nothing (there's a test).

**Why is `frustrated` not a routing signal?** It's tuned for precision and only raises priority. Routing on it would
send calm clients to people because of an exclamation mark, and telling a calm client they seem upset annoys them.

**How would you show the handoff improved CSAT?** Mostly I wouldn't claim it from CSAT alone. A 0.3-point change in
handed-off CSAT needs about 230 responses per arm, roughly one month. The same change for one topic needs about two
months, and 0.1 points needs a quarter. So the primary metrics are behavioural, against a holdout: re-ask rate,
re-contact within 48h, abandonment and time to first reply. CSAT is a guardrail, reported with its response rate,
and changes are split into mix and performance.

**Containment went up and handed-off CSAT went down. Is the handoff worse?** Maybe not. See section 4 of the report:
moving easy chats to the AI leaves harder chats for humans, so handed-off CSAT can fall with no change in quality. The
mix-shift decomposition separates the two.

**Why `UNDER_REVIEW` with no reason?** Explaining a compliance review to the client could be tipping off. The
assistant says it's being reviewed and hands off, flagged `restricted`.

**How do you know your wait message is honest?** I backtested it. Over 9 simulated days, with a single number 25% of
clients waited more than 5 minutes longer than they were told, and 46% when priority reordered the queue. With the
calibrated range it was 2%. The range itself is still slightly overconfident: 13% of waits land above it, against a
10% target. The world is simulated, so the next step is replaying real queue logs. The method (simulate a
distribution, calibrate online, check coverage) is from my scheduler project.

**Why a range and not "about 8 minutes"?** A single number is a median, so about half of clients wait longer than
they were told. And priority lets later chats jump ahead, so the number is worst exactly when the queue is busiest.
A range is honest about that uncertainty.

**What would you do in your first month?** Get 500 real handed-off transcripts and have two people label them for
the signals, measuring agreement. Run rules and rules + model against that. Agree the contract with the
contact-centre team. Ship the briefing first, because it's low risk and the 41% re-ask rate is the biggest
visible pain.

**What's missing for production?** A durable queue and Postgres instead of in-process retry and SQLite, OAuth for
Genesys, secret rotation, PII redaction before logging, tracing on one correlation id, and alerting on dead letters.
See `docs/architecture.md`.
