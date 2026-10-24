import json

import pytest
from fastapi.testclient import TestClient

from agentcommerce.pii import SafeError
from agentcommerce.server import create_app
from agentcommerce.x402 import Quote, pay_quote


@pytest.fixture
def client():
    with TestClient(create_app(0.0, 0.0)) as c:
        yield c


def events(c, rid):
    out, cur = [], None
    with c.stream("GET", f"/api/runs/{rid}/stream") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("event: "):
                cur = line[7:]
            elif line.startswith("data: "):
                out.append((cur, json.loads(line[6:])))
    return out


def start(c, sid, msg):
    r = c.post("/api/chat", json={"session_id": sid, "message": msg})
    assert r.status_code == 200
    return r.json()["run_id"]


def chat(c, sid, msg):
    return events(c, start(c, sid, msg))


def sid(c):
    return c.post("/api/session").json()["session_id"]


def test_security_headers(client):
    r = client.get("/")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_static_served(client):
    assert "EventSource" in client.get("/app.js").text and client.get("/app.css").status_code == 200


def test_ui_uses_textcontent_only(client):
    assert "innerHTML" not in client.get("/app.js").text.replace("no innerHTML", "")


def test_stream_tokens_tools_and_approval(client):
    kinds = [k for k, _ in chat(client, sid(client), "buy a headlamp")]
    assert kinds[0] == "tool_call" and "token" in kinds and "approval_required" in kinds and kinds[-1] == "done"


def test_decision_approve_and_reject(client):
    s = sid(client)
    chat(client, s, "buy a headlamp")
    rid = client.post("/api/decision", json={"session_id": s, "approve": True}).json()["run_id"]
    assert any(d.get("status") == "captured" for _, d in events(client, rid))
    chat(client, s, "buy a trail mix")
    rid = client.post("/api/decision", json={"session_id": s, "approve": False}).json()["run_id"]
    assert any(d.get("status") == "rejected_by_user" for _, d in events(client, rid))
    assert client.app.state.agents[s].s.service.captured_total() == 2900


def test_stream_twice_is_404(client):
    s = sid(client)
    rid = start(client, s, "hi")
    events(client, rid)
    assert client.get(f"/api/runs/{rid}/stream").status_code == 404


def test_cancel_before_stream_clears_unseen_approval(client):
    s = sid(client)
    rid = start(client, s, "buy a headlamp")
    assert client.post(f"/api/runs/{rid}/cancel").json() == {"cancelled": True}
    assert [k for k, _ in events(client, rid)] == ["cancelled"]
    assert client.app.state.agents[s].s.pending is None
    rid = client.post("/api/decision", json={"session_id": s, "approve": True}).json()["run_id"]
    assert client.app.state.agents[s].s.service.captured_total() == 0
    assert any("nothing" in d.get("text", "").lower() for _, d in events(client, rid))


def test_cancel_unknown_run(client):
    assert client.post("/api/runs/run_nope/cancel").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"session_id": "short", "message": "x"},
        {"session_id": "s" * 20, "message": ""},
        {"session_id": "s" * 20, "message": "x" * 2001},
        {"session_id": "s" * 20, "message": "hi", "extra": 1},
        {"message": "hi"},
    ],
)
def test_bad_chat_input(client, body):
    assert client.post("/api/chat", json=body).status_code == 422


def test_unknown_session(client):
    assert client.post("/api/chat", json={"session_id": "s" * 20, "message": "hi"}).status_code == 404


def test_paid_402_then_200_then_replay_refused(client):
    r = client.get("/api/paid/report")
    assert r.status_code == 402
    proof = pay_quote(client.app.state.user_key, Quote(**r.json()["quote"]), {"larkspur-books"}, 100)
    ok = client.get("/api/paid/report", headers={"X-Payment": json.dumps(proof)})
    assert ok.status_code == 200 and "contents" in ok.json()["report"]
    again = client.get("/api/paid/report", headers={"X-Payment": json.dumps(proof)})
    assert again.status_code == 402 and "already used" in again.json()["error"]


@pytest.mark.parametrize("hdr", ["not json", "[1,2]", "{}"])
def test_paid_malformed_proof(client, hdr):
    assert client.get("/api/paid/report", headers={"X-Payment": hdr}).status_code == 402


@pytest.mark.parametrize("kw", [{"first_token_delay": -0.1}, {"token_delay": -1}])
def test_negative_delays_rejected(kw):
    with pytest.raises(SafeError, match="negative"):
        create_app(**{"first_token_delay": 0.0, "token_delay": 0.0, **kw})


def test_negative_delay_env_rejected(monkeypatch):
    monkeypatch.setenv("TOKEN_DELAY_S", "-1")
    with pytest.raises(SafeError, match="negative"):
        create_app()


def test_card_in_chat_not_streamed_back(client, caplog):
    s = sid(client)
    body = json.dumps(chat(client, s, "my card 4242 4242 4242 4242 buy a headlamp"))
    assert "4242 4242" not in body and "4242 4242" not in caplog.text
