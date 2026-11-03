"""Circuit breaker for the bridge's calls to the contact centre (closed -> open -> half-open).

Adapted from the per-tool breaker in my ATLAS project (atlas/execution/circuit.py).

Why the bridge needs it: retry-with-backoff is per request. During an outage, every
escalation would wait through the whole backoff sequence before the client hears anything,
and every one of those calls adds load to a system that is already failing. With a breaker:

  * CLOSED     normal; consecutive failures are counted
  * OPEN       after `failure_threshold` consecutive failures: calls are not attempted at all,
               the payload is dead-lettered and the client is told straight away
  * HALF_OPEN  after `cooldown` seconds: one trial call is let through; success closes the
               breaker, failure re-opens it immediately

What counts as a failure matters: a 4xx is OUR mistake (bad request), and the service is up,
so it must not open the breaker. Only transport errors, timeouts and 5xx count.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    clock: Callable[[], float]          # injectable, so tests and simulations use logical time
    failure_threshold: int = 3
    cooldown: float = 30.0
    state_: State = State.CLOSED
    consecutive_failures: int = 0
    opened_at: float = 0.0
    times_opened: int = 0
    calls_short_circuited: int = 0
    _trial_in_flight: bool = field(default=False, repr=False)

    def state(self) -> State:
        if self.state_ is State.OPEN and self.clock() - self.opened_at >= self.cooldown:
            self.state_ = State.HALF_OPEN
            self._trial_in_flight = False
        return self.state_

    def allow(self) -> bool:
        s = self.state()
        if s is State.CLOSED:
            return True
        if s is State.HALF_OPEN and not self._trial_in_flight:
            self._trial_in_flight = True     # exactly one trial call while half-open
            return True
        self.calls_short_circuited += 1
        return False

    def record_success(self) -> None:
        self.state_, self.consecutive_failures, self._trial_in_flight = State.CLOSED, 0, False

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.state_ is State.HALF_OPEN or self.consecutive_failures >= self.failure_threshold:
            if self.state_ is not State.OPEN:
                self.times_opened += 1
            self.state_, self.opened_at, self._trial_in_flight = State.OPEN, self.clock(), False

    def snapshot(self) -> dict:
        return {"state": self.state().value, "consecutive_failures": self.consecutive_failures,
                "times_opened": self.times_opened, "calls_short_circuited": self.calls_short_circuited}
