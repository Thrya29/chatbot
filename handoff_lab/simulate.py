"""A synthetic 30-day month of chat, calibrated to the case study's data pack.

Purpose: show what the dashboard reports versus what is true, so the measurement plan
can be argued with numbers. It is NOT a forecast of what this design would achieve.

Calibration targets (data pack): 5,000 chats; 38% contained; CSAT 4.3 AI-resolved, 3.4
handed off, 2.6 after 3+ frustrated turns; survey response ~9% AI / ~22% handed off;
12% of contained chats re-contact within 48h (CSAT 2.9); asked for a human early and
kept in the AI flow: 2.4; 17% abandon the queue, almost never surveyed; per-topic share,
containment and CSAT from table G.

ASSUMPTIONS (not in the data pack, marked so they can be challenged):
  A1 abandoners' true satisfaction ~2.0 (unobservable by definition: they are not surveyed)
  A2 per-chat score = round(normal(topic mean, 0.9)) clipped to 1..5
  A3 a re-contacting client would have rated 2.9 had they answered (as the brief observed)
  A4 response rate for abandoners 1%
  A5 latent-mean offsets fitted so the surveyed averages match 4.3 / 3.4 (see constants)
"""
from __future__ import annotations

import random

# topic: (share, containment, csat_ai, csat_handed)  -- data pack table G
TOPICS = {
    "Funding": (0.19, 0.29, 4.1, 3.0),
    "KYC": (0.15, 0.58, 4.4, 3.6),
    "Trade queries": (0.14, 0.22, 3.9, 3.1),
    "Platform": (0.13, 0.45, 4.5, 3.7),
    "Margin & close-outs": (0.11, 0.18, 3.8, 3.0),
    "Statements": (0.10, 0.52, 4.5, 3.8),
    "Product info": (0.08, 0.62, 4.6, 3.9),
    "Access & security": (0.06, 0.20, 4.0, 3.2),
    "Admin": (0.04, 0.36, 4.3, 3.7),
}
RESPONSE = {"ai": 0.09, "handed": 0.22, "abandoned": 0.01}
RECONTACT_48H = 0.12
ABANDON = 0.17
FRUSTRATED_SHARE = 0.34           # of handed-off, from the 200-chat review
FRUSTRATED_CSAT = 2.6
ASKED_HUMAN_KEPT_SHARE = 0.10     # of contained chats (assumption, sized so the 2.4 cohort is visible)
ASKED_HUMAN_KEPT_CSAT = 2.4
ABANDON_CSAT = 2.0                # A1
# A5 latent-mean offsets, fitted (scripts/calibrate in tests) so the SURVEYED averages match the
# data pack's 4.3 / 3.4. They are large because a 1-5 scale compresses the top end, and the
# reported numbers already include re-contacting and frustrated clients.
AI_OFFSET = 0.75
HANDED_OFFSET = 0.6


def _score(rng: random.Random, mean: float) -> int:
    return max(1, min(5, round(rng.gauss(mean, 0.9))))


def simulate(n: int = 5000, seed: int = 7, topic_mix: dict[str, float] | None = None,
             containment: dict[str, float] | None = None) -> list[dict]:
    rng = random.Random(seed)
    names = list(TOPICS)
    weights = [(topic_mix or {}).get(t, TOPICS[t][0]) for t in names]
    out = []
    for i in range(n):
        t = rng.choices(names, weights)[0]
        _share, cont, csat_ai, csat_h = TOPICS[t]
        cont = (containment or {}).get(t, cont)
        handed = rng.random() >= cont
        c = {"id": i, "topic": t, "handed_off": handed, "recontact_7d": False, "abandoned": False,
             "frustrated": False, "asked_human_kept": False}
        if handed:
            c["frustrated"] = rng.random() < FRUSTRATED_SHARE
            c["abandoned"] = rng.random() < ABANDON
            mean = ABANDON_CSAT if c["abandoned"] else FRUSTRATED_CSAT if c["frustrated"] else csat_h + HANDED_OFFSET
            resp = RESPONSE["abandoned"] if c["abandoned"] else RESPONSE["handed"]
        else:
            c["recontact_7d"] = rng.random() < RECONTACT_48H
            c["asked_human_kept"] = (not c["recontact_7d"]) and rng.random() < ASKED_HUMAN_KEPT_SHARE
            mean = (2.9 if c["recontact_7d"] else ASKED_HUMAN_KEPT_CSAT if c["asked_human_kept"]
                    else csat_ai + AI_OFFSET)
            resp = RESPONSE["ai"]
        c["true_csat"] = _score(rng, mean)
        c["responded"] = rng.random() < resp
        c["objected"] = c["asked_human_kept"]
        out.append(c)
    return out


def summarise(convs: list[dict]) -> dict:
    def avg(xs):
        xs = list(xs)
        return sum(xs) / len(xs) if xs else float("nan")

    ai = [c for c in convs if not c["handed_off"]]
    ho = [c for c in convs if c["handed_off"]]
    return {
        "n": len(convs),
        "containment": len(ai) / len(convs),
        "true_resolution": sum(1 for c in ai if not c["recontact_7d"]) / len(convs),
        "reported_csat_ai": avg(c["true_csat"] for c in ai if c["responded"]),
        "reported_csat_handed": avg(c["true_csat"] for c in ho if c["responded"]),
        "true_csat_ai": avg(c["true_csat"] for c in ai),
        "true_csat_handed": avg(c["true_csat"] for c in ho),
        "responses_ai": sum(c["responded"] for c in ai),
        "responses_handed": sum(c["responded"] for c in ho),
        "reported_csat_frustrated": avg(c["true_csat"] for c in ho if c["frustrated"] and not c["abandoned"]
                                        and c["responded"]),
        "reported_csat_recontact": avg(c["true_csat"] for c in ai if c["recontact_7d"] and c["responded"]),
        "abandoned": sum(c["abandoned"] for c in ho),
        "abandoned_responses": sum(c["abandoned"] and c["responded"] for c in ho),
    }


def responses_needed(delta: float, sd: float = 1.0, z_alpha: float = 1.96, z_beta: float = 0.84) -> int:
    """Survey responses needed PER ARM to detect a CSAT change of `delta` (two-sided 5%, 80% power)."""
    return int(2 * (z_alpha + z_beta) ** 2 * sd ** 2 / delta ** 2) + 1
