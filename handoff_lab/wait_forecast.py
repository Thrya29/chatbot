"""Wait-range forecasting for the honest wait message.

The problem: a single estimated-wait number ("about 6 minutes") is unreliable exactly when it
matters. Priority lets later P1/P2 chats jump ahead, handle times have a long tail, and people
ahead abandon. So the mock Genesys (like many ACD estimates) returns no estimate when priority
reorders the queue, and the client hears "I can't see the current wait".

The method is borrowed from my Adaptive Parallelism scheduler project: predict a DISTRIBUTION
by Monte Carlo simulation, recalibrate online from real measurements, and then check whether
the predicted ranges actually held (coverage), instead of trusting a point estimate.

Per forecast, `sims` runs of a small queue simulation:
  * every busy slot frees after a handle time drawn from the learned distribution, conditioned
    on how long that chat has already been running (residual life, not a fresh draw)
  * higher-priority chats arrive by a Poisson process at the recently observed rate and jump ahead
  * each person ahead, and each jumper, may abandon once before reaching the front (learned rate)
  * our client is served when a slot frees and nobody is ahead
The P10-P90 spread is the range the client is told; the P90 is the "promise".

Inputs are things a contact centre exposes: queue observations (waiting, interacting and how
long), recent offered counts by priority, and completed handle times. See docs/design.md.
"""
from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class QueueSnapshot:
    slots_total: int                    # agents on queue x chat capacity
    busy_ages: list[float]              # seconds each in-progress chat has been running
    ahead: int                          # waiting chats that rank ahead of this one
    higher_rate: float                  # per second: arrivals with higher priority (recent window)
    # Optional: expected higher-priority arrival rate at `offset` seconds from now, e.g. from an
    # interval forecast learned on previous days. When given, it replaces the flat recent rate,
    # so a market-open surge is anticipated and its end is too.
    higher_rate_at: Callable[[float], float] | None = None


@dataclass
class Forecast:
    p10: float
    p50: float
    p90: float
    sims: int

    def minutes(self) -> tuple[int, int, int]:
        return tuple(max(0, round(x / 60)) for x in (self.p10, self.p50, self.p90))


@dataclass
class WaitForecaster:
    prior_mean_s: float = 14 * 60       # data pack: average handle time 14 minutes
    prior_sigma: float = 0.6            # log-space spread before any data
    half_life: float = 200.0            # completed chats; older observations fade
    abandon_prior: float = 0.17         # data pack: 17% abandon while waiting
    sims: int = 300
    seed: int = 0
    _w: float = field(default=0.0, init=False)
    _m: float = field(default=0.0, init=False)
    _s: float = field(default=0.0, init=False)
    _abandon: float = field(default=0.0, init=False)
    _abandon_n: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        # Start from the prior as if we had seen 20 chats, so a few odd early chats can't swing it.
        mu = math.log(self.prior_mean_s) - self.prior_sigma ** 2 / 2
        self._w, self._m, self._s = 20.0, mu, self.prior_sigma ** 2
        self._abandon, self._abandon_n = self.abandon_prior, 20.0
        self._rng = random.Random(self.seed)

    # ------------------------------------------------------------ online learning
    def observe_handle(self, seconds: float) -> None:
        """A chat finished: update the log-normal handle-time estimate (exponentially weighted)."""
        decay = 0.5 ** (1 / self.half_life)
        x = math.log(max(seconds, 1.0))
        self._w = self._w * decay + 1
        delta = x - self._m
        self._m += delta / self._w
        self._s = (self._s * (self._w - 1) + delta * (x - self._m)) / self._w

    def observe_outcome(self, abandoned: bool) -> None:
        decay = 0.5 ** (1 / self.half_life)
        self._abandon_n = self._abandon_n * decay + 1
        self._abandon += (float(abandoned) - self._abandon) / self._abandon_n

    @property
    def handle_mean_s(self) -> float:
        return math.exp(self._m + self._s / 2)

    # ------------------------------------------------------------ simulation
    def _handle(self, rng: random.Random) -> float:
        return rng.lognormvariate(self._m, math.sqrt(max(self._s, 1e-4)))

    def _residual(self, rng: random.Random, age: float) -> float:
        for _ in range(30):                     # rejection sampling: T given T > age
            t = self._handle(rng)
            if t > age:
                return t - age
        return self._handle(rng) * 0.5          # very old chat: assume it is near its end

    def forecast(self, snap: QueueSnapshot) -> Forecast:
        rng = random.Random(self._rng.random())
        free_now = snap.slots_total - len(snap.busy_ages)
        if free_now > 0 and snap.ahead == 0:
            return Forecast(0, 0, 0, 0)
        waits = []
        for _ in range(self.sims):
            heap = [0.0] * max(0, free_now) + [self._residual(rng, a) for a in snap.busy_ages]
            if not heap:
                heap = [self._handle(rng)]
            heapq.heapify(heap)
            # Each person ahead gets ONE chance to give up before being served (not one per event).
            ahead, last = sum(rng.random() >= self._abandon for _ in range(snap.ahead)), 0.0
            while True:
                t = heapq.heappop(heap)
                rate = snap.higher_rate_at((last + t) / 2) if snap.higher_rate_at else snap.higher_rate
                if rate > 0:                    # higher-priority arrivals since the last event jump ahead
                    jumpers = _poisson(rng, rate * (t - last))
                    ahead += sum(rng.random() >= self._abandon for _ in range(jumpers))
                last = t
                if ahead == 0:
                    waits.append(t)
                    break
                ahead -= 1
                heapq.heappush(heap, t + self._handle(rng))
                if t > 6 * 3600:                # safety stop: far beyond any useful promise
                    waits.append(t)
                    break
        waits.sort()
        q = lambda p: waits[min(len(waits) - 1, int(p * len(waits)))]   # noqa: E731
        return Forecast(q(0.10), q(0.50), q(0.90), self.sims)


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 30:                                 # normal approximation for large means
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def _nice(minutes: float, up: bool) -> int:
    """Round to numbers people say: whole minutes under 10, then fives."""
    if minutes < 10:
        return max(1, math.ceil(minutes) if up else math.floor(minutes) or 1)
    return int(5 * (math.ceil(minutes / 5) if up else math.floor(minutes / 5)))


def range_message(fc: Forecast) -> str:
    lo, hi = _nice(fc.p10 / 60, up=False), _nice(fc.p90 / 60, up=True)
    if fc.p90 == 0:
        return "Someone from the team has your conversation now and will reply shortly."
    if hi <= 2:
        return "Someone from the team should reply in the next couple of minutes."
    span = f"{lo} to {hi} minutes" if lo < hi else f"about {hi} minutes"
    if hi <= 20:
        return f"Someone from the team should reply in {span}."
    return (f"The team is busy right now. Someone should reply in {span}. You don't need to keep this open: "
            "the reply will appear here and we'll email you.")


@dataclass
class OnlineCalibrator:
    """Keeps the told range honest when the world drifts from the model's assumptions.

    The Monte Carlo range is only as good as its assumptions; heavy handle-time tails and surges
    make it overconfident. After each client's actual wait is known, the bounds are nudged
    (multiplicatively) so that over time ~10% of waits exceed the top and ~10% fall below the bottom.
    This is adaptive conformal-style calibration; it only ever uses waits that have already happened.
    """
    target_above: float = 0.10
    target_below: float = 0.10
    eta: float = 0.03
    k_hi: float = 1.0
    k_lo: float = 1.0
    seen: int = 0

    def apply(self, fc: Forecast) -> Forecast:
        return Forecast(fc.p10 * self.k_lo, fc.p50, max(fc.p90 * self.k_hi, fc.p50), fc.sims)

    def observe(self, fc: Forecast, actual: float) -> None:
        """`fc` is the RAW forecast that was made for this client; `actual` their observed wait."""
        self.seen += 1
        above = actual > fc.p90 * self.k_hi
        below = actual < fc.p10 * self.k_lo
        self.k_hi = min(5.0, max(0.5, self.k_hi * math.exp(self.eta * (above - self.target_above))))
        self.k_lo = min(2.0, max(0.1, self.k_lo * math.exp(-self.eta * (below - self.target_below))))
