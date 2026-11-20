import json
import time
import uuid

from ouroboros_client import config, spool
from ouroboros_client.client import TelemetryClient


def make_client(url, token=lambda: "tok"):
    return TelemetryClient(url, scope="test", token_provider=token)


def record_ok(client, **over):
    kwargs = dict(
        skill_name="TestSkill",
        skill_version="1.0.0",
        status="success",
        session_id=str(uuid.uuid4()),
        feedback={"notes": "fine"},
    )
    kwargs.update(over)
    client.record(**kwargs)


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_record_returns_fast_while_server_hangs(server, ouro_home):
    server.state.delay = 5.0
    client = make_client(server.url)
    start = time.time()
    record_ok(client)
    elapsed = time.time() - start
    assert elapsed < 1.0
    assert len(spool.list_files()) == 1  # durably on disk regardless of network


def test_send_and_spool_cleanup_on_2xx(server, ouro_home):
    client = make_client(server.url)
    record_ok(client)
    assert wait_for(lambda: len(server.events()) == 1)
    assert wait_for(lambda: spool.list_files() == [])


def test_spool_on_failure_then_drain_on_recovery(server, ouro_home):
    client = make_client("http://127.0.0.1:1")  # dead port
    record_ok(client)
    record_ok(client)
    client.flush()
    assert len(spool.list_files()) == 2

    recovered = make_client(server.url)
    recovered.flush()
    assert spool.list_files() == []
    assert len(server.events()) == 2


def test_non_2xx_keeps_spool(server, ouro_home):
    server.state.status = 503
    client = make_client(server.url)
    record_ok(client)
    client.flush()
    assert len(spool.list_files()) == 1


def test_crash_resend_sends_same_event_id(server, ouro_home):
    client = make_client(server.url)
    record_ok(client)
    client.flush()
    assert len(server.events()) == 1
    sent = server.events()[0]

    # Simulate crash-after-POST-before-delete: resurrect the spool file
    spool.write_event(sent)
    client.flush()
    ids = [e["event_id"] for e in server.events()]
    assert ids == [sent["event_id"], sent["event_id"]]  # server-side dedup handles it
    assert spool.list_files() == []


def test_stale_events_batched_separately_and_dropped(server, ouro_home, monkeypatch):
    monkeypatch.setenv("OUROBOROS_FRESHNESS_WINDOW", "60")
    client = make_client(server.url)
    record_ok(client)  # fresh
    # Craft a stale spool file (filename timestamp far in the past)
    stale_event = {
        "event_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "client_ts": "2020-01-01T00:00:00+00:00",
        "skill_name": "Old",
        "skill_version": "1",
        "status": "success",
        "feedback": {"notes": "old"},
    }
    stale_path = config.spool_dir() / f"{0:015d}-{stale_event['event_id']}.json"
    stale_path.write_text(json.dumps(stale_event))

    def responder(body):
        # Server rejects any batch containing the stale event
        return 400 if any(e["skill_name"] == "Old" for e in body["events"]) else 202

    server.state.responder = responder
    client.flush()
    # Fresh accepted, stale dropped after its own 400 — spool fully clear
    assert spool.list_files() == []
    fresh_batches = [
        r for r in server.state.requests
        if all(e["skill_name"] != "Old" for e in r["body"]["events"])
    ]
    assert any(len(b["body"]["events"]) == 1 for b in fresh_batches)


def test_invalid_input_dropped_silently(server, ouro_home):
    client = make_client(server.url)
    record_ok(client, status="sorta")  # bad status
    record_ok(client, session_id="not-a-uuid")
    record_ok(client, feedback={})  # missing notes
    client.flush()
    assert spool.list_files() == []
    assert server.events() == []


def test_opt_out_writes_nothing(server, ouro_home, monkeypatch):
    monkeypatch.setenv("OUROBOROS_TELEMETRY", "off")
    client = make_client(server.url)
    record_ok(client)
    client.flush()
    assert not (ouro_home / "spool").exists()
    assert server.events() == []


def test_spool_cap_drops_oldest(server, ouro_home, monkeypatch):
    monkeypatch.setenv("OUROBOROS_SPOOL_CAP", "3")
    client = make_client("http://127.0.0.1:1")
    for _ in range(5):
        record_ok(client)
    assert len(spool.list_files()) == 3
