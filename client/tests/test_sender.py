"""Hook-event batch path of the background sender (design D6, client-sdk spec):
batches of <=200 to POST /v1/telemetry/hook-events, status handling, the time
budget, the 5,000 cap, and the nudges from `report` and the out-of-process
`python3 -m ouroboros_client.sender`."""

import json
import time
import uuid
from pathlib import Path

from ouroboros_client import __version__, config, hook, sender, spool
from ouroboros_client.client import TelemetryClient
from ouroboros_client.report import main as report_main

from .test_client import record_ok

HOOK_PATH = "/v1/telemetry/hook-events"
EVENTS_PATH = "/v1/telemetry/events"


def hook_record(name="PostToolUse", **payload) -> dict:
    return hook.build_record({"hook_event_name": name, "session_id": str(uuid.uuid4()),
                              "cwd": "/w", "tool_name": "Bash", **payload})


def spool_hooks(n: int) -> list[Path]:
    return [spool.write_hook_event(hook_record()) for _ in range(n)]


def make_client(url, token=lambda: "tok"):
    return TelemetryClient(url, scope="test", token_provider=token)


def test_hook_batch_shape_and_cleanup(server, ouro_home):
    spool_hooks(3)
    make_client(server.url).flush()
    reqs = server.requests_to(HOOK_PATH)
    assert len(reqs) == 1
    body = reqs[0]["body"]
    assert body["client"] == {"name": "ouroboros-plugin", "version": __version__}
    assert len(body["events"]) == 3
    for e in body["events"]:
        assert e["client_version"] == __version__
        assert set(e) >= {"hook_event_id", "session_id", "hook_event_name", "client_ts", "payload"}
    assert reqs[0]["headers"]["Authorization"] == "Bearer tok"
    assert spool.list_hook_files() == []
    # the outcome endpoint was not called for an empty outcome spool
    assert server.requests_to(EVENTS_PATH) == []


def test_batches_of_at_most_200_oldest_first(server, ouro_home):
    files = spool_hooks(450)
    ids_in_order = [json.loads(f.read_text())["hook_event_id"] for f in files]
    make_client(server.url).flush()
    sizes = [len(r["body"]["events"]) for r in server.requests_to(HOOK_PATH)]
    assert sizes == [200, 200, 50]
    sent = [e["hook_event_id"] for e in server.hook_events()]
    assert sent == ids_in_order
    assert spool.list_hook_files() == []


def test_5xx_keeps_spool_and_stops(server, ouro_home):
    spool_hooks(250)
    server.state.status = 503
    make_client(server.url).flush()
    assert len(server.requests_to(HOOK_PATH)) == 1  # stopped after the first failure
    assert len(spool.list_hook_files()) == 250


def test_401_keeps_spool(server, ouro_home):
    spool_hooks(5)
    server.state.status = 401
    make_client(server.url).flush()
    assert len(spool.list_hook_files()) == 5


def test_422_drops_batch_and_logs_locally(server, ouro_home):
    spool_hooks(5)
    server.state.status = 422
    make_client(server.url).flush()
    assert spool.list_hook_files() == []
    log = (ouro_home / "sender.log").read_text()
    assert "rejected 422" in log and "batch of 5" in log


def test_network_failure_keeps_spool(ouro_home):
    spool_hooks(3)
    make_client("http://127.0.0.1:1").flush()
    assert len(spool.list_hook_files()) == 3


def test_no_token_means_silent_spool(server, ouro_home):
    spool_hooks(2)
    make_client(server.url, token=lambda: None).flush()
    assert len(spool.list_hook_files()) == 2
    assert server.state.requests == []


def test_no_stale_drop_for_hook_events(server, ouro_home, monkeypatch):
    monkeypatch.setenv("OUROBOROS_FRESHNESS_WINDOW", "1")
    rec = hook_record()
    rec["client_ts"] = "2020-01-01T00:00:00Z"
    old = config.hook_spool_dir()
    old.mkdir(parents=True, exist_ok=True)
    (old / f"{0:015d}-{rec['hook_event_id']}.json").write_text(json.dumps(rec))
    make_client(server.url).flush()
    (sent,) = server.hook_events()
    assert sent["client_ts"] == "2020-01-01T00:00:00Z"  # uploaded with its original time


def test_drain_respects_time_budget(server, ouro_home):
    spool_hooks(600)
    server.state.delay = 0.3
    s = sender.Sender(server.url, lambda: "tok")
    start = time.monotonic()
    s._drain_hooks("tok", budget=0.5)
    elapsed = time.monotonic() - start
    assert elapsed < 1.5
    # at most two POSTs fit in the budget; the rest stays for the next nudge
    assert 1 <= len(server.requests_to(HOOK_PATH)) <= 2
    assert len(spool.list_hook_files()) >= 200


def test_hook_spool_cap_5000_default_and_override(ouro_home, monkeypatch):
    assert config.hook_spool_cap() == 5000
    monkeypatch.setenv("OUROBOROS_HOOK_SPOOL_CAP", "10")
    files = spool_hooks(25)
    remaining = spool.list_hook_files()
    assert len(remaining) == 10
    assert remaining == files[-10:]


def test_both_spools_drained_by_one_flush(server, ouro_home):
    spool_hooks(2)
    client = make_client(server.url)
    record_ok(client)
    client.flush()
    assert len(server.hook_events()) == 2
    assert len(server.requests_to(EVENTS_PATH)) >= 1
    assert spool.list_files() == [] and spool.list_hook_files() == []


def test_outcome_events_carry_client_version(server, ouro_home):
    client = make_client(server.url)
    record_ok(client)
    client.flush()
    (event,) = server.events()
    assert event["client_version"] == __version__


def test_report_cli_nudges_hook_spool(server, ouro_home, monkeypatch, capsys):
    spool_hooks(3)
    monkeypatch.setenv("OUROBOROS_API_URL", server.url)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", str(uuid.uuid4()))
    monkeypatch.setattr("ouroboros_client.client.default_token_provider", lambda scope=None: (lambda: "tok"))
    rc = report_main(["--skill", "S", "--version", "1", "--status", "success", "--notes", "n", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hook_spooled_before"] == 3 and out["hook_spooled_after"] == 0
    assert len(server.hook_events()) == 3


def test_sender_main_drains_out_of_process(server, ouro_home, monkeypatch):
    spool_hooks(4)
    monkeypatch.setenv("OUROBOROS_API_URL", server.url)
    monkeypatch.setattr("ouroboros_client.client.default_token_provider", lambda scope=None: (lambda: "tok"))
    assert sender.main([]) == 0
    assert len(server.hook_events()) == 4
    assert spool.list_hook_files() == []


def test_sender_main_inert_without_url_or_when_off(server, ouro_home, monkeypatch):
    spool_hooks(1)
    monkeypatch.delenv("OUROBOROS_API_URL", raising=False)
    assert sender.main([]) == 0
    monkeypatch.setenv("OUROBOROS_API_URL", server.url)
    monkeypatch.setenv("OUROBOROS_TELEMETRY", "off")
    assert sender.main([]) == 0
    assert server.state.requests == []
    assert len(spool.list_hook_files()) == 1


def test_sender_main_skips_when_locked(server, ouro_home, monkeypatch):
    spool_hooks(1)
    monkeypatch.setenv("OUROBOROS_API_URL", server.url)
    monkeypatch.setattr("ouroboros_client.client.default_token_provider", lambda scope=None: (lambda: "tok"))
    held = sender._acquire_lock()
    assert held is not None
    assert sender.main([]) == 0
    assert server.state.requests == []  # the other process owns the drain
    held.close()
    assert sender.main([]) == 0
    assert len(server.hook_events()) == 1


def test_sender_module_runs_as_script(server, ouro_home, monkeypatch, tmp_path):
    """The exact command the hook spawns, under a dev key dir."""
    import subprocess
    import sys

    import pytest

    pytest.importorskip("jwt")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))
    try:
        from scripts.gen_jwks import generate
    finally:
        sys.path.pop(0)
    generate(tmp_path / "keys")
    spool_hooks(2)
    env = {"PATH": "/usr/bin:/bin", "OUROBOROS_HOME": str(ouro_home), "OUROBOROS_API_URL": server.url,
           "OUROBOROS_DEV_KEY_DIR": str(tmp_path / "keys")}
    result = subprocess.run([sys.executable, "-m", "ouroboros_client.sender"], env=env, capture_output=True, timeout=30)
    assert result.returncode == 0 and result.stdout == b"" and result.stderr == b""
    assert len(server.hook_events()) == 2
    assert server.requests_to(HOOK_PATH)[0]["headers"]["Authorization"].startswith("Bearer ey")
