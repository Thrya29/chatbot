from datetime import date

import jsonschema
import pytest

from handoff_lab.contract import Handoff, parse_header, render_note, validate
from handoff_lab.detection import detect
from handoff_lab.router import decide
from handoff_lab.withdrawal import Withdrawal, add_working_days, assess

TODAY = date(2026, 10, 28)   # a Wednesday


# ---------------------------------------------------------------- contract
def _handoff(**kw):
    base = dict(queue="FundingPayments", priority="P2", flags=["repeat_contact", "possible_complaint"],
                topic="withdrawal_not_received", conversation_id="conv-1", summary="s", client_wants="w")
    base.update(kw)
    return Handoff(**base)


def test_contract_payload_is_valid_and_flags_are_sorted():
    p = _handoff().to_payload()
    validate(p)
    assert p["attributes"]["hl_flags"] == "possible_complaint,repeat_contact"
    assert p["attributes"]["hl_contract"] == "v1"


def test_contract_rejects_unknown_queue():
    with pytest.raises(jsonschema.ValidationError):
        _handoff(queue="Marketing").to_payload()


def test_header_round_trip_supports_free_text_routing():
    text = render_note(_handoff().to_payload())
    hdr = parse_header(text)
    assert hdr["queue"] == "FundingPayments" and hdr["priority"] == "P2"
    assert "repeat_contact" in hdr["flags"]
    assert parse_header("hello, I need help") is None


def test_note_has_fixed_order_sections():
    text = render_note(_handoff(do_not_ask_again=["amount"]).to_payload())
    order = ["SUMMARY", "CLIENT WANTS", "AI ALREADY TOLD THEM", "DATA PULLED", "SIGNALS", "CLIENT WAS TOLD",
             "DO NOT ASK AGAIN", "SUGGESTED NEXT STEP"]
    positions = [text.index(s + ":") for s in order]
    assert positions == sorted(positions)


# --------------------------------------------------------------- detection
@pytest.mark.parametrize("text,queue,priority", [
    ("You closed my position at the worst possible price. This is theft. I'm going to the regulator.",
     "Complaints", "P2"),
    ("I've lost nearly everything this month. I can't stop trading.", "PriorityVulnerable", "P1"),
    ("I lost my phone and can't get into my authenticator. The market opens in 20 minutes.", "AccountKYC", "P1"),
    ("There's a withdrawal on my account I didn't make.", "Security", "P1"),
    ("There's a withdrawal on my account I did not make.", "Security", "P1"),
    ("Honestly I don't want to live anymore after these losses", "PriorityVulnerable", "P1"),
])
def test_red_tier_routes(text, queue, priority):
    dec = decide(detect(text))
    assert (dec.tier, dec.action, dec.queue, dec.priority) == ("red", "handoff", queue, priority)


def test_self_harm_gets_crisis_resources():
    dec = decide(detect("I just want to end it all"))
    assert "116 123" in dec.client_message


def test_vulnerability_outranks_complaint():
    dec = decide(detect("This is unacceptable, I need this money for rent and I can't cope"))
    assert dec.queue == "PriorityVulnerable" and "possible_complaint" in dec.flags


def test_repeat_and_request_for_person_is_p2_funding():
    det = detect("You keep telling me the same thing. This is useless. Get me a person.",
                 ["Where is my withdrawal?", "My withdrawal still hasn't arrived"])
    dec = decide(det)
    assert {"repeat_contact", "asked_for_human", "possible_complaint"} <= dec.flags
    assert (dec.queue, dec.priority) == ("FundingPayments", "P2")


def test_human_before_any_problem_is_honoured_immediately():
    dec = decide(detect("agent"))
    assert dec.action == "handoff" and dec.priority == "P3"


def test_irritation_alone_never_routes():
    dec = decide(detect("Great, thanks a lot!! That worked"))
    assert dec.action == "answer" and "frustrated" in dec.flags


def test_advice_request_is_declined_not_answered():
    dec = decide(detect("Should I close my position before the results?"))
    assert dec.action == "answer" and "can't advise" in dec.client_message


def test_how_to_question_is_green():
    dec = decide(detect("How do I withdraw funds to a different bank account?"))
    assert dec.tier == "green"


# -------------------------------------------------------------- withdrawal
def test_working_days_skip_weekends_and_holidays():
    assert add_working_days(date(2026, 10, 23), 1) == date(2026, 10, 26)      # Fri -> Mon
    assert add_working_days(date(2026, 12, 24), 1) == date(2026, 12, 29)      # skips 25 and 28 Dec


def _w(**kw):
    base = dict(id="WD-1", amount="2500.00", currency="GBP", requested_on=date(2026, 10, 22),
                method="FASTER_PAYMENTS", account_last4="4417", status="SENT", sent_on=date(2026, 10, 23),
                bank_reference="HLW-88213")
    base.update(kw)
    return Withdrawal(**base)


def test_late_withdrawal_hands_off_with_facts():
    a = assess(_w(), TODAY)
    assert a.outcome == "handoff" and a.category == "sent_late" and a.due_by == date(2026, 10, 26)
    assert "£2,500.00" in a.reply and "HLW-88213" in a.reply


def test_in_window_withdrawal_resolves_with_reference():
    a = assess(_w(sent_on=date(2026, 10, 28)), TODAY)
    assert a.outcome == "resolve" and "HLW-88213" in a.reply


def test_under_review_never_gives_a_reason():
    a = assess(_w(status="UNDER_REVIEW"), TODAY)
    assert a.outcome == "handoff" and "restricted" in a.extra_flags
    for word in ("because", "compliance", "aml", "suspicious", "fraud", "investigat"):
        assert word not in a.reply.lower()


def test_unknown_method_never_guesses():
    a = assess(_w(method="CARRIER_PIGEON"), TODAY)
    assert a.outcome == "handoff" and "data_unavailable" in a.extra_flags
