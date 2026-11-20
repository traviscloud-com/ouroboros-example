import uuid

from ouroboros_client import interactions, spool
from ouroboros_client.client import TelemetryClient

from .test_client import record_ok, wait_for


def seed_pair(session_id, prompt="please fix the query", response="Done — updated."):
    interactions.record_prompt(session_id, "p1", prompt)
    interactions.record_response(session_id, "p1", response)


def test_captured_pair_attaches_as_v11(server, ouro_home):
    sid = str(uuid.uuid4())
    seed_pair(sid)
    client = TelemetryClient(server.url, scope="t", token_provider=lambda: "tok")
    record_ok(client, session_id=sid, status="user_corrected")
    client.flush()
    assert wait_for(lambda: len(server.events()) == 1)
    (event,) = server.events()
    assert event["interaction"] == {
        "user_prompt": "please fix the query",
        "assistant_response": "Done — updated.",
    }
    assert event["prompt_id"] == "p1"  # join key for the server-side turn link
    batch = server.state.requests[0]["body"]
    assert batch["schema_version"] == "1.1"


def test_pair_consumed_only_once(server, ouro_home):
    sid = str(uuid.uuid4())
    seed_pair(sid)
    client = TelemetryClient(server.url, scope="t", token_provider=lambda: "tok")
    record_ok(client, session_id=sid)
    record_ok(client, session_id=sid)
    client.flush()
    events = server.events()
    assert len(events) == 2
    assert sum(1 for e in events if "interaction" in e) == 1


def test_no_capture_degrades_to_v10(server, ouro_home):
    client = TelemetryClient(server.url, scope="t", token_provider=lambda: "tok")
    record_ok(client)
    client.flush()
    (event,) = server.events()
    assert "interaction" not in event and "prompt_id" not in event
    assert server.state.requests[0]["body"]["schema_version"] == "1.0"


def test_incomplete_pair_not_attached(server, ouro_home):
    sid = str(uuid.uuid4())
    interactions.record_prompt(sid, "p1", "no answer yet")
    client = TelemetryClient(server.url, scope="t", token_provider=lambda: "tok")
    record_ok(client, session_id=sid)
    client.flush()
    (event,) = server.events()
    assert "interaction" not in event


def test_newest_complete_pair_wins(server, ouro_home):
    sid = str(uuid.uuid4())
    seed_pair(sid, "older prompt", "older answer")
    seed_pair(sid, "newer prompt", "newer answer")
    client = TelemetryClient(server.url, scope="t", token_provider=lambda: "tok")
    record_ok(client, session_id=sid)
    client.flush()
    (event,) = server.events()
    assert event["interaction"]["user_prompt"] == "newer prompt"


def test_opt_out_skips_attach_and_spool(server, ouro_home, monkeypatch):
    sid = str(uuid.uuid4())
    seed_pair(sid)
    monkeypatch.setenv("OUROBOROS_TELEMETRY", "off")
    client = TelemetryClient(server.url, scope="t", token_provider=lambda: "tok")
    record_ok(client, session_id=sid)
    client.flush()
    assert spool.list_files() == []
    assert server.events() == []
    # pair untouched (not consumed)
    monkeypatch.delenv("OUROBOROS_TELEMETRY")
    assert interactions.take_latest_complete(sid) is not None
