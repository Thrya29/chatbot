"""Measurement helpers: honest numbers with uncertainty attached.

Everything here is plain arithmetic so it can be checked by hand in an interview.
"""
from __future__ import annotations

import math
from collections import Counter


def confusion(y_true: list[bool], y_pred: list[bool]) -> dict[str, int]:
    c = Counter(zip(y_true, y_pred))
    return {"tp": c[(True, True)], "fp": c[(False, True)], "fn": c[(True, False)], "tn": c[(False, False)]}


def prf(cm: dict[str, int]) -> dict[str, float | None]:
    tp, fp, fn = cm["tp"], cm["fp"], cm["fn"]
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p and r else None
    return {"precision": p, "recall": r, "f1": f1}


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion. Behaves sensibly at 0/n and n/n."""
    if n == 0:
        return (0.0, 1.0)
    ph = successes / n
    denom = 1 + z * z / n
    centre = (ph + z * z / (2 * n)) / denom
    half = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def rule_of_three(n: int) -> float:
    """With 0 failures in n trials, the 95% upper bound on the failure rate is about 3/n."""
    return 3 / n if n else 1.0


def mean_ci(values: list[float], z: float = 1.96) -> tuple[float, float, float]:
    n = len(values)
    if n == 0:
        return (float("nan"),) * 3
    m = sum(values) / n
    if n == 1:
        return (m, m, m)
    sd = math.sqrt(sum((v - m) ** 2 for v in values) / (n - 1))
    half = z * sd / math.sqrt(n)
    return (m, m - half, m + half)


def resolution_rate(conversations: list[dict]) -> dict[str, float]:
    """Three resolution numbers, from most flattering to most honest.

    - containment: never reached a person.
    - claimed: the vendor-style number (no handoff AND client did not object at the end).
    - true: contained AND no re-contact on the same topic within 7 days.
    """
    n = len(conversations) or 1
    contained = [c for c in conversations if not c["handed_off"]]
    return {
        "containment": len(contained) / n,
        "claimed": sum(1 for c in contained if not c.get("objected")) / n,
        "true": sum(1 for c in contained if not c["recontact_7d"]) / n,
    }


def mix_shift(rates_a: dict[str, float], mix_a: dict[str, float],
              rates_b: dict[str, float], mix_b: dict[str, float]) -> dict[str, float]:
    """Split a change in an overall rate into 'the mix of contacts changed' vs 'performance changed'.

    overall = sum(mix[k] * rate[k]). The split uses the average of both periods as the base,
    so the two parts add up exactly to the total change.
    """
    keys = set(mix_a) | set(mix_b)
    total = sum(mix_b.get(k, 0) * rates_b.get(k, 0) for k in keys) - sum(
        mix_a.get(k, 0) * rates_a.get(k, 0) for k in keys)
    mix_effect = sum((mix_b.get(k, 0) - mix_a.get(k, 0)) * (rates_a.get(k, 0) + rates_b.get(k, 0)) / 2 for k in keys)
    rate_effect = sum((rates_b.get(k, 0) - rates_a.get(k, 0)) * (mix_a.get(k, 0) + mix_b.get(k, 0)) / 2 for k in keys)
    return {"total": total, "mix_effect": mix_effect, "rate_effect": rate_effect}
