"""Backtest the wait forecaster against a simulated contact-centre day it doesn't know.

    python -m handoff_lab.wait_backtest            # 10 days, writes reports/wait_forecast.{json,md}

The "world" is a discrete-event simulation of one chat queue, 07:00-22:00, calibrated loosely to
the data pack (median wait for those who wait ~9 min off-peak, ~20+ min after market open; ~17%
abandon). It is deliberately DIFFERENT from what the forecaster assumes:
  * handle times are log-normal PLUS a 6% tail of chats that take 3x longer (the forecaster fits
    a plain log-normal)
  * arrivals triple in the 30 minutes after market open (08:00). The forecaster isn't told; it only
    sees the trailing 30-minute arrival rate, so it lags the surge
  * abandonment depends on each client's patience, not a fixed probability
Each client who has to wait gets three possible answers at the moment of handoff:
  A. single number, ACD-style: (people ahead + 1) / slots x average handle time
  B1. forecast range P10-P90 using the trailing 30-minute arrival rate
  B2. forecast range P10-P90 using an arrival profile per 15-minute interval learned on previous
      days (what workforce-management forecasts provide). Day 1 has no history, so all scoring
      starts from day 2, for every method
  B3. B2 plus online calibration (OnlineCalibrator): bounds nudged after each observed wait so
      that ~10% fall above and ~10% below. Learns only from waits that have already happened
  C. "I can't see the current wait" (what it said before when priority reorders the queue)
and we record what actually happened. Clients who abandon have no observed wait and are excluded
from accuracy (their count is reported).
"""
from __future__ import annotations

import heapq
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

from .metrics import mean_ci
from .wait_forecast import Forecast, OnlineCalibrator, QueueSnapshot, WaitForecaster, _nice

ROOT = Path(__file__).resolve().parent.parent
OPEN, CLOSE, MARKET_OPEN = 7 * 3600, 22 * 3600, 8 * 3600
PRIORITY_MIX = [(1, 0.05), (2, 0.30), (3, 0.65)]
WINDOW = 1800


@dataclass
class World:
    agents: int = 7
    capacity: int = 3
    base_rate_per_h: float = 92.0
    surge_multiplier: float = 2.5
    handle_mean_s: float = 14 * 60
    handle_sigma: float = 0.55
    tail_share: float = 0.06
    tail_factor: float = 3.0
    patience_mean_s: float = 70 * 60

    def rate(self, t: float) -> float:
        r = self.base_rate_per_h
        if MARKET_OPEN <= t < MARKET_OPEN + 1800:
            r *= self.surge_multiplier
        elif t >= 18 * 3600:
            r *= 0.6
        return r / 3600

    def handle(self, rng: random.Random) -> float:
        mu = math.log(self.handle_mean_s) - self.handle_sigma ** 2 / 2
        h = rng.lognormvariate(mu, self.handle_sigma)
        return h * self.tail_factor if rng.random() < self.tail_share else h


class ArrivalProfile:
    """Arrivals per 15-minute interval and priority, averaged over previous days (EWMA)."""

    def __init__(self, alpha: float = 0.3):
        self.alpha, self.rates, self.days = alpha, {}, 0

    def learn_day(self, arrivals: list[tuple[float, int]]) -> None:
        counts: dict[tuple[int, int], int] = {}
        for t, p in arrivals:
            counts[(int(t // 900), p)] = counts.get((int(t // 900), p), 0) + 1
        keys = set(counts) | set(self.rates)
        for k in keys:
            today = counts.get(k, 0) / 900
            self.rates[k] = today if self.days == 0 else (1 - self.alpha) * self.rates.get(k, 0) + self.alpha * today
        self.days += 1

    def higher_rate_fn(self, now: float, priority: int) -> Callable[[float], float] | None:
        if not self.days:
            return None
        return lambda off: sum(self.rates.get((int((now + off) // 900), p), 0.0) for p in (1, 2, 3) if p < priority)


def simulate_day(seed: int, world: World | None = None, sims: int = 200,
                 profile: ArrivalProfile | None = None, calibrator: OnlineCalibrator | None = None) -> list[dict]:
    w = world or World()
    rng = random.Random(seed)
    fc = WaitForecaster(sims=sims, seed=seed)
    fc_prof = WaitForecaster(sims=sims, seed=seed + 10_000)
    slots = w.agents * w.capacity
    # client state
    arrivals: list[tuple[float, int]] = []           # (time, priority) for recent-rate windows
    waiting: dict[int, tuple[int, float]] = {}       # id -> (priority, arrival)
    busy: dict[int, float] = {}                      # id -> service start
    events: list[tuple[float, int, str, int]] = []   # (time, seq, kind, client)
    seq = 0
    records: dict[int, dict] = {}

    def push(t, kind, cid):
        nonlocal seq
        seq += 1
        heapq.heappush(events, (t, seq, kind, cid))

    # pre-generate arrivals (thinning for the time-varying rate)
    t, cid, peak = OPEN, 0, w.base_rate_per_h * w.surge_multiplier / 3600
    while True:
        t += rng.expovariate(peak)
        if t >= CLOSE:
            break
        if rng.random() < w.rate(t) / peak:
            cid += 1
            push(t, "arrive", cid)

    def start_service(now):
        while waiting and len(busy) < slots:
            nxt = min(waiting, key=lambda c: (waiting[c][0], waiting[c][1]))
            waiting.pop(nxt)
            busy[nxt] = now
            records[nxt]["actual_wait"] = now - records[nxt]["arrived"]
            if calibrator is not None and "raw" in records[nxt]:      # the wait is now known: learn from it
                calibrator.observe(records[nxt].pop("raw"), records[nxt]["actual_wait"])
            push(now + w.handle(rng), "done", nxt)

    while events:
        now, _, kind, c = heapq.heappop(events)
        if kind == "arrive":
            pr = rng.choices([p for p, _ in PRIORITY_MIX], [s for _, s in PRIORITY_MIX])[0]
            rec = {"id": c, "arrived": now, "priority": pr, "actual_wait": None, "abandoned": False,
                   "surge": MARKET_OPEN <= now < MARKET_OPEN + 1800}
            records[c] = rec
            ahead = sum(1 for p, a in waiting.values() if (p, a) < (pr, now))
            must_wait = len(busy) >= slots or ahead > 0
            if must_wait:
                recent = [p for (ta, p) in arrivals if ta >= now - WINDOW]
                higher = sum(1 for p in recent if p < pr) / min(WINDOW, max(60.0, now - OPEN))
                ages = [now - s for s in busy.values()]
                f = fc.forecast(QueueSnapshot(slots, ages, ahead, higher))
                rec.update(p10=f.p10, p50=f.p50, p90=f.p90,
                           single=(ahead + 1) / slots * fc.handle_mean_s,
                           reordered=any(p < pr for p, _ in waiting.values()))
                fn = profile.higher_rate_fn(now, pr) if profile else None
                if fn:
                    g = fc_prof.forecast(QueueSnapshot(slots, ages, ahead, higher, fn))
                    rec.update(q10=g.p10, q50=g.p50, q90=g.p90, raw=g)
                    if calibrator is not None:
                        cal = calibrator.apply(g)
                        rec.update(c10=cal.p10, c50=cal.p50, c90=cal.p90)
            arrivals.append((now, pr))
            waiting[c] = (pr, now)
            push(now + rng.expovariate(1 / w.patience_mean_s), "abandon", c)
            start_service(now)
        elif kind == "abandon":
            if c in waiting:
                waiting.pop(c)
                records[c]["abandoned"] = True
                fc.observe_outcome(True)
                fc_prof.observe_outcome(True)
        elif kind == "done":
            h = now - busy.pop(c)
            for f in (fc, fc_prof):
                f.observe_handle(h)
                f.observe_outcome(False)
            start_service(now)
    if profile is not None:
        profile.learn_day(arrivals)
    return list(records.values())


GRACE = 300   # "waited more than 5 minutes longer than we said": same rule for every method


def _told_hi(r: dict, method: str) -> float:
    if method == "single":
        return max(60, round(r["single"] / 60) * 60)            # "about X minutes"
    hi_key = {"trailing": "p90", "profile": "q90", "calibrated": "c90"}[method]
    return _nice(r[hi_key] / 60, up=True) * 60                   # top of the range we actually say


def score(records: list[dict]) -> dict:
    served = [r for r in records if "c90" in r and r["actual_wait"] is not None]

    def block(rs):
        if not rs:
            return None
        out = {"n": len(rs), "median_actual_min": sorted(r["actual_wait"] for r in rs)[len(rs) // 2] / 60}
        for m, (lo, mid, hi) in (("trailing", ("p10", "p50", "p90")), ("profile", ("q10", "q50", "q90")),
                                 ("calibrated", ("c10", "c50", "c90"))):
            out[f"{m}_coverage"] = sum(r[lo] <= r["actual_wait"] <= r[hi] for r in rs) / len(rs)
            out[f"{m}_above_p90"] = sum(r["actual_wait"] > r[hi] for r in rs) / len(rs)
            out[f"{m}_width_min"] = sum(r[hi] - r[lo] for r in rs) / len(rs) / 60
            out[f"{m}_mae_min"] = sum(abs(r["actual_wait"] - r[mid]) for r in rs) / len(rs) / 60
        for m in ("single", "trailing", "profile", "calibrated"):
            out[f"{m}_broken"] = sum(r["actual_wait"] > _told_hi(r, m) + GRACE for r in rs) / len(rs)
        out["single_mae_min"] = sum(abs(r["actual_wait"] - r["single"]) for r in rs) / len(rs) / 60
        return out

    return {"clients": len(records), "abandon_rate_all": sum(r["abandoned"] for r in records) / max(1, len(records)),
            "all": block(served), "surge": block([r for r in served if r["surge"]]),
            "outside_surge": block([r for r in served if not r["surge"]]),
            "reordered": block([r for r in served if r["reordered"]])}


METHODS = ("trailing", "profile", "calibrated")
KEYS = (["median_actual_min", "single_broken", "single_mae_min"]
        + [f"{m}_{k}" for m in METHODS for k in ("broken", "coverage", "above_p90", "width_min", "mae_min")])


def run(days: int = 9, sims: int = 100) -> dict:
    """Consecutive days in the same world; the interval profile learns from each finished day.
    Day 1 is warm-up only (no history yet) and is not scored for any method."""
    profile, calibrator = ArrivalProfile(), OnlineCalibrator()
    per_day = []
    for seed in range(1, days + 2):
        recs = simulate_day(seed, sims=sims, profile=profile, calibrator=calibrator)
        if seed > 1:
            per_day.append(score(recs))
    out = {"days_scored": days, "sims_per_forecast": sims, "per_day": per_day, "summary": {},
           "final_calibration": {"k_hi": calibrator.k_hi, "k_lo": calibrator.k_lo}}
    for segment in ("all", "outside_surge", "surge", "reordered"):
        seg = {}
        for k in KEYS:
            vals = [d[segment][k] for d in per_day if d[segment]]
            m, lo, hi = mean_ci(vals)
            seg[k] = {"mean": m, "ci": [lo, hi]}
        seg["n_total"] = sum(d[segment]["n"] for d in per_day if d[segment])
        out["summary"][segment] = seg
    out["summary"]["abandon_rate_all"] = mean_ci([d["abandon_rate_all"] for d in per_day])[0]
    return out


def markdown(res: dict) -> str:
    s = res["summary"]

    def pct(seg, k):
        v = s[seg][k]
        return f"{100 * v['mean']:.0f}% ({100 * max(0, v['ci'][0]):.0f}–{100 * min(1, v['ci'][1]):.0f})"

    def mins(seg, k):
        v = s[seg][k]
        return f"{v['mean']:.1f}"

    segs = (("all", "All clients who waited"), ("outside_surge", "Outside the surge"),
            ("surge", "08:00–08:30 market-open surge"), ("reordered", "Priority had reordered the queue"))
    t1 = "\n".join(f"| {name} | {s[k]['n_total']:,} | {mins(k, 'median_actual_min')} | {pct(k, 'single_broken')} | "
                   f"{pct(k, 'trailing_broken')} | {pct(k, 'profile_broken')} | {pct(k, 'calibrated_broken')} |"
                   for k, name in segs)
    t2 = "\n".join(f"| {name} | " + " | ".join(f"{pct(k, m + '_coverage')} / {pct(k, m + '_above_p90')} / "
                                               f"{mins(k, m + '_width_min')}" for m in METHODS) + " |"
                   for k, name in segs)
    return f"""# Wait-range forecast: backtest

Generated by `python -m handoff_lab.wait_backtest`. {res['days_scored']} scored days (after one warm-up day),
{res['sims_per_forecast']} Monte Carlo runs per forecast. Means across days, 95% CI across days in brackets.
**Simulated world, not real traffic.** It deliberately differs from the forecaster's assumptions (heavier handle-time
tail, a market-open surge the forecaster isn't told about, patience-based abandonment); see the module docstring.

## 1. How often a client waited more than 5 minutes longer than they were told

Same rule for every method: "about X minutes" for the single number, and the top of the range actually said
(rounded the way the message rounds it) for the forecasts.

| Segment | Served clients | Median wait (min) | Single number | Range, trailing rate | Range, interval profile | Range, profile + calibration |
|---|---|---|---|---|---|---|
{t1}

## 2. Does the range mean what it says?

The range should contain the actual wait about **80%** of the time, with about **10%** above it. Each cell:
held / above the top / average width in minutes.

| Segment | Trailing rate | Interval profile | Profile + calibration |
|---|---|---|---|
{t2}

Calibration multipliers at the end: upper × {res['final_calibration']['k_hi']:.2f}, lower ×
{res['final_calibration']['k_lo']:.2f}.

Overall abandonment in the simulated world: {100 * s['abandon_rate_all']:.0f}% of clients. Clients who abandoned have
no observed wait and are excluded, which flatters every method slightly.
"""


def main() -> None:
    res = run()
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    (out / "wait_forecast.json").write_text(json.dumps(res["summary"], indent=2))
    (out / "wait_forecast.md").write_text(markdown(res))
    print(markdown(res))


if __name__ == "__main__":
    main()
