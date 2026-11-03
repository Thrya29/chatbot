import random
from datetime import timedelta

from handoff_lab.lab import Lab
from handoff_lab.mock_genesys import Agent
from handoff_lab.wait_backtest import run
from handoff_lab.wait_forecast import Forecast, OnlineCalibrator, QueueSnapshot, WaitForecaster, range_message

BUSY = [300.0] * 6          # 2 agents x 3 chats, each running for 5 minutes


def fc(**kw):
    snap = dict(slots_total=6, busy_ages=BUSY, ahead=3, higher_rate=0.0)
    snap.update(kw)
    return WaitForecaster(sims=400, seed=1).forecast(QueueSnapshot(**snap))


def test_free_slot_and_nobody_ahead_means_no_wait():
    assert fc(busy_ages=BUSY[:5], ahead=0).p90 == 0


def test_forecast_moves_the_right_way():
    base = fc()
    assert fc(ahead=8).p50 > base.p50                   # more people ahead -> longer
    assert fc(slots_total=12, busy_ages=BUSY * 2).p50 < base.p50   # more agents -> shorter
    assert fc(higher_rate=0.01).p90 > base.p90          # higher-priority chats jumping in -> longer tail
    assert base.p10 <= base.p50 <= base.p90


def test_same_seed_same_forecast():
    assert fc() == fc()


def test_handle_time_learning_tracks_the_data():
    f = WaitForecaster()
    rng = random.Random(3)
    for _ in range(2000):
        f.observe_handle(rng.lognormvariate(6.9, 0.5))   # mean ~ 1,124 s
    assert 1000 < f.handle_mean_s < 1250


def test_calibrator_widens_an_overconfident_range_to_about_ten_percent():
    rng = random.Random(5)
    cal = OnlineCalibrator()
    raw = Forecast(300, 600, 900, 0)
    over = []
    for i in range(4000):
        actual = rng.lognormvariate(6.4, 0.6)          # raw p90 (900 s) sits at about the 70th percentile
        if i >= 3000:
            over.append(actual > raw.p90 * cal.k_hi)
        cal.observe(raw, actual)
    assert cal.k_hi > 1.2
    assert 0.06 < sum(over) / len(over) < 0.14


def test_range_message_wording():
    assert range_message(Forecast(0, 0, 0, 0)).startswith("Someone from the team has your conversation now")
    assert range_message(Forecast(180, 300, 420, 1)) == "Someone from the team should reply in 3 to 7 minutes."
    busy = range_message(Forecast(600, 1200, 1900, 1))
    assert "busy" in busy and "10 to 35 minutes" in busy


def test_bridge_gives_a_range_when_priority_reorders_the_queue():
    lab = Lab()
    lab.staff(Agent("Solo", {"FundingPayments"}, {"en"}, capacity=1))
    first = lab.platform.open_thread("c1")
    lab.client_turn(first.id, "agent please")                          # takes the only slot
    p2 = lab.platform.open_thread("c2")
    lab.client_turn(p2.id, "This is a joke, I want a person")          # P2 waits
    p3 = lab.platform.open_thread("c3")
    lab.client_turn(p3.id, "agent please")                             # P3 behind a P2: reordered
    msg = lab.platform.threads[p3.id].messages[-1][1]
    assert "can't see the current wait" not in msg and "minutes" in msg


def test_bridge_learns_from_the_actual_wait_at_first_agent_reply():
    lab = Lab()
    solo = Agent("Solo", {"FundingPayments"}, {"en"}, capacity=1)
    lab.staff(solo)
    a = lab.platform.open_thread("c1"); lab.client_turn(a.id, "agent please")
    b = lab.platform.open_thread("c2"); lab.client_turn(b.id, "agent please")
    first, second = list(lab.genesys.conversations.values())
    lab.clock[0] += timedelta(minutes=12)
    lab.genesys.wrap_up(first.id, "Resolved")                          # frees the slot; c2 connects
    lab.genesys.agent_reply(second.id, "Hi, I'm here")
    assert lab.bridge.calibrator.seen == 1


def test_backtest_runs_and_beats_the_single_number():
    res = run(days=1, sims=40)["summary"]["all"]
    assert res["calibrated_broken"]["mean"] < res["single_broken"]["mean"]
    assert 0.5 < res["calibrated_coverage"]["mean"] < 0.95
