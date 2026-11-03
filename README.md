# Handoff Lab

**A working, measured AI-assistant-to-human handoff for a regulated trading platform's support chat.**

When an AI support assistant hands a chat to a human, three things usually go wrong. The human re-asks what the
client already said. Complaints and vulnerable clients get a help article instead of a person. And the dashboard
says things are fine because the people having the worst time never answer the survey.

Handoff Lab builds the whole path end to end and then measures it honestly:

```
client ─▶ AI assistant ──signed webhook──▶ Bridge ──Open Messaging──▶ Contact centre (Architect flow + ACD)
  ▲      (detect → tier →                 (verify, dedupe,             (route by participant data,
  │       answer from data,                 map, retry, breaker,                  priority, skills, language,
  │       or hand off with                  dead-letter,                 bullseye widening, wrap-up)
  │       a briefing)                       honest wait msg)                       │
  └──────────── same chat thread ◀── REST reply ◀── Bridge ◀──signed webhook── human agent
```

The assistant platform and the contact centre are **mocks** shaped like Intercom Fin and Genesys Cloud Open
Messaging. Everything else is real code with tests: the handoff contract, detection, routing, withdrawal-status
logic, bridge reliability (including a circuit breaker), HTTP service, evaluation harness, simulation and report.
See [docs/mapping-to-fin-genesys.md](docs/mapping-to-fin-genesys.md) for how each piece maps onto the real products.

> Unofficial and independent. Not affiliated with, or built from inside knowledge of, any company. All clients,
> conversations and numbers are synthetic. See [docs/assumptions-and-limits.md](docs/assumptions-and-limits.md).

## Results (from `make all`)

| What was measured | Result | Read it as |
|---|---|---|
| Unit and integration tests | **67 pass** | contract, detection, routing, bridge, circuit breaker, ACD, HTTP, metrics |
| Rules v1, blind held-out: reached a person when one was needed | **37%** (19/51) | the first honest number |
| Rules v2, dev set (tuned on) | 96% (24/25) | flattering by construction |
| Rules v2, **new blind held-out**: reached a person when needed | **43%** (15/35, 95% CI 28–59%) | **the honest number** |
| Rules v2, blind: self-harm messages routed to vulnerable-client team | **1 / 4** | this is why rules alone are not enough |
| Simulated month: containment vs true resolution (no re-contact) | 38% vs **33%** | containment flatters the AI |
| Queue abandoners who answered the CSAT survey | **6 of 548** | the worst experiences are invisible to CSAT |

**The main finding is a negative result, reported as one.** Keyword rules that look excellent on the data they
were tuned on reach a person in fewer than half of unseen cases that need one. Rewriting them by concept barely
moved the blind number (37% → 43%). So the design keeps the rules as an auditable floor, adds a model layer that can
only *add* flags ([`llm_detector.py`](handoff_lab/llm_detector.py)), and makes "talk to a person" available on every
turn until a detector clears the self-harm and fraud gates on blind data. The model layer is scored by
`make eval-llm`. It has not been run here (no API key), so no number is claimed for it.

Full results: [`reports/eval_results.md`](reports/eval_results.md) · report page: `reports/index.html` · what the agent
sees: `reports/agent_view.html` · six case conversations: [`reports/demo_transcripts.md`](reports/demo_transcripts.md)

## What a handoff looks like

The client's third message about a missing withdrawal: *"You keep telling me the same thing. This is useless. Get me
a person."* The agent receives routing attributes (participant data):

```
hl_queue=FundingPayments  hl_priority=P2  hl_topic=withdrawal_not_received  hl_ref=WD-55120
hl_flags=asked_for_human,frustrated,late_payment,possible_complaint,repeat_contact
```

…and a briefing in a fixed order, whose first line still routes the chat if only free text survives:

```
[HANDOFF v1] queue=FundingPayments priority=P2 flags=asked_for_human,frustrated,late_payment,... lang=en auth=verified
SUMMARY: Client asked for a person. Topic: withdrawal not received. ...
CLIENT WANTS: To know where the money is and receive it.
AI ALREADY TOLD THEM: 26 Oct: Withdrawal times article
DATA PULLED: WD-55120 | GBP 2500.00 | sent 2026-10-23 | FASTER_PAYMENTS | ending 4417 | ref HLW-88213 | due_by 2026-10-26
SIGNALS: repeat_contact ("2 earlier contacts on this issue"); late_payment ("WD-55120 sent 2026-10-23, due by 2026-10-26"); ...
DO NOT ASK AGAIN: amount £2,500; account ending 4417
SUGGESTED NEXT STEP: Trace the payment with the bank reference; confirm whether to log a complaint. (the human decides)
```

The client is told exactly what was passed on, and gets a wait message that is true, including out of hours.

## Quickstart

Python 3.10+. One runtime dependency (`jsonschema`), plus `matplotlib` for the report.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make all          # tests → demo → evals → report
open reports/index.html
```

| Command | What it does |
|---|---|
| `make test` | 67 tests |
| `make demo` | runs the six case conversations end to end; writes transcripts and the agent view |
| `make eval` | scores detection + routing on dev and both held-out sets; `--gate` fails CI on safety gates |
| `make eval-llm` | same, with the model layer (needs `ANTHROPIC_API_KEY`) |
| `make report` | builds `reports/index.html` |
| `make serve` | runs the bridge as an HTTP service on :8080 (`/webhooks/assistant`, `/webhooks/genesys`, `/healthz`, `/metrics`) |

## Repository map

```
contracts/handoff-contract.v1.schema.json   the contract between AI and contact centre (JSON Schema)
handoff_lab/
  contract.py        Handoff dataclass, validation, note rendering, header parsing
  detection.py       deterministic signals (recall-tuned for harm, precision-tuned for irritation)
  llm_detector.py    optional model layer: add-only, allowlisted, falls back to rules
  router.py          tiering: self-harm > vulnerability > fraud > complaint > access > advice > asked-for-person
  withdrawal.py      status-first withdrawal answers (working days, UK bank holidays, no tipping-off)
  assistant.py       one assistant turn: detect → decide → answer from data or hand off with a briefing
  bridge.py          signature check, idempotency, mapping, retry/backoff, dead-letter, honest wait messages
  circuit.py         circuit breaker for Genesys calls (adapted from my ATLAS project)
  store.py           SQLite: conversation mapping, seen events, dead letters
  server.py          the bridge as an HTTP service (stdlib)
  mock_assistant_platform.py, mock_genesys.py   the two external systems, mocked
  metrics.py         confusion matrix, Wilson CI, rule of three, true resolution, mix-shift
  simulate.py        synthetic month calibrated to the case data pack
  report.py          the HTML report
evals/               dev.jsonl, heldout.jsonl, heldout2.jsonl (hand-labelled), run_evals.py
docs/                architecture, design decisions, mapping to Fin/Genesys, assumptions and limits, walkthrough
```

## Documentation

- [Architecture](docs/architecture.md): components, sequence, failure handling, what production would add
- [Design decisions](docs/design.md): why each choice, and what was rejected
- [Mapping to Fin and Genesys Cloud](docs/mapping-to-fin-genesys.md)
- [Assumptions and limits](docs/assumptions-and-limits.md)
- [Walkthrough and likely questions](docs/walkthrough.md)

MIT licence.
