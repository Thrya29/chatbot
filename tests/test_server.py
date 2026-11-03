import json
import threading
import urllib.error
import urllib.request

import pytest

from handoff_lab.lab import ASSISTANT_SECRET, GENESYS_SECRET, Lab
from handoff_lab.mock_genesys import Agent
from handoff_lab.server import make_server
from handoff_lab.signatures import sign


@pytest.fixture
def running():
    lab = Lab()
    lab.staff(Agent("Priya", {"FundingPayments"}, {"en"}))
    srv = make_server(lab, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield lab, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _post(url, body, header, secret):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={header: sign(data, secret), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_health(running):
    _, base = running
    with urllib.request.urlopen(base + "/healthz") as r:
        assert json.load(r) == {"ok": True}


def test_escalation_over_http_reaches_genesys(running):
    lab, base = running
    t = lab.platform.open_thread("client-2")
    res = lab.assistant.handle(t.id, "client-2", "get me a person")
    event = {"id": "evt-http-1", "type": "conversation.escalated", "conversation_id": t.id,
             "client_id": "client-2", "handoff": res.handoff, "transcript": ["client: get me a person"]}
    code, out = _post(base + "/webhooks/assistant", event, "X-Signature-256", ASSISTANT_SECRET)
    assert code == 200 and len(lab.genesys.conversations) == 1
    code, out = _post(base + "/webhooks/assistant", event, "X-Signature-256", ASSISTANT_SECRET)
    assert out["status"] == "duplicate ignored"


def test_wrong_secret_is_401_over_http(running):
    _, base = running
    code, _ = _post(base + "/webhooks/genesys", {"id": "x"}, "X-Hub-Signature-256", "wrong")
    assert code == 401
    with urllib.request.urlopen(base + "/metrics") as r:
        assert json.load(r)["/webhooks/genesys:401"] == 1
