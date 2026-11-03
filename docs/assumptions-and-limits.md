# Assumptions and limits

Read this before trusting any number in the repository.

## What this is

- An independent portfolio project. It is not affiliated with, endorsed by, or built from inside knowledge of any
  company. It uses no company's branding. The `hl_` prefix stands for Handoff Lab.
- The scenario and data pack come from a public-style case study. All clients, conversations, withdrawals and
  references are synthetic.

## What is real and what is mocked

| Real, tested code | Mocked |
|---|---|
| Contract, validation, briefing, header parsing | The AI assistant platform |
| Detection rules, routing, withdrawal logic | Genesys Cloud (Open Messaging, Architect, ACD) |
| Bridge: HMAC, idempotency, mapping, retry, dead-letter | Account and payment data |
| HTTP service for the bridge (stdlib) | The model behind `llm_detector.py` (tests use a fake) |
| Eval harness, metrics, simulation, report | |

The mocks follow public documentation as I understood it. The real products have behaviour the mocks don't model
(rate limits, attribute limits, retries on their side, UI configuration).

## Evaluation limits

- All three eval sets are small (40, 88 and 61 conversations) and were written and labelled by one person, who also
  wrote the rules. Even the "blind" set shares its author's idea of how clients talk. Real transcripts, labelled by
  two people with agreement measured, are the next step.
- The confidence intervals are wide on purpose. The blind self-harm gate rests on 4 messages.
- `make eval-llm` has not been run in this repository, so no claim is made about the model layer's performance.
- The non-English fallback uses a tiny stopword guess and only ensures such messages reach a person. Routing them to
  the right queue is not attempted.

## Simulation limits

- The simulated month is calibrated to the data pack's headline numbers. Assumptions A1–A5 in `simulate.py` (for
  example, abandoners' true satisfaction of about 2.0) are guesses, and A1 is unobservable by definition.
- It shows how measurements can mislead. **It is not a forecast** of what this design would achieve.

## Wait-forecast limits

- The backtest world is simulated and loosely calibrated to the data pack: median wait about 8 minutes, a
  market-open surge, about 14% abandonment. It deliberately differs from the forecaster's assumptions, but it is
  still my simulation, not real queue data.
- Clients who abandoned have no observed wait and are excluded from accuracy, which flatters every method.
- The calibrated range is close to, but not at, its target: it held 76% (target 80%) with 13% above (target 10%).
- One queue with uniform agents is modelled. Real queues share agents across queues and skills.

## Known misses that are kept visible

Every miss is listed in `reports/eval_results.md`, including sarcasm ("Cheers for the copy-paste answer again"),
indirect self-harm ("I won't be around"), scams ("a WhatsApp group told me to deposit"), and false alarms ("I'm a bit
anxious about volatility…").
