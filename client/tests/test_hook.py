"""ouroboros-hook: turn cache (unchanged behaviour) + hook-event projections
(design D6 as amended by spike 0.4). Fixtures under tests/fixtures/hooks/ are
the redacted real captures from the survey (`<EventName>.json`, plus
`<EventName>.<variant>.json`); assertions tolerate extra keys in the *input*
and check the *output* whitelist exactly."""

import hashlib
import io
import json
import os
import time
import uuid
from pathlib import Path

import pytest

from ouroboros_client import __version__, config, hook, interactions, spool

FIXTURES = Path(__file__).parent / "fixtures" / "hooks"
FIXTURE_FILES = sorted(p.stem for p in FIXTURES.glob("*.json"))  # incl. variants
EVENTS = sorted({stem.split(".")[0] for stem in FIXTURE_FILES})

# Keys that must never appear anywhere in a spooled record (raw stdin leakage).
RAW_ONLY_KEYS = {"transcript_path", "cwd", "background_tasks", "session_crons", "tool_calls",
                 "prompt_cache_warm", "cache_ttl", "estimated_cache_write_usd", "pricing",
                 "seconds_since_last_response", "prompt_cache_likely_expired"}

# Whitelist per event: keys always present (null when absent from the payload),
# keys that need OUROBOROS_CAPTURE_PROMPTS=on, keys that need TOOL_CONTENT=on.
TOOL_META = {"tool_name", "tool_use_id", "mcp_server", "duration_ms", "success", "is_interrupt",
             "input_bytes", "output_bytes", "error_class", "skill_name"}
EXPECTED = {
    "SessionStart": ({"source", "model", "agent_type", "context_tokens"}, {"session_title"}, set()),
    "SessionEnd": ({"reason"}, set(), set()),
    "UserPromptSubmit": ({"prompt_length"}, {"prompt"}, set()),
    "UserPromptExpansion": ({"expansion_type", "command_name", "command_source", "command_args_length"}, set(), {"command_args"}),
    "PostToolUse": (TOOL_META, set(), {"tool_input", "tool_response"}),
    "PostToolUseFailure": (TOOL_META, set(), {"tool_input", "tool_response"}),
    "PostToolBatch": ({"tool_use_ids"}, set(), set()),
    "SubagentStart": ({"agent_id", "agent_type", "agent_transcript_hash", "last_assistant_message_length"}, {"last_assistant_message"}, set()),
    "SubagentStop": ({"agent_id", "agent_type", "agent_transcript_hash", "last_assistant_message_length"}, {"last_assistant_message"}, set()),
    "PreCompact": ({"trigger", "custom_instructions_length", "compact_summary_length"}, {"compact_summary"}, set()),
    "PostCompact": ({"trigger", "custom_instructions_length", "compact_summary_length"}, {"compact_summary"}, set()),
    "InstructionsLoaded": ({"file_path", "content_sha256", "memory_type", "load_reason", "globs", "trigger_file_path", "parent_file_path"}, set(), set()),
    "PreModelSwitch": ({"from_model", "to_model", "requested_model", "source", "context_tokens"}, set(), set()),
    "PostModelSwitch": ({"from_model", "to_model", "requested_model", "source", "context_tokens"}, set(), set()),
    "Stop": ({"stop_hook_active", "last_assistant_message_length"}, set(), set()),
}


def event_name(stem: str) -> str:
    return stem.split(".")[0]


def load_fixture(stem: str) -> dict:
    return json.loads((FIXTURES / f"{stem}.json").read_text())


def expected_keys(stem: str, data: dict, *, prompts: bool, tool_content: bool) -> set:
    always, prompt_keys, tool_keys = EXPECTED[event_name(stem)]
    keys = set(always)
    if prompts:
        keys |= prompt_keys
    if tool_content:
        keys |= tool_keys
    keys |= {k for k in hook.COMMON_KEYS if k in data}
    return keys


def run_hook(payload, monkeypatch) -> int:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))
    return hook.main()


def spooled() -> list[dict]:
    return [json.loads(p.read_text()) for p in spool.list_hook_files()]


def prompt_event(session_id, prompt_id="p1", text="how do I join tables?"):
    return {
        "hook_event_name": "UserPromptSubmit",
        "session_id": session_id,
        "prompt_id": prompt_id,
        "prompt_text": text,
        "cwd": "/tmp",
    }


def stop_event(session_id, prompt_id="p1", text="Here is the JOIN query."):
    return {
        "hook_event_name": "Stop",
        "session_id": session_id,
        "prompt_id": prompt_id,
        "last_assistant_message": text,
        "stop_reason": "end_turn",
    }


# --- turn cache: unchanged behaviour ----------------------------------------


def test_prompt_and_stop_pair_up(ouro_home, monkeypatch):
    sid = str(uuid.uuid4())
    assert run_hook(prompt_event(sid), monkeypatch) == 0
    assert run_hook(stop_event(sid), monkeypatch) == 0
    pair = interactions.take_latest_complete(sid)
    assert pair == {
        "user_prompt": "how do I join tables?",
        "assistant_response": "Here is the JOIN query.",
    }
    # ... and two hook-event records were spooled (hook-capture spec)
    names = [r["hook_event_name"] for r in spooled()]
    assert names == ["UserPromptSubmit", "Stop"]


def test_pairing_matches_prompt_id(ouro_home, monkeypatch):
    sid = str(uuid.uuid4())
    run_hook(prompt_event(sid, "p1", "first"), monkeypatch)
    run_hook(prompt_event(sid, "p2", "second"), monkeypatch)
    run_hook(stop_event(sid, "p1", "answer to first"), monkeypatch)
    pair = interactions.take_latest_complete(sid)
    assert pair == {"user_prompt": "first", "assistant_response": "answer to first"}


def test_stop_without_prompt_id_completes_newest_open(ouro_home, monkeypatch):
    sid = str(uuid.uuid4())
    run_hook(prompt_event(sid, None, "orphan prompt"), monkeypatch)
    event = stop_event(sid, None, "orphan answer")
    del event["prompt_id"]
    run_hook(event, monkeypatch)
    pair = interactions.take_latest_complete(sid)
    assert pair["assistant_response"] == "orphan answer"


def test_stop_without_any_prompt_is_noop(ouro_home, monkeypatch):
    sid = str(uuid.uuid4())
    assert run_hook(stop_event(sid), monkeypatch) == 0
    assert interactions.take_latest_complete(sid) is None


def test_ring_eviction(ouro_home, monkeypatch):
    monkeypatch.setenv("OUROBOROS_INTERACTION_RING", "3")
    sid = str(uuid.uuid4())
    for i in range(6):
        run_hook(prompt_event(sid, f"p{i}", f"prompt {i}"), monkeypatch)
    entries = json.loads((config.interactions_dir() / f"{sid}.json").read_text())
    assert [e["user_prompt"] for e in entries] == ["prompt 3", "prompt 4", "prompt 5"]


def test_ttl_cleanup(ouro_home, monkeypatch):
    old_sid, new_sid = str(uuid.uuid4()), str(uuid.uuid4())
    run_hook(prompt_event(old_sid), monkeypatch)
    old_file = config.interactions_dir() / f"{old_sid}.json"
    stale = time.time() - config.interaction_ttl_seconds() - 10
    os.utime(old_file, (stale, stale))

    run_hook(prompt_event(new_sid), monkeypatch)
    assert not old_file.exists()
    assert (config.interactions_dir() / f"{new_sid}.json").exists()


# --- exit 0, opt-out, stdlib ------------------------------------------------


def test_malformed_input_exits_zero(ouro_home, monkeypatch):
    assert run_hook("this is not json {", monkeypatch) == 0
    assert run_hook("", monkeypatch) == 0
    assert run_hook("[1, 2, 3]", monkeypatch) == 0
    assert run_hook('"just a string"', monkeypatch) == 0
    assert run_hook({"hook_event_name": "SomethingElse", "session_id": "s"}, monkeypatch) == 0
    assert run_hook({"hook_event_name": "UserPromptSubmit"}, monkeypatch) == 0  # no session_id
    assert run_hook({"hook_event_name": "PostToolUse", "session_id": 42}, monkeypatch) == 0
    assert run_hook({"hook_event_name": "SessionStart", "session_id": "s", "context_tokens": "lots",
                     "source": {"nested": True}, "model": None}, monkeypatch) == 0
    assert not (ouro_home / "interactions").exists() or not list(
        (ouro_home / "interactions").glob("*.json")
    )
    # only the (tolerant) SessionStart produced a record; nothing from garbage
    assert [r["hook_event_name"] for r in spooled()] == ["SessionStart"]
    # garbage-typed values are dropped to null, never copied
    assert spooled()[0]["payload"] == {"source": None, "model": None, "agent_type": None,
                                       "context_tokens": None, "session_title": None}


def test_garbage_stdin_through_subprocess_exits_zero(ouro_home):
    import subprocess
    import sys

    for raw in (b"\xff\xfe garbage", b"{", b"null", b""):
        result = subprocess.run(
            [sys.executable, "-m", "ouroboros_client.hook"],
            input=raw, capture_output=True, env={**os.environ, "OUROBOROS_HOME": str(ouro_home)},
        )
        assert result.returncode == 0
        assert result.stdout == b"" and result.stderr == b""


def test_opt_out_captures_nothing(ouro_home, monkeypatch):
    monkeypatch.setenv("OUROBOROS_TELEMETRY", "off")
    sid = str(uuid.uuid4())
    assert run_hook(prompt_event(sid), monkeypatch) == 0
    for stem in FIXTURE_FILES:
        assert run_hook(load_fixture(stem), monkeypatch) == 0
    assert not (ouro_home / "interactions").exists()
    assert not (ouro_home / "spool").exists()


def test_hook_module_is_stdlib_only(ouro_home):
    import subprocess
    import sys

    # Importing the hook module must not pull httpx (design D4 / D16)
    code = (
        "import sys; import ouroboros_client.hook; "
        "sys.exit(1 if 'httpx' in sys.modules else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code])
    assert result.returncode == 0


def test_all_d6_events_supported():
    assert set(hook.SUPPORTED_EVENTS) == set(EXPECTED) == set(EVENTS)
    assert len(EVENTS) == 15


# --- record envelope ---------------------------------------------------------


def test_record_envelope(ouro_home, monkeypatch):
    data = load_fixture("PostToolUse")
    run_hook(data, monkeypatch)
    (record,) = spooled()
    assert set(record) == {"hook_event_id", "session_id", "prompt_id", "hook_event_name",
                           "client_ts", "client_version", "client_seq", "cwd_hash", "payload"}
    assert record["client_seq"] == 1
    assert uuid.UUID(record["hook_event_id"]).version == 4
    assert record["session_id"] == data["session_id"]
    assert record["prompt_id"] == data["prompt_id"]
    assert record["client_version"] == __version__
    assert record["client_ts"].endswith("Z") and "T" in record["client_ts"]
    assert record["cwd_hash"] == hashlib.sha256(data["cwd"].encode()).hexdigest()
    assert "transcript_path" not in json.dumps(record)
    assert "/home/user" not in json.dumps(record)


def test_agent_id_lifted_to_envelope(ouro_home, monkeypatch):
    run_hook(load_fixture("SubagentStop"), monkeypatch)
    (record,) = spooled()
    assert record["agent_id"] == load_fixture("SubagentStop")["agent_id"]


def test_subagent_stop_without_agent_id_is_accepted(ouro_home, monkeypatch):
    # the /compact summarizer fires SubagentStop without agent_id/agent_type (spike 0.4)
    data = load_fixture("SubagentStop")
    del data["agent_id"]
    del data["agent_type"]
    run_hook(data, monkeypatch)
    (record,) = spooled()
    assert "agent_id" not in record
    assert record["payload"]["agent_id"] is None and record["payload"]["agent_type"] is None
    assert record["payload"]["agent_transcript_hash"]


def test_client_seq_increments_per_session(ouro_home, monkeypatch):
    sid_a, sid_b = str(uuid.uuid4()), str(uuid.uuid4())
    run_hook(prompt_event(sid_a, "p1"), monkeypatch)
    run_hook(stop_event(sid_a, "p1"), monkeypatch)
    run_hook(prompt_event(sid_b, "p1"), monkeypatch)
    run_hook(prompt_event(sid_a, "p2"), monkeypatch)
    seqs = [(r["session_id"], r["client_seq"]) for r in spooled()]
    assert seqs == [(sid_a, 1), (sid_a, 2), (sid_b, 1), (sid_a, 3)]


def test_client_seq_is_the_order_key_not_the_clock(ouro_home, monkeypatch):
    # Filenames carry the wall clock (ns), which can step backwards on the
    # host (spike 0.4); client_seq is the per-session order the server uses.
    sid = str(uuid.uuid4())
    for i in range(50):
        run_hook(prompt_event(sid, f"p{i}"), monkeypatch)
    by_seq = sorted(spooled(), key=lambda r: r["client_seq"])
    assert [r["client_seq"] for r in by_seq] == list(range(1, 51))
    assert [r["prompt_id"] for r in by_seq] == [f"p{i}" for i in range(50)]


def test_version_matches_pyproject():
    import tomllib

    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == __version__ == "0.2.0"


# --- projections, every fixture (incl. variants), toggles off/on -------------


@pytest.mark.parametrize("stem", FIXTURE_FILES)
def test_projection_defaults(stem, ouro_home, monkeypatch):
    """Default toggles: prompts on, tool content off. Every whitelisted key is
    present (null when the payload lacks it); nothing else is."""
    data = load_fixture(stem)
    assert run_hook(data, monkeypatch) == 0
    (record,) = spooled()
    assert record["hook_event_name"] == event_name(stem)
    payload = record["payload"]
    assert set(payload) == expected_keys(stem, data, prompts=True, tool_content=False)
    _, _, tool_keys = EXPECTED[event_name(stem)]
    for key in tool_keys:
        if key in data and data[key]:
            assert json.dumps(data[key]) not in json.dumps(payload), "tool content leaked"
    assert not (RAW_ONLY_KEYS & set(payload))
    dumped = json.dumps(record)
    assert "transcript_path" not in dumped and "/home/user" not in dumped


@pytest.mark.parametrize("stem", FIXTURE_FILES)
def test_projection_prompts_off(stem, ouro_home, monkeypatch):
    monkeypatch.setenv("OUROBOROS_CAPTURE_PROMPTS", "off")
    data = load_fixture(stem)
    run_hook(data, monkeypatch)
    (record,) = spooled()
    assert set(record["payload"]) == expected_keys(stem, data, prompts=False, tool_content=False)
    # none of the verbatim text from the fixture survives
    for key in ("prompt", "last_assistant_message", "compact_summary", "session_title"):
        if isinstance(data.get(key), str) and data[key]:
            assert data[key] not in json.dumps(record)
    assert not (ouro_home / "interactions").exists()


@pytest.mark.parametrize("stem", FIXTURE_FILES)
def test_projection_tool_content_on(stem, ouro_home, monkeypatch):
    monkeypatch.setenv("OUROBOROS_CAPTURE_TOOL_CONTENT", "on")
    data = load_fixture(stem)
    run_hook(data, monkeypatch)
    (record,) = spooled()
    payload = record["payload"]
    assert set(payload) == expected_keys(stem, data, prompts=True, tool_content=True)
    _, _, tool_keys = EXPECTED[event_name(stem)]
    for key in tool_keys:
        source = data.get(key)
        if source is None:
            assert payload[key] is None
        else:
            expected = source if isinstance(source, str) else json.dumps(source, separators=(",", ":"))
            assert payload[key] == expected


def test_common_keys_effort_object_flattened(ouro_home, monkeypatch):
    data = load_fixture("PostToolUse")
    assert data["effort"] == {"level": "medium"}  # real shape
    run_hook(data, monkeypatch)
    p = spooled()[0]["payload"]
    assert p["effort"] == "medium" and p["permission_mode"] == "default"
    # a SessionStart(startup) has neither -> neither key appears
    run_hook(load_fixture("SessionStart"), monkeypatch)
    assert "effort" not in spooled()[1]["payload"] and "permission_mode" not in spooled()[1]["payload"]


def test_session_start_variants(ouro_home, monkeypatch):
    for stem in ("SessionStart", "SessionStart.compact", "SessionStart.resume"):
        run_hook(load_fixture(stem), monkeypatch)
    startup, compact, resume = [r["payload"] for r in spooled()]
    assert startup == {"source": "startup", "model": None, "agent_type": None, "context_tokens": None, "session_title": None}
    assert compact["source"] == "compact" and compact["model"] == "claude-haiku-5-5"
    assert resume["source"] == "resume" and resume["context_tokens"] == 21277
    assert "estimated_cache_write_usd" not in resume


def test_model_invoked_skill_surfaces_as_skill_name(ouro_home, monkeypatch):
    run_hook(load_fixture("PostToolUse"), monkeypatch)
    p = spooled()[0]["payload"]
    assert p["tool_name"] == "Skill" and p["skill_name"] == "tinyskill"
    assert p["success"] is True and p["is_interrupt"] is None and p["mcp_server"] is None
    assert p["output_bytes"] == len(json.dumps(load_fixture("PostToolUse")["tool_response"], separators=(",", ":")).encode())
    assert "commandName" not in json.dumps(p)


def test_agent_tool_use_metadata_only(ouro_home, monkeypatch):
    data = load_fixture("PostToolUse.Agent")
    run_hook(data, monkeypatch)
    p = spooled()[0]["payload"]
    assert p["tool_name"] == "Agent" and p["skill_name"] is None and p["duration_ms"] == 1298
    assert p["output_bytes"] > 500
    dumped = json.dumps(p)
    assert "resolvedModel" not in dumped and "agentId" not in dumped and data["tool_input"]["prompt"] not in dumped


def test_post_tool_batch_projects_ids_only(ouro_home, monkeypatch):
    data = load_fixture("PostToolBatch")
    run_hook(data, monkeypatch)
    p = spooled()[0]["payload"]
    assert p["tool_use_ids"] == [c["tool_use_id"] for c in data["tool_calls"]]
    assert "tool_response" not in json.dumps(p) and "tinyskill" not in json.dumps(p)
    data["tool_calls"] = "weird"
    run_hook(data, monkeypatch)
    assert spooled()[1]["payload"]["tool_use_ids"] is None


def test_prompt_submit_lengths_and_text(ouro_home, monkeypatch):
    data = load_fixture("UserPromptSubmit")
    run_hook(data, monkeypatch)
    (record,) = spooled()
    assert record["payload"] == {"prompt": data["prompt"], "prompt_length": len(data["prompt"]),
                                 "permission_mode": "default"}
    # the cache was written too (default on)
    assert interactions.take_latest_complete(data["session_id"]) is None  # no Stop yet
    entries = json.loads((config.interactions_dir() / f"{data['session_id']}.json").read_text())
    assert entries[0]["user_prompt"] == data["prompt"]


def test_prompts_off_keeps_structure_and_skips_cache(ouro_home, monkeypatch):
    monkeypatch.setenv("OUROBOROS_CAPTURE_PROMPTS", "off")
    sid = str(uuid.uuid4())
    run_hook(prompt_event(sid, "p1", "secret question"), monkeypatch)
    run_hook(stop_event(sid, "p1", "secret answer"), monkeypatch)
    prompt_rec, stop_rec = spooled()
    assert prompt_rec["payload"] == {"prompt_length": len("secret question")}
    assert stop_rec["payload"] == {"stop_hook_active": None, "last_assistant_message_length": len("secret answer")}
    assert interactions.take_latest_complete(sid) is None
    assert not (ouro_home / "interactions").exists()


def test_tool_use_metadata_only_by_default(ouro_home, monkeypatch):
    data = load_fixture("PostToolUse")
    data["tool_response"] = {"stdout": "x" * 200_000}
    run_hook(data, monkeypatch)
    (record,) = spooled()
    p = record["payload"]
    assert p["tool_name"] == data["tool_name"]
    assert p["duration_ms"] == data["duration_ms"]
    assert p["success"] is True
    assert p["output_bytes"] == len(json.dumps(data["tool_response"], separators=(",", ":")).encode())
    assert p["input_bytes"] == len(json.dumps(data["tool_input"], separators=(",", ":")).encode())
    assert "tool_response" not in p and "tool_input" not in p
    assert "x" * 100 not in json.dumps(record)
    assert "effort" in p and p["effort"] == "medium"  # common key copied from the real payload


def test_tool_content_on_is_bounded(ouro_home, monkeypatch):
    monkeypatch.setenv("OUROBOROS_CAPTURE_TOOL_CONTENT", "on")
    data = load_fixture("PostToolUse")
    data["tool_response"] = "y" * 200_000
    run_hook(data, monkeypatch)
    (record,) = spooled()
    assert len(record["payload"]["tool_response"]) == hook._TOOL_CONTENT_MAX
    assert record["payload"]["tool_response_truncated"] is True
    assert record["payload"]["output_bytes"] == 200_000


def test_tool_failure_projection(ouro_home, monkeypatch):
    data = load_fixture("PostToolUseFailure")
    run_hook(data, monkeypatch)
    (record,) = spooled()
    p = record["payload"]
    assert p["success"] is False
    assert p["is_interrupt"] is False
    assert p["error_class"] == data["error"][:hook._ERROR_CLASS_MAX]
    assert p["output_bytes"] is None  # no tool_response on a failure
    assert p["mcp_server"] is None


def test_error_class_is_first_line_truncated_and_mcp_server_parsed(ouro_home, monkeypatch):
    data = load_fixture("PostToolUseFailure")
    data["tool_name"] = "mcp__github__create_pr"
    data["error"] = "PermissionError: token lacks repo scope\n  at line 2\n" + "z" * 500
    run_hook(data, monkeypatch)
    p = spooled()[0]["payload"]
    assert p["error_class"] == "PermissionError: token lacks repo scope"
    assert p["mcp_server"] == "github"
    assert "at line 2" not in json.dumps(p) and "zzzz" not in json.dumps(p)


def test_subagent_projection_hashes_transcript_path(ouro_home, monkeypatch):
    data = load_fixture("SubagentStop")
    run_hook(data, monkeypatch)
    (record,) = spooled()
    p = record["payload"]
    assert p["agent_transcript_hash"] == hashlib.sha256(data["agent_transcript_path"].encode()).hexdigest()
    assert data["agent_transcript_path"] not in json.dumps(record)
    assert p["last_assistant_message"] == data["last_assistant_message"]
    assert p["last_assistant_message_length"] == len(data["last_assistant_message"])


def test_compact_projection(ouro_home, monkeypatch):
    pre, post = load_fixture("PreCompact"), load_fixture("PostCompact")
    assert pre["custom_instructions"] is None  # bare /compact (spike 0.4)
    pre["custom_instructions"] = "keep the test names"
    run_hook(pre, monkeypatch)
    run_hook(post, monkeypatch)
    pre_rec, post_rec = spooled()
    assert pre_rec["payload"] == {"trigger": "manual", "custom_instructions_length": len("keep the test names"),
                                  "compact_summary_length": None, "compact_summary": None}
    assert "keep the test names" not in json.dumps(pre_rec)  # instructions text never captured
    assert post_rec["payload"]["compact_summary"] == post["compact_summary"]
    assert post_rec["payload"]["compact_summary_length"] == len(post["compact_summary"])


def test_model_switch_projection(ouro_home, monkeypatch):
    data = load_fixture("PreModelSwitch")
    run_hook(data, monkeypatch)
    assert spooled()[0]["payload"] == {"from_model": data["from_model"], "to_model": data["to_model"],
                                       "requested_model": "haiku", "source": "command",
                                       "context_tokens": data["context_tokens"]}
    assert "cache_ttl" not in json.dumps(spooled()[0])


def test_session_end_projection(ouro_home, monkeypatch):
    run_hook(load_fixture("SessionEnd"), monkeypatch)
    assert spooled()[0]["payload"] == {"reason": "other"}


def test_expansion_args_text_needs_tool_content(ouro_home, monkeypatch):
    data = load_fixture("UserPromptExpansion")
    run_hook(data, monkeypatch)
    p = spooled()[0]["payload"]
    assert p["command_name"] == "hello" and p["command_source"] == "projectSettings"
    assert p["command_args_length"] == 0 and "command_args" not in p
    data["command_args"] = "--fix PR-12"
    monkeypatch.setenv("OUROBOROS_CAPTURE_TOOL_CONTENT", "on")
    run_hook(data, monkeypatch)
    assert spooled()[1]["payload"]["command_args"] == "--fix PR-12"
    assert data["prompt"] not in json.dumps(spooled())  # the expansion's prompt text is not whitelisted


# --- InstructionsLoaded: path relative to cwd + content hash -----------------


def test_instructions_loaded_real_fixture_hashes_claude_md(ouro_home, monkeypatch, tmp_path):
    data = load_fixture("InstructionsLoaded")
    (tmp_path / "CLAUDE.md").write_text("# project\n")
    data["cwd"] = str(tmp_path)
    data["file_path"] = str(tmp_path / "CLAUDE.md")
    run_hook(data, monkeypatch)
    p = spooled()[0]["payload"]
    assert p["file_path"] == "CLAUDE.md" and p["memory_type"] == "Project" and p["load_reason"] == "session_start"
    assert p["content_sha256"] == hashlib.sha256(b"# project\n").hexdigest()


def test_instructions_loaded_hash_and_relative_path(ouro_home, monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    rule = repo / ".claude" / "rules" / "terraform.md"
    rule.parent.mkdir(parents=True)
    rule.write_text("# Rule: terraform\nnever apply prod\n")
    (repo / "CLAUDE.md").write_text("# project\n")
    data = load_fixture("InstructionsLoaded")
    data.update({
        "cwd": str(repo),
        "file_path": str(rule),
        "memory_type": "project", "load_reason": "glob_match", "globs": ["modules/**", "live/**"],
        "parent_file_path": str(repo / "CLAUDE.md"),
        "trigger_file_path": str(repo / "modules" / "hub" / "main.tf"),
    })
    run_hook(data, monkeypatch)
    (record,) = spooled()
    p = record["payload"]
    assert p["file_path"] == ".claude/rules/terraform.md"
    assert p["parent_file_path"] == "CLAUDE.md"
    assert p["trigger_file_path"] == "modules/hub/main.tf"
    assert p["content_sha256"] == hashlib.sha256(rule.read_bytes()).hexdigest()
    assert p["memory_type"] == "project" and p["load_reason"] == "glob_match"
    assert p["globs"] == ["modules/**", "live/**"]
    assert "never apply prod" not in json.dumps(record)
    assert str(tmp_path) not in json.dumps(record)


def test_instructions_loaded_unreadable_file_has_no_hash(ouro_home, monkeypatch, tmp_path):
    data = load_fixture("InstructionsLoaded")
    data["cwd"] = str(tmp_path)
    data["file_path"] = str(tmp_path / "missing.md")
    run_hook(data, monkeypatch)
    p = spooled()[0]["payload"]
    assert p["file_path"] == "missing.md" and p["content_sha256"] is None
    assert p["globs"] is None and p["trigger_file_path"] is None and p["parent_file_path"] is None


def test_instructions_loaded_home_path_folds_to_tilde(ouro_home, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    data = load_fixture("InstructionsLoaded")
    data["cwd"] = str(tmp_path / "work" / "repo")
    data["file_path"] = str(tmp_path / ".claude" / "CLAUDE.md")
    run_hook(data, monkeypatch)
    assert spooled()[0]["payload"]["file_path"] == "~/.claude/CLAUDE.md"


def test_instructions_loaded_huge_file_not_hashed(ouro_home, monkeypatch, tmp_path):
    big = tmp_path / "big.md"
    big.write_bytes(b"\0" * (config.INSTRUCTIONS_HASH_MAX_BYTES + 1))
    data = load_fixture("InstructionsLoaded")
    data["cwd"] = str(tmp_path)
    data["file_path"] = "big.md"
    run_hook(data, monkeypatch)
    assert spooled()[0]["payload"]["content_sha256"] is None


# --- spool cap and nudge -----------------------------------------------------


def test_hook_spool_cap_drops_oldest(ouro_home, monkeypatch):
    monkeypatch.setenv("OUROBOROS_HOOK_SPOOL_CAP", "4")
    sid = str(uuid.uuid4())
    for i in range(7):
        run_hook(prompt_event(sid, f"p{i}", f"prompt {i}"), monkeypatch)
    records = spooled()
    assert len(records) == 4
    assert [r["prompt_id"] for r in records] == ["p3", "p4", "p5", "p6"]
    # the outcome spool is untouched / separate
    assert spool.list_files() == []
    assert spool.list_hook_files()[0].parent == config.hook_spool_dir()


def test_hook_nudges_sender_out_of_process(ouro_home, monkeypatch):
    monkeypatch.setenv("OUROBOROS_HOOK_NUDGE", "on")
    monkeypatch.setenv("OUROBOROS_API_URL", "http://127.0.0.1:1")
    calls = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            calls.append((cmd, kwargs))

    monkeypatch.setattr(hook.subprocess, "Popen", FakePopen)
    sid = str(uuid.uuid4())
    run_hook(prompt_event(sid), monkeypatch)
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd[1:] == ["-m", "ouroboros_client.sender"]
    assert kwargs["start_new_session"] is True
    assert kwargs["stdout"] == hook.subprocess.DEVNULL
    # a burst of hook runs within the interval spawns no second sender
    run_hook(stop_event(sid), monkeypatch)
    run_hook(prompt_event(sid, "p2"), monkeypatch)
    assert len(calls) == 1
    # ... but after the interval has passed, it nudges again
    marker = config.hook_spool_dir() / ".last_nudge"
    old = time.time() - config.HOOK_NUDGE_MIN_INTERVAL_SECONDS - 1
    os.utime(marker, (old, old))
    run_hook(prompt_event(sid, "p3"), monkeypatch)
    assert len(calls) == 2


def test_no_nudge_without_api_url(ouro_home, monkeypatch):
    monkeypatch.setenv("OUROBOROS_HOOK_NUDGE", "on")
    monkeypatch.delenv("OUROBOROS_API_URL", raising=False)
    calls = []
    monkeypatch.setattr(hook.subprocess, "Popen", lambda *a, **k: calls.append(a))
    run_hook(prompt_event(str(uuid.uuid4())), monkeypatch)
    assert calls == []
    assert len(spool.list_hook_files()) == 1  # still spooled for a later report
