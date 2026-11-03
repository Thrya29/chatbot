import json
import urllib.request
from datetime import timedelta

from handoff_lab.circuit import CircuitBreaker, State
from handoff_lab.lab import Lab
from handoff_lab.mock_genesys import Agent


# ------------------------------------------------------------- the breaker itself
def test_opens_after_threshold_and_half_opens_after_cooldown():
    now = [0.0]
    b = CircuitBreaker(clock=lambda: now[0], failure_threshold=3, cooldown=30)
    for _ in range(2):
        b.record_failure()
    assert b.state() is State.CLOSED
    b.record_failure()
    assert b.state() is State.OPEN and not b.allow()
    now[0] = 30
    assert b.state() is State.HALF_OPEN
    assert b.allow() and not b.allow()          # exactly one trial while half-open


def test_half_open_failure_reopens_and_success_closes():
    now = [0.0]
    b = CircuitBreaker(clock=lambda: now[0], failure_threshold=1, cooldown=10)
    b.record_failure()
    now[0] = 10
    assert b.allow()
    b.record_failure()
    assert b.state() is State.OPEN and b.times_opened == 2
    now[0] = 20
    assert b.allow()
    b.record_success()
    assert b.state() is State.CLOSED and b.consecutive_failures == 0


# ------------------------------------------------------------- inside the bridge
def _lab():
    lab = Lab(genesys_down=True)
    lab.staff(Agent("Priya", {"FundingPayments", "PriorityVulnerable"}, {"en"}))
    lab.sleeps = []
    lab.bridge.sleep = lab.sleeps.append
    return lab


def _ask(lab, client, text="agent please"):
    t = lab.platform.open_thread(client)
    lab.client_turn(t.id, text)
    return lab.platform.threads[t.id]


def test_outage_is_detected_once_then_later_clients_are_told_immediately():
    lab = _lab()
    first = _ask(lab, "c1")
    assert lab.genesys_attempts == 3 and len(lab.sleeps) == 2      # first client pays for the retries
    assert lab.bridge.breaker.state() is State.OPEN

    second, third = _ask(lab, "c2"), _ask(lab, "c3")
    assert lab.genesys_attempts == 3 and len(lab.sleeps) == 2      # no calls, no waiting for the rest
    for t in (first, second, third):
        assert "couldn't pass this to our team" in t.messages[-1][1]
    reasons = [r[2] for r in lab.store.dead_letters()]
    assert reasons.count("circuit open: genesys not called") == 2   # nothing lost: all dead-lettered


def test_recovers_after_cooldown_when_genesys_is_back():
    lab = _lab()
    _ask(lab, "c1")
    lab.genesys_down = False
    _ask(lab, "c2")                                                 # still inside the cooldown
    assert not lab.genesys.conversations
    lab.clock[0] += timedelta(seconds=30)
    t = _ask(lab, "c3")                                             # the half-open trial succeeds
    assert lab.bridge.breaker.state() is State.CLOSED
    assert len(lab.genesys.conversations) == 1 and "couldn't pass" not in t.messages[-1][1]


def test_bad_requests_do_not_open_the_breaker():
    lab = Lab()
    for i in range(5):
        lab.bridge._post_with_retry({"customAttributes": {}, "text": "x",
                                     "channel": {"from": {"id": f"a{i}"}, "messageId": f"m{i}"}}, "test")
    assert lab.bridge.breaker.state() is State.CLOSED


def test_client_at_risk_still_gets_crisis_line_during_an_outage():
    lab = _lab()
    t = _ask(lab, "c9", "I just want to end it all")
    assert "116 123" in t.messages[-1][1]


def test_breaker_state_is_visible_on_metrics():
    from handoff_lab.server import make_server
    import threading
    lab = _lab()
    _ask(lab, "c1")
    srv = make_server(lab, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/metrics") as r:
            m = json.load(r)
        assert m["genesys_breaker"]["state"] == "open" and m["genesys_breaker"]["times_opened"] == 1
    finally:
        srv.shutdown()
