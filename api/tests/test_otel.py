"""OTLP/HTTP receiver (spike 0.2, tasks 2.4 / 2.9): protobuf and JSON decode,
attribute coercion, mapping into the normalized tables, unmapped names
tolerated, OTLP success response, auth required.

Fixtures under fixtures/otlp/ are synthesized (no real payloads)."""

import copy
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from google.protobuf import json_format
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
    ExportLogsServiceResponse,
)
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
    ExportMetricsServiceResponse,
)
from sqlalchemy import text

from app import otel
from app.otel import coerce_attributes, coerce_value, summarize_logs, summarize_metrics

FIXTURES = Path(__file__).parent / "fixtures" / "otlp"
SESSION = "11111111-1111-4111-8111-111111111111"
PROMPT = "pppppppp-0000-4000-8000-000000000001"
JSON_CT = "application/json"
PROTO_CT = "application/x-protobuf"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def agent_for(session_id: str) -> str:
    return f"agent-{session_id[:17]}"


def _with_session(payload: dict, session_id: str, prompt_id: str | None = None) -> dict:
    """Rewrite the fixture's fixed ids (session, prompt, tool_use, request,
    agent) so tests sharing one database stay independent."""
    text_ = (
        json.dumps(payload)
        .replace(SESSION, session_id)
        .replace("toolu_fake_", f"toolu_{session_id[:8]}_")
        .replace("req_fake_", f"req_{session_id[:8]}_")
        .replace("agent-0123456789abcde", agent_for(session_id))
    )
    if prompt_id:
        text_ = text_.replace(PROMPT, prompt_id)
    return json.loads(text_)


def _headers(token, ct, **extra):
    return {"Authorization": f"Bearer {token}", "Content-Type": ct, **extra}


def post_json(client, token, path, payload, **extra):
    return client.post(path, json=payload, headers=_headers(token, JSON_CT, **extra))


def post_proto(client, token, path, payload, message, **extra):
    msg = json_format.ParseDict(payload, message)
    return client.post(path, content=msg.SerializeToString(), headers=_headers(token, PROTO_CT, **extra))


def rows(engine, table, session_id, order="1"):
    with engine.connect() as conn:
        return conn.execute(
            text(f"SELECT * FROM {table} WHERE session_id = :s ORDER BY {order}"), {"s": session_id}
        ).mappings().all()


@pytest.fixture()
def sid() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def metrics(sid) -> dict:
    return _with_session(_load("metrics.json"), sid)


@pytest.fixture()
def logs(sid) -> dict:
    return _with_session(_load("logs.json"), sid, str(uuid.uuid4()))


# ------------------------------------------------------------------ coercion


def test_coerce_value_shapes():
    assert coerce_value("input_tokens", "130") == 130
    assert coerce_value("duration_ms", "-2723") == -2723
    assert coerce_value("success", "true") is True
    assert coerce_value("safe_mode", "False") is False
    assert coerce_value("has_hooks", True) is True
    assert coerce_value("cost_usd", 0.001) == 0.001
    assert coerce_value("model", "claude-sonnet-4-5") == "claude-sonnet-4-5"
    # identifiers are never coerced even when they look numeric
    assert coerce_value("event.sequence", "42") == 42
    assert coerce_value("plugin.version", "1") == "1"
    assert coerce_value("prompt.id", "123456") == "123456"
    assert coerce_value("request_id", "0") == "0"


def test_coerce_attributes_drops_base_url_and_content_by_default():
    raw = {
        "anthropic.custom_base_url": "https://proxy.example.com",
        "prompt": "<REDACTED>",
        "prompt_text": "hello",
        "response": "x",
        "tool_input": "{}",
        "tool_parameters": "{}",
        "input_tokens": "12",
        "user.id": "0000000000000000000000000000000000000000000000000000000000000001",
    }
    out = coerce_attributes(raw)
    assert out == {"input_tokens": 12, "user.id": raw["user.id"]}
    with_prompts = coerce_attributes(raw, frozenset({"prompts"}))
    assert set(with_prompts) == {"input_tokens", "user.id", "prompt", "prompt_text", "response"}
    with_tools = coerce_attributes(raw, frozenset({"tool_content"}))
    assert set(with_tools) == {"input_tokens", "user.id", "tool_input", "tool_parameters"}
    assert "anthropic.custom_base_url" not in coerce_attributes(raw, frozenset({"prompts", "tool_content"}))


def test_capture_header_parsing():
    class R:  # minimal stand-in for fastapi.Request
        def __init__(self, v):
            self.headers = {"x-ouroboros-capture": v} if v is not None else {}

    assert otel.capture_optins(R(None)) == frozenset()
    assert otel.capture_optins(R("prompts, Tool-Content")) == frozenset({"prompts", "tool_content"})


# ------------------------------------------------------------------ transport


def test_metrics_json_returns_otlp_success(client, token, metrics):
    resp = post_json(client, token, "/v1/otel/v1/metrics", metrics)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(JSON_CT)
    json_format.Parse(resp.content, ExportMetricsServiceResponse())
    summary = summarize_metrics(json_format.ParseDict(metrics, ExportMetricsServiceRequest()))
    assert summary["data_points"] == 13
    assert summary["resource_attribute_keys"] == ["os.type", "service.name", "service.version"]


def test_logs_protobuf_returns_otlp_success(client, token, logs):
    resp = post_proto(client, token, "/v1/otel/v1/logs", logs, ExportLogsServiceRequest())
    assert resp.status_code == 200
    assert resp.headers["content-type"] == PROTO_CT
    ExportLogsServiceResponse().ParseFromString(resp.content)
    summary = summarize_logs(json_format.ParseDict(logs, ExportLogsServiceRequest()))
    assert summary["events"]["api_request"] == 2
    assert summary["events"]["skill_activated"] == 2  # one named via body only
    assert "prompt.id" in summary["attribute_keys"]["api_request"]


def test_metrics_protobuf_roundtrip(client, token, metrics):
    resp = post_proto(client, token, "/v1/otel/v1/metrics", metrics, ExportMetricsServiceRequest())
    assert resp.status_code == 200
    assert resp.content == ExportMetricsServiceResponse().SerializeToString()


def test_requires_auth_and_rejects_bad_bodies(client, token, metrics, logs, engine, sid):
    assert client.post("/v1/otel/v1/metrics", json=metrics).status_code == 401
    assert client.post("/v1/otel/v1/logs", json=logs).status_code == 401
    assert rows(engine, "usage_facts", sid) == []  # nothing stored without a token
    bad_ct = client.post("/v1/otel/v1/logs", content=b"x", headers=_headers(token, "text/plain"))
    assert bad_ct.status_code == 415
    garbage = client.post("/v1/otel/v1/logs", content=b"{not json", headers=_headers(token, JSON_CT))
    assert garbage.status_code == 400
    garbage_pb = client.post("/v1/otel/v1/metrics", content=b"\xff\xff\xff", headers=_headers(token, PROTO_CT))
    assert garbage_pb.status_code == 400


# -------------------------------------------------------------------- metrics


def test_counters_become_reconciliation_facts(client, token, engine, sid, metrics):
    before = otel.UNMAPPED["metric:claude_code.code_edit_tool.decision"]
    assert post_json(client, token, "/v1/otel/v1/metrics", metrics).status_code == 200
    facts = rows(engine, "usage_facts", sid, order="at, kind, token_type")
    kinds = sorted(f["kind"] for f in facts)
    assert kinds == ["active_time", "commit", "cost", "cost", "loc", "pr", "session"] + ["token"] * 5
    assert otel.UNMAPPED["metric:claude_code.code_edit_tool.decision"] == before + 1
    assert all(f["prompt_id"] is None and f["request_id"] is None for f in facts)

    tokens = {(f["token_type"], f["skill_name"]): f for f in facts if f["kind"] == "token"}
    skilled_input = tokens[("input", "tinyskill")]
    assert skilled_input["value"] == 1200 and skilled_input["model"] == "claude-sonnet-4-5"
    assert skilled_input["query_source"] == "main"
    assert skilled_input["attrs"]["type"] == "input" and skilled_input["attrs"]["metric"] == "claude_code.token.usage"
    assert next(c for c in facts if c["kind"] == "cost" and c["skill_name"])["attrs"]["effort"] == "medium"
    assert skilled_input["attrs"]["user.id"].startswith("0000")  # opaque hash kept
    assert "anthropic.custom_base_url" not in skilled_input["attrs"]
    assert tokens[("output", None)]["value"] == 77  # gauge asInt coerced
    assert skilled_input["at"] == datetime.fromtimestamp(1767225605, tz=timezone.utc)

    costs = [f for f in facts if f["kind"] == "cost"]
    assert sorted(float(c["cost_usd"]) for c in costs) == [0.001, 0.0123]
    assert all(c["cost_usd"] == c["value"] for c in costs)
    session_fact = next(f for f in facts if f["kind"] == "session")
    assert session_fact["value"] == 1 and session_fact["attrs"]["start_type"] == "fresh"
    assert next(f for f in facts if f["kind"] == "active_time")["value"] == 12.5

    with engine.connect() as conn:
        s = conn.execute(text("SELECT * FROM sessions WHERE session_id = :s"), {"s": sid}).mappings().one()
    assert s["client_version"] == "2.1.319" and s["started_at"] is None


def test_metric_replay_does_not_duplicate(client, token, engine, sid, metrics):
    for _ in range(2):
        post_json(client, token, "/v1/otel/v1/metrics", metrics)
    assert len(rows(engine, "usage_facts", sid)) == 12


# ----------------------------------------------------------------------- logs


def test_log_events_map_into_tables(client, token, engine, sid, logs):
    unmapped_before = copy.copy(otel.UNMAPPED)
    resp = post_proto(client, token, "/v1/otel/v1/logs", logs, ExportLogsServiceRequest())
    assert resp.status_code == 200
    prompt_id = next(
        a["value"]["stringValue"]
        for r in logs["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        for a in r["attributes"] if a["key"] == "prompt.id"
    )

    # api_request: primary usage fact, cost from micros, tokens in attrs
    facts = rows(engine, "usage_facts", sid)
    assert len(facts) == 1 and facts[0]["kind"] == "api_request"
    f = facts[0]
    assert f["prompt_id"] == prompt_id and f["request_id"] == f"req_{sid[:8]}_0001"
    assert float(f["cost_usd"]) == 0.0123 and f["value"] == 10000
    assert f["model"] == "claude-sonnet-4-5" and f["skill_name"] == "tinyskill" and f["query_source"] == "sdk"
    assert f["attrs"]["input_tokens"] == 1200 and f["attrs"]["cache_creation_5m_tokens"] == 500
    assert f["attrs"]["ttft_ms"] == 900 and f["attrs"]["event.sequence"] == 12
    assert "anthropic.custom_base_url" not in f["attrs"] and "session.id" not in f["attrs"]

    # user_prompt / assistant_response: lengths only, never the redacted text
    turns = rows(engine, "turns", sid)
    assert len(turns) == 1
    t = turns[0]
    assert t["prompt_id"] == prompt_id and t["prompt_length"] == 42 and t["response_length"] == 640
    assert t["prompt_text"] is None and t["response_text"] is None and t["model"] == "claude-sonnet-4-5"
    assert t["submitted_at"] < t["stopped_at"]
    assert "prompt" not in json.dumps(t["raw"]["otel_user_prompt"]).replace("prompt_length", "").replace("prompt.id", "")

    # skill_activated: named skill registers an artifact; redacted name does not
    acts = rows(engine, "skill_activations", sid, order="at")
    assert [a["skill_name"] for a in acts] == ["tinyskill", "custom_skill"]
    assert acts[0]["artifact_id"] is not None and acts[1]["artifact_id"] is None
    assert acts[0]["invocation_trigger"] == "claude-proactive" and acts[0]["skill_source"] == "projectSettings"
    with engine.connect() as conn:
        skill = conn.execute(text("SELECT * FROM artifacts WHERE artifact_id = :a"), {"a": acts[0]["artifact_id"]}).mappings().one()
    assert skill["kind"] == "skill" and skill["name"] == "tinyskill" and skill["source"] == "project"

    # tool_decision + tool_result enrich one tool_calls row; content dropped, bad latency ignored
    calls = rows(engine, "tool_calls", sid)
    assert len(calls) == 1
    c = calls[0]
    assert c["tool_use_id"] == f"toolu_{sid[:8]}_0000000000000001" and c["decision"] == "accept"
    assert c["success"] is True and c["duration_ms"] is None
    assert c["input_bytes"] == 34 and c["output_bytes"] == 512 and c["input_content"] is None
    assert set(c["raw"]) == {"otel_tool_decision", "otel_tool_result"}
    assert "tool_parameters" not in c["raw"]["otel_tool_decision"]

    # subagent_completed
    agent = rows(engine, "subagent_runs", sid)[0]
    assert agent["agent_id"] == agent_for(sid) and agent["total_tokens"] == 5400
    assert agent["total_tool_uses"] == 3 and agent["agent_type"] == "Explore"
    assert agent["started_at"] is not None and agent["stopped_at"] - agent["started_at"] == timedelta(seconds=3)

    # compaction with token delta
    comp = rows(engine, "compactions", sid)[0]
    assert comp["trigger"] == "manual" and comp["pre_tokens"] == 150000 and comp["post_tokens"] == 40000
    assert set(comp["raw"]) == {"otel"}

    # api_error -> turn_errors, message text not stored
    err = rows(engine, "turn_errors", sid)[0]
    assert err["kind"] == "api_error" and err["status_code"] == 529 and err["duration_ms"] == 1500
    assert err["error_class"] == "overloaded_error: Overloaded" and err["message"] is None
    assert "error" not in err["raw"]

    # hook_execution_* -> hook_runs
    runs = rows(engine, "hook_runs", sid, order="at")
    assert [r["phase"] for r in runs] == ["start", "complete"]
    assert runs[1]["duration_ms"] == 23 and runs[1]["num_success"] == 1 and runs[1]["success"] is True
    assert runs[1]["hook_event"] == "UserPromptSubmit" and runs[1]["hook_source"] == "merged"

    # plugin_loaded / hook_registered -> artifacts (+ version from plugin.version)
    with engine.connect() as conn:
        plugins = conn.execute(text("SELECT * FROM artifacts WHERE kind = 'plugin' ORDER BY name")).mappings().all()
        hooks = conn.execute(text("SELECT * FROM artifacts WHERE kind = 'hook' AND owner_repo = 'ouroboros' ORDER BY name")).mappings().all()
        versions = conn.execute(
            text("SELECT v.* FROM artifact_versions v JOIN artifacts a USING (artifact_id) WHERE a.kind = 'plugin' AND a.name = 'ouroboros'")
        ).mappings().all()
    names = [p["name"] for p in plugins]
    assert "ouroboros" in names and any(n.startswith("third-party:") for n in names)
    ours = next(p for p in plugins if p["name"] == "ouroboros")
    assert ours["source"] == "user" and ours["plugin_id_hash"].endswith("2") and ours["owner_repo"] == "ouroboros"
    assert ours["raw"]["otel_plugin_loaded"]["has_hooks"] is True and ours["raw"]["otel_plugin_loaded"]["safe_mode"] is False
    assert [v["content_hash"] for v in versions] == ["plugin-version:0.2.0"]
    assert versions[0]["frontmatter"] == {"version": "0.2.0"}
    assert [h["name"] for h in hooks] == ["Stop", "UserPromptSubmit"]
    assert all(h["source"] == "plugin" and h["path"] == "command" for h in hooks)

    # unmapped / incomplete records counted, never rejected
    delta = {k: v - unmapped_before.get(k, 0) for k, v in otel.UNMAPPED.items() if v != unmapped_before.get(k, 0)}
    assert delta == {
        "event:managed_settings_resolved": 1,
        "event:mcp_server_connection": 1,
        "event:api_request:no_session": 1,
    }


def test_log_replay_is_idempotent(client, token, engine, sid, logs):
    for _ in range(2):
        post_json(client, token, "/v1/otel/v1/logs", logs)
    for table, expected in [("usage_facts", 1), ("turns", 1), ("skill_activations", 2), ("tool_calls", 1),
                            ("subagent_runs", 1), ("compactions", 1), ("turn_errors", 1), ("hook_runs", 2)]:
        assert len(rows(engine, table, sid)) == expected, table


def test_content_optin_header_stores_prompt_and_tool_input(client, token, engine, sid, logs):
    records = logs["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    for r in records:
        for a in r["attributes"]:
            if a["key"] in ("prompt", "response") and a["value"] == {"stringValue": "<REDACTED>"}:
                a["value"] = {"stringValue": "real " + a["key"]}
    resp = post_json(client, token, "/v1/otel/v1/logs", logs, **{"X-Ouroboros-Capture": "prompts,tool_content"})
    assert resp.status_code == 200
    t = rows(engine, "turns", sid)[0]
    assert t["prompt_text"] == "real prompt" and t["response_text"] == "real response"
    c = rows(engine, "tool_calls", sid)[0]
    # tool_decision arrives first with tool_parameters; tool_result fills only NULLs
    assert json.loads(c["input_content"]) == {"bash_command": "ls", "full_command": "ls -la"}


def test_subagent_converges_from_both_pipelines_in_either_order(client, token, engine):
    fixture = _load("logs.json")
    hook_stop = {
        "hook_event_id": str(uuid.uuid4()),
        "session_id": None,
        "prompt_id": None,
        "hook_event_name": "SubagentStop",
        "client_ts": datetime.now(timezone.utc).isoformat(),
        "payload": {"agent_type": "Explore", "last_assistant_message_length": 5},
    }
    otel_only = {"resourceLogs": [{"resource": fixture["resourceLogs"][0]["resource"], "scopeLogs": [{"logRecords": [
        r for r in fixture["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        if any(a["key"] == "event.name" and a["value"]["stringValue"] == "subagent_completed" for a in r["attributes"])
    ]}]}]}

    for order in ("hook_first", "otel_first"):
        sid = str(uuid.uuid4())
        hook = dict(hook_stop, hook_event_id=str(uuid.uuid4()), session_id=sid, agent_id=agent_for(sid))
        otel_payload = _with_session(otel_only, sid)
        steps = [
            lambda: client.post("/v1/telemetry/hook-events", json={"events": [hook]}, headers={"Authorization": f"Bearer {token}"}),
            lambda: post_json(client, token, "/v1/otel/v1/logs", otel_payload),
        ]
        if order == "otel_first":
            steps.reverse()
        for step in steps:
            assert step().status_code in (200, 202)
        runs = rows(engine, "subagent_runs", sid)
        assert len(runs) == 1, order
        run = runs[0]
        assert run["agent_type"] == "Explore" and run["total_tokens"] == 5400 and run["total_tool_uses"] == 3
        assert run["last_message_length"] == 5 and run["stopped_at"] is not None
        assert set(run["raw"]) == {"SubagentStop", "otel_subagent_completed"}, order


def test_skill_activation_converges_hook_and_otel(client, token, engine, logs):
    sid, pid = str(uuid.uuid4()), str(uuid.uuid4())
    hook = {
        "hook_event_id": str(uuid.uuid4()), "session_id": sid, "prompt_id": pid, "hook_event_name": "PostToolUse",
        "client_ts": datetime.now(timezone.utc).isoformat(),
        "payload": {"tool_name": "Skill", "tool_use_id": f"toolu_{sid[:8]}_skill", "tool_input": {"skill": "tinyskill"}, "duration_ms": 30},
    }
    skill_rec = next(
        r for r in logs["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        if any(a["key"] == "skill.name" and a["value"]["stringValue"] == "tinyskill" for a in r["attributes"])
    )
    skill_rec = copy.deepcopy(skill_rec)
    for a in skill_rec["attributes"]:
        if a["key"] == "session.id":
            a["value"]["stringValue"] = sid
        if a["key"] == "prompt.id":
            a["value"]["stringValue"] = pid
    otel_payload = {"resourceLogs": [{"scopeLogs": [{"logRecords": [skill_rec]}]}]}

    assert post_json(client, token, "/v1/otel/v1/logs", otel_payload).status_code == 200
    assert client.post("/v1/telemetry/hook-events", json={"events": [hook]}, headers={"Authorization": f"Bearer {token}"}).status_code == 202
    acts = rows(engine, "skill_activations", sid)
    assert len(acts) == 1
    assert acts[0]["skill_name"] == "tinyskill" and acts[0]["invocation_trigger"] == "claude-proactive"
    assert acts[0]["prompt_id"] == pid and acts[0]["artifact_id"] is not None


def test_turn_from_otel_then_hook_fills_nulls(client, token, engine, sid, logs):
    """Built-in /model and /compact emit OTel user_prompt with no hook pair (spike 0.4)."""
    post_json(client, token, "/v1/otel/v1/logs", logs)
    t = rows(engine, "turns", sid)[0]
    hook = {
        "hook_event_id": str(uuid.uuid4()), "session_id": sid, "prompt_id": t["prompt_id"],
        "hook_event_name": "UserPromptSubmit", "client_ts": datetime.now(timezone.utc).isoformat(),
        "payload": {"prompt_length": 999, "permission_mode": "plan"},
    }
    client.post("/v1/telemetry/hook-events", json={"events": [hook]}, headers={"Authorization": f"Bearer {token}"})
    after = rows(engine, "turns", sid)
    assert len(after) == 1
    assert after[0]["prompt_length"] == 42  # first value wins; nulls only are filled
    assert after[0]["submitted_at"] == t["submitted_at"]
    assert set(after[0]["raw"]) == {"otel_user_prompt", "otel_assistant_response", "UserPromptSubmit"}
    with engine.connect() as conn:
        s = conn.execute(text("SELECT permission_mode FROM sessions WHERE session_id = :s"), {"s": sid}).scalar_one()
    assert s == "plan"
