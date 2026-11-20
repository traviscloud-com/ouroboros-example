"""POST /v1/telemetry/hook-events (tasks 2.3 / 2.9): raw store, order-tolerant
projection into the session model, duplicates, stale rejection, auth."""

import copy
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app

from .conftest import make_settings

FIXTURES = Path(__file__).parent / "fixtures" / "hooks"
URL = "/v1/telemetry/hook-events"


def _rebase(batch: dict, newest_age_seconds: int = 60) -> dict:
    """Shift every client_ts so the newest one is `newest_age_seconds` ago."""
    batch = copy.deepcopy(batch)
    stamps = [datetime.fromisoformat(e["client_ts"].replace("Z", "+00:00")) for e in batch["events"]]
    shift = datetime.now(timezone.utc) - timedelta(seconds=newest_age_seconds) - max(stamps)
    for e, ts in zip(batch["events"], stamps):
        e["client_ts"] = (ts + shift).isoformat()
    return batch


def _fresh_ids(batch: dict) -> dict:
    """New session, hook_event, prompt, agent and tool_use ids so tests sharing
    one database stay independent (tool_use_id and agent_id are global keys)."""
    sid = str(uuid.uuid4())
    suffix = sid[:8]
    text_ = json.dumps(batch).replace("agent-0123456789abcde", f"agent-{suffix}").replace("toolu_fake_", f"toolu_{suffix}_")
    batch = json.loads(text_)
    prompts: dict[str, str] = {}
    for e in batch["events"]:
        e["hook_event_id"] = str(uuid.uuid4())
        e["session_id"] = sid
        if e.get("prompt_id"):
            e["prompt_id"] = prompts.setdefault(e["prompt_id"], str(uuid.uuid4()))
    return batch


@pytest.fixture()
def lifecycle() -> dict:
    return _fresh_ids(_rebase(json.loads((FIXTURES / "session_lifecycle.json").read_text())))


def post(client, token, body):
    return client.post(URL, json=body, headers={"Authorization": f"Bearer {token}"})


def rows(engine, table, session_id, order="1"):
    with engine.connect() as conn:
        return conn.execute(
            text(f"SELECT * FROM {table} WHERE session_id = :s ORDER BY {order}"), {"s": session_id}
        ).mappings().all()


def one(engine, table, session_id):
    found = rows(engine, table, session_id)
    assert len(found) == 1, f"{table}: expected 1 row, found {len(found)}"
    return found[0]


# ------------------------------------------------------------------- envelope


def test_requires_bearer(client, lifecycle):
    assert client.post(URL, json=lifecycle).status_code == 401


def test_malformed_envelope_is_422(client, token):
    assert post(client, token, {"events": []}).status_code == 422
    assert post(client, token, {"nope": 1}).status_code == 422
    too_many = {"events": [{"x": 1}] * 201}
    assert post(client, token, too_many).status_code == 422


def test_malformed_event_is_rejected_not_fatal(client, token, engine, lifecycle):
    good = lifecycle["events"][0]
    bad_no_session = {"hook_event_id": str(uuid.uuid4()), "hook_event_name": "Stop", "client_ts": good["client_ts"]}
    bad_session = dict(good, hook_event_id=str(uuid.uuid4()), session_id="not-a-uuid")
    naive_ts = dict(good, hook_event_id=str(uuid.uuid4()), client_ts="2026-01-01T00:00:00")
    resp = post(client, token, {"events": [good, bad_no_session, bad_session, naive_ts, "garbage"]})
    assert resp.status_code == 202
    body = resp.json()
    assert body["accepted"] == 1 and body["duplicates"] == 0
    assert [r["hook_event_id"] for r in body["rejected"]] == [
        bad_no_session["hook_event_id"], bad_session["hook_event_id"], naive_ts["hook_event_id"], None
    ]
    assert all(r["reason"].startswith("invalid") for r in body["rejected"])
    assert len(rows(engine, "hook_events_raw", good["session_id"])) == 1


# ------------------------------------------------------------------ freshness


def test_offline_batch_within_seven_days_accepted(client, token, engine, lifecycle):
    three_days = _rebase(lifecycle, newest_age_seconds=3 * 86400)
    resp = post(client, token, three_days)
    assert resp.status_code == 202, resp.text
    assert resp.json()["rejected"] == []
    raw = rows(engine, "hook_events_raw", three_days["events"][0]["session_id"])
    newest = max(r["client_ts"] for r in raw)
    assert datetime.now(timezone.utc) - newest > timedelta(days=2, hours=23)
    assert all(datetime.now(timezone.utc) - r["received_at"] < timedelta(minutes=5) for r in raw)


def test_stale_events_rejected_individually(client, token, engine, lifecycle):
    stale = copy.deepcopy(lifecycle["events"][1])
    stale["hook_event_id"] = str(uuid.uuid4())
    stale["client_ts"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    resp = post(client, token, {"events": [lifecycle["events"][0], stale]})
    assert resp.status_code == 202
    body = resp.json()
    assert body["accepted"] == 1
    assert body["rejected"] == [{"hook_event_id": stale["hook_event_id"], "reason": "stale: client_ts older than 604800s"}]
    assert len(rows(engine, "hook_events_raw", stale["session_id"])) == 1


def test_max_age_configurable(migrated_db, key_dir, token, lifecycle, monkeypatch):
    monkeypatch.setenv("HOOK_EVENTS_MAX_AGE_SECONDS", "30")
    settings = make_settings(migrated_db, key_dir)
    assert settings.hook_events_max_age_seconds == 30
    with TestClient(create_app(settings)) as c:
        body = post(c, token, {"events": [lifecycle["events"][0]]}).json()  # rebased to 60s old
    assert body["accepted"] == 0 and body["rejected"][0]["reason"].startswith("stale")


# ------------------------------------------------------------------ projection


def test_session_assembled_from_lifecycle(client, token, engine, lifecycle):
    resp = post(client, token, lifecycle)
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"accepted": 20, "duplicates": 0, "rejected": []}
    sid = lifecycle["events"][0]["session_id"]
    by_name = {}
    for e in lifecycle["events"]:
        by_name.setdefault(e["hook_event_name"], []).append(e)

    session = one(engine, "sessions", sid)
    assert session["source"] == "startup"  # SessionStart(compact) did not overwrite
    assert session["started_at"].isoformat() == by_name["SessionStart"][0]["client_ts"]
    assert session["ended_at"].isoformat() == by_name["SessionEnd"][0]["client_ts"]
    assert session["end_reason"] == "other"
    assert session["model_initial"] == "claude-haiku-4-5"  # filled by the compact start (was NULL)
    assert session["permission_mode"] == "default" and session["effort"] == "medium"  # from later events
    assert session["cwd_hash"] == by_name["SessionStart"][0]["cwd_hash"]
    assert session["client_version"] == "0.2.0"
    assert session["auth_upn"] == "dev@ouroboros.local"
    assert set(session["raw"]) == {"SessionStart", "SessionStart:compact", "SessionEnd"}

    turns = rows(engine, "turns", sid, order="seq")
    # seq is the client_seq of each turn's UserPromptSubmit, not a 1..n counter
    assert [t["seq"] for t in turns] == [by_name["UserPromptSubmit"][0]["client_seq"], by_name["UserPromptSubmit"][1]["client_seq"]]
    assert [t["prompt_id"] for t in turns] == [by_name["UserPromptSubmit"][0]["prompt_id"], by_name["UserPromptSubmit"][1]["prompt_id"]]
    assert turns[0]["prompt_length"] == 42 and turns[0]["response_length"] == 640
    assert turns[0]["prompt_text"] is None and turns[0]["response_text"] is None  # metadata only
    assert turns[0]["stopped_at"] > turns[0]["submitted_at"]
    assert turns[1]["expansion_type"] == "slash_command" and turns[1]["command_name"] == "hello"
    assert turns[1]["command_source"] == "projectSettings"

    tools = rows(engine, "tool_calls", sid, order="tool_use_id")
    assert [t["tool_name"] for t in tools] == ["Bash", "Bash", "Skill"]  # PostToolBatch ids stay raw-only
    assert all(t["prompt_id"] == turns[0]["prompt_id"] for t in tools)
    bash_ok, bash_failed, skill = tools
    assert bash_ok["duration_ms"] == 1200 and bash_ok["input_bytes"] == 34 and bash_ok["output_bytes"] == 512
    assert bash_ok["started_at"] == datetime.fromisoformat(by_name["PostToolUse"][0]["client_ts"]) - timedelta(milliseconds=1200)
    assert bash_ok["input_content"] is None and bash_ok["output_content"] is None
    assert bash_failed["success"] is False and bash_failed["is_interrupt"] is False
    assert bash_failed["error_class"].startswith("ls: cannot access")

    activation = one(engine, "skill_activations", sid)
    assert activation["skill_name"] == "tinyskill" and activation["prompt_id"] == turns[0]["prompt_id"]
    assert activation["artifact_id"] is not None

    agent = one(engine, "subagent_runs", sid)  # the agent-less /compact SubagentStop made no row
    assert agent["agent_id"] == by_name["SubagentStart"][0]["agent_id"] and agent["agent_type"] == "Explore"
    assert agent["started_at"] < agent["stopped_at"]
    assert agent["last_message_length"] == 318 and len(agent["transcript_hash"]) == 64
    assert agent["total_tokens"] is None  # OTel's half

    compaction = one(engine, "compactions", sid)  # Pre + Post converge on one row
    assert compaction["trigger"] == "manual" and compaction["summary_length"] == 3400
    assert compaction["custom_instructions_length"] == 0
    assert set(compaction["raw"]) == {"hook_pre", "hook_post"}
    assert compaction["pre_tokens"] is None

    loaded = one(engine, "instructions_loaded", sid)
    assert loaded["file_path"] == ".claude/CLAUDE.md" and loaded["memory_type"] == "Project"
    assert len(loaded["content_hash"]) == 64 and loaded["artifact_id"] is not None
    with engine.connect() as conn:
        art = conn.execute(text("SELECT * FROM artifacts WHERE artifact_id = :a"), {"a": loaded["artifact_id"]}).mappings().one()
        ver = conn.execute(text("SELECT * FROM artifact_versions WHERE artifact_id = :a"), {"a": loaded["artifact_id"]}).mappings().all()
    assert art["kind"] == "instructions" and art["path"] == ".claude/CLAUDE.md" and art["source"] == "project"
    assert loaded["content_hash"] in [v["content_hash"] for v in ver]  # the artifact is shared across sessions

    switches = rows(engine, "model_switches", sid, order="at")
    assert [s["phase"] for s in switches] == ["pre", "post"]
    assert switches[0]["from_model"] == "claude-sonnet-4-5" and switches[1]["to_model"] == "claude-haiku-4-5"
    assert switches[0]["context_tokens"] == 12000

    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM events WHERE session_id = :s"), {"s": sid}).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM hook_events_raw WHERE session_id = :s"), {"s": sid}).scalar_one() == 20
        batch_raw = conn.execute(
            text("SELECT payload FROM hook_events_raw WHERE session_id = :s AND hook_event_name = 'PostToolBatch'"), {"s": sid}
        ).scalar_one()
    assert len(batch_raw["tool_use_ids"]) == 3 and batch_raw["client_seq"] == by_name["PostToolBatch"][0]["client_seq"]


def test_replay_is_a_counted_noop(client, token, engine, lifecycle):
    first = post(client, token, lifecycle).json()
    assert first["accepted"] == 20
    second = post(client, token, lifecycle).json()
    assert second == {"accepted": 0, "duplicates": 20, "rejected": []}
    sid = lifecycle["events"][0]["session_id"]
    for table, expected in [("hook_events_raw", 20), ("turns", 2), ("tool_calls", 3), ("subagent_runs", 1),
                            ("compactions", 1), ("instructions_loaded", 1), ("model_switches", 2), ("skill_activations", 1)]:
        assert len(rows(engine, table, sid)) == expected, table


def test_stop_before_user_prompt_submit_converges(client, token, engine, lifecycle):
    by_name = {e["hook_event_name"]: e for e in lifecycle["events"] if e["hook_event_name"] in ("UserPromptSubmit", "Stop")}
    stop, submit = by_name["Stop"], by_name["UserPromptSubmit"]
    assert stop["prompt_id"] == submit["prompt_id"]
    sid = stop["session_id"]

    assert post(client, token, {"events": [stop]}).json()["accepted"] == 1
    turn = one(engine, "turns", sid)
    assert turn["stopped_at"] is not None and turn["submitted_at"] is None
    assert turn["response_length"] == 12 and turn["prompt_length"] is None
    session = one(engine, "sessions", sid)
    assert session["started_at"] is None  # stub only

    assert post(client, token, {"events": [submit]}).json()["accepted"] == 1
    turn = one(engine, "turns", sid)
    assert turn["submitted_at"] is not None and turn["stopped_at"] is not None
    assert turn["prompt_length"] == 7 and turn["response_length"] == 12
    assert set(turn["raw"]) == {"Stop", "UserPromptSubmit"}
    assert turn["seq"] == submit["client_seq"]  # earliest event of the turn wins, not the creator (Stop)
    assert submit["client_seq"] < stop["client_seq"]


def test_turn_seq_falls_back_to_arrival_order_without_client_seq(client, token, engine, lifecycle):
    submits = [e for e in lifecycle["events"] if e["hook_event_name"] == "UserPromptSubmit"]
    for e in submits:
        e.pop("client_seq")
    assert post(client, token, {"events": list(reversed(submits))}).json()["accepted"] == 2
    turns = rows(engine, "turns", submits[0]["session_id"], order="seq")
    assert [t["seq"] for t in turns] == [1, 2]
    assert turns[0]["prompt_id"] == submits[1]["prompt_id"]  # arrival order


def test_malformed_json_is_400(client, token):
    resp = client.post(URL, content=b"{not json", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    assert resp.status_code == 400


def test_content_stored_only_when_client_sends_it(client, token, engine, lifecycle):
    submit = next(e for e in lifecycle["events"] if e["hook_event_name"] == "UserPromptSubmit")
    tool = next(e for e in lifecycle["events"] if e["hook_event_name"] == "PostToolUse")
    submit = dict(submit, payload={"prompt": "please list the files", "permission_mode": "default"})
    tool = dict(tool, payload=dict(tool["payload"], tool_input={"command": "ls"}, tool_response={"stdout": "a\nb"}))
    assert post(client, token, {"events": [submit, tool]}).json()["accepted"] == 2
    sid = submit["session_id"]
    turn = one(engine, "turns", sid)
    assert turn["prompt_text"] == "please list the files" and turn["prompt_length"] == len("please list the files")
    call = one(engine, "tool_calls", sid)
    assert json.loads(call["input_content"]) == {"command": "ls"}
    assert json.loads(call["output_content"]) == {"stdout": "a\nb"}


def test_unknown_hook_name_kept_raw_only(client, token, engine, lifecycle):
    ev = dict(lifecycle["events"][0], hook_event_name="Notification", payload={"message_length": 12})
    assert post(client, token, {"events": [ev]}).json() == {"accepted": 1, "duplicates": 0, "rejected": []}
    sid = ev["session_id"]
    assert one(engine, "hook_events_raw", sid)["hook_event_name"] == "Notification"
    assert one(engine, "sessions", sid)["started_at"] is None


def test_post_tool_batch_projects_each_call(client, token, engine, lifecycle):
    base = next(e for e in lifecycle["events"] if e["hook_event_name"] == "PostToolUse")
    ev = dict(base, hook_event_name="PostToolBatch", payload={
        "tool_calls": [
            {"tool_name": "Read", "tool_use_id": f"toolu_{base['session_id'][:8]}_b1", "output_bytes": 10},
            {"tool_name": "Grep", "tool_use_id": f"toolu_{base['session_id'][:8]}_b2", "output_bytes": 20},
            "not-a-dict",
        ]
    })
    assert post(client, token, {"events": [ev]}).json()["accepted"] == 1
    calls = rows(engine, "tool_calls", ev["session_id"], order="tool_use_id")
    assert [(c["tool_name"], c["output_bytes"]) for c in calls] == [("Read", 10), ("Grep", 20)]
    assert all(c["started_at"].isoformat() == ev["client_ts"] for c in calls)


def test_projection_failure_rejects_only_that_event(client, token, engine, lifecycle):
    good = lifecycle["events"][0]
    # context_tokens beyond INTEGER is dropped by the coercion layer, but a
    # payload that is not an object at all is a shape error.
    broken = dict(lifecycle["events"][1], hook_event_id=str(uuid.uuid4()), payload=["not", "an", "object"])
    body = post(client, token, {"events": [good, broken]}).json()
    assert body["accepted"] == 1
    assert body["rejected"][0]["hook_event_id"] == broken["hook_event_id"]
    assert len(rows(engine, "hook_events_raw", good["session_id"])) == 1
