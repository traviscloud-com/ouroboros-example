"""Transcript parser and opt-in uploader (design D8, task 2.6) against the
synthesized golden fixtures in fixtures/transcripts/."""

import json
import sys
from pathlib import Path

import pytest

from ouroboros_client import transcript as t

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
PROJECTS = FIXTURES  # the fixture dir plays ~/.claude/projects
PROJECT = FIXTURES / "-home-user-project"
SESSION = "11111111-1111-4111-8111-111111111111"
AGENT = "0123456789abcdef0"
SESSION_FILE = PROJECT / f"{SESSION}.jsonl"
AGENT_FILE = PROJECT / SESSION / "subagents" / f"agent-{AGENT}.jsonl"
P1 = "aaaaaaaa-0000-4000-8000-000000000001"
P2 = "aaaaaaaa-0000-4000-8000-000000000002"

sys.path.insert(0, str(FIXTURES))
import synthesize  # noqa: E402  (the fixture generator)


def uuid_n(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


# --- fixtures are synthetic and reproducible -----------------------------------------

def test_fixtures_match_the_synthesizer():
    for rel, content in synthesize.render().items():
        assert (PROJECT / rel).read_text() == content, rel


def test_redactor_preserves_length_and_removes_words():
    text = "Please run pytest on 3 files at /home/user/project"
    out = synthesize.redact(text)
    assert len(out) == len(text)
    assert "pytest" not in out and "0" in out and out.count(" ") == text.count(" ")


# --- parsing -----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def parsed():
    return t.parse_transcript(SESSION_FILE)


def test_parse_counts_every_line_class(parsed):
    s = parsed.stats
    assert s.total == 28
    assert s.emitted == 17 == len(parsed.lines)
    assert s.skipped_never == 2  # api-request-blob + file-history-snapshot
    assert s.skipped_side_channel == 8
    assert s.skipped_unknown == 1  # "future-thing"
    assert s.skipped_malformed == 0
    assert parsed.session_id == SESSION and parsed.agent_id is None
    assert parsed.versions == {"2.1.319"}


def test_unknown_keys_are_ignored(parsed):
    first = parsed.lines[0]
    assert first["type"] == "user" and first["subtype"] == "prompt"
    assert "futureUnknownKey" not in json.dumps(first)
    assert set(first) <= {
        "line_uuid", "parent_uuid", "seq", "type", "subtype", "prompt_id", "role", "model",
        "message_id", "request_id", "stop_reason", "timestamp", "usage", "tool_use_ids",
        "tool_names", "content_chars", "is_sidechain", "is_error", "attribution_skill", "version",
    }


def test_chain_order_not_timestamp_order(parsed):
    uuids = [l["line_uuid"] for l in parsed.lines]
    # the attachment is timestamped before its parent prompt but follows it in chain order
    assert uuids.index(uuid_n(2)) == uuids.index(uuid_n(1)) + 1
    # the second streaming line has an earlier timestamp than the first; chain order wins
    assert uuids.index(uuid_n(16)) == uuids.index(uuid_n(15)) + 1
    assert [l["seq"] for l in parsed.lines] == list(range(17))
    # the compact_boundary is a second root (parentUuid null) and keeps file position
    boundary = next(l for l in parsed.lines if l["subtype"] == "compact_boundary")
    assert boundary["parent_uuid"] is None and boundary["seq"] == 10


def test_assistant_usage_deduped_by_message_id(parsed):
    msg1 = [l for l in parsed.lines if l["message_id"] == "msg_" + "1".zfill(24)]
    assert len(msg1) == 3 and [l["subtype"] for l in msg1] == ["thinking", "text", "tool_use"]
    assert msg1[0]["usage"] == {
        "input_tokens": 32, "output_tokens": 1200, "cache_read_tokens": 180000,
        "cache_creation_tokens": 1500, "thinking_tokens": 400,
    }
    assert msg1[1]["usage"] is None and msg1[2]["usage"] is None
    assert parsed.stats.deduped_usage == 3
    total_out = sum(l["usage"]["output_tokens"] for l in parsed.lines if l["usage"])
    assert total_out == 1200 + 300 + 90 + 0 + 60


def test_prompt_id_inherited_along_the_chain(parsed):
    by_uuid = {l["line_uuid"]: l for l in parsed.lines}
    assert by_uuid[uuid_n(1)]["prompt_id"] == P1
    for n in (2, 3, 4, 5, 6, 7, 8, 9, 10):
        assert by_uuid[uuid_n(n)]["prompt_id"] == P1, n
    assert by_uuid[uuid_n(11)]["prompt_id"] is None  # compact_boundary: new root, no turn
    for n in (13, 14, 15, 16, 17):
        assert by_uuid[uuid_n(n)]["prompt_id"] == P2, n


def test_tool_use_ids_and_names_including_tool_results(parsed):
    by_uuid = {l["line_uuid"]: l for l in parsed.lines}
    assert by_uuid[uuid_n(5)]["tool_use_ids"] == ["toolu_" + "1".zfill(24)]
    assert by_uuid[uuid_n(5)]["tool_names"] == ["Bash"]
    assert by_uuid[uuid_n(5)]["attribution_skill"] == "run-tests"
    assert by_uuid[uuid_n(6)]["subtype"] == "tool_result"
    assert by_uuid[uuid_n(6)]["tool_names"] == ["Bash"] and by_uuid[uuid_n(6)]["is_error"] is False
    assert by_uuid[uuid_n(14)]["model"] == "<synthetic>" and by_uuid[uuid_n(14)]["is_error"] is True


def test_default_mode_carries_no_text(parsed):
    dump = json.dumps(parsed.lines)
    assert "content" not in {k for l in parsed.lines for k in l}
    assert "xxxx" not in dump  # the redacted texts never appear
    assert all(isinstance(l["content_chars"], int) for l in parsed.lines)
    assert parsed.lines[0]["content_chars"] == 56


def test_content_mode_includes_prompt_and_assistant_text_only():
    parsed = t.parse_transcript(SESSION_FILE, with_content=True)
    with_text = {l["line_uuid"]: l["content"] for l in parsed.lines if "content" in l}
    assert uuid_n(1) in with_text and uuid_n(4) in with_text and uuid_n(13) in with_text
    # tool results, tool_use inputs, attachments and system lines carry no content
    for n in (2, 5, 6, 7, 8, 10, 11, 16, 17):
        assert uuid_n(n) not in with_text, n
    # a thinking signature is never content
    assert "SSSS" not in json.dumps(parsed.lines)


def test_subagent_transcript_sets_agent_id_and_sidechain():
    parsed = t.parse_transcript(AGENT_FILE)
    assert parsed.agent_id == AGENT and parsed.session_id == SESSION
    assert parsed.stats.skipped_never == 1 and parsed.stats.emitted == 7
    assert all(l["is_sidechain"] for l in parsed.lines)
    assert all(l["prompt_id"] == P1 for l in parsed.lines)  # the parent turn's prompt id
    assert parsed.lines[-1]["type"] == "user"  # ends on the handback tool result
    assert parsed.transcript_hash and len(parsed.transcript_hash) == 64
    assert parsed.stats.deduped_usage == 1
    assert t.subagent_files(SESSION_FILE) == [AGENT_FILE]


# --- never-list and memory/ ----------------------------------------------------------

def test_never_list_lines_are_dropped_before_json_decoding(monkeypatch):
    seen: list[str] = []
    real_loads = json.loads

    def spy(raw, *a, **kw):
        seen.append(raw)
        return real_loads(raw, *a, **kw)

    monkeypatch.setattr(t.json, "loads", spy)
    t.parse_transcript(SESSION_FILE)
    assert seen and not any("api-request-blob" in raw for raw in seen)
    assert not any("file-history" in raw for raw in seen)


def test_never_list_paths_are_refused():
    with pytest.raises(t.NeverListError):
        t.parse_transcript(PROJECT / "memory" / "notes.md")
    with pytest.raises(t.NeverListError):
        t.parse_transcript(PROJECT / SESSION / "tool-results" / "toolu_000000000000000000000001.txt")
    with pytest.raises(t.NeverListError):
        t.check_path_allowed(Path("/anywhere/api-request-blob-1.jsonl"))


def test_parser_never_opens_side_files(monkeypatch):
    opened: list[str] = []
    real_open = Path.open

    def spy(self, *a, **kw):
        opened.append(str(self))
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", spy)
    for path in [SESSION_FILE] + t.subagent_files(SESSION_FILE):
        t.parse_transcript(path, with_content=True)
    assert opened and not any("tool-results" in p or "/memory/" in p for p in opened)


def test_discovery_excludes_memory_and_tool_results():
    found = t.discover_sessions(PROJECTS)
    assert found == [SESSION_FILE]
    assert t.find_session(PROJECTS, SESSION) == SESSION_FILE
    assert t.find_session(PROJECTS, "no-such") is None


# --- retention bound ---------------------------------------------------------------------

def test_backfill_days_bounded_by_cleanup_period(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"cleanupPeriodDays": 14, "env": {"X": "1"}}))
    assert t.local_retention_days(settings) == 14
    assert t.bounded_days(90, settings) == 14
    assert t.bounded_days(7, settings) == 7
    assert t.local_retention_days(tmp_path / "missing.json") == 30
    settings.write_text("not json")
    assert t.bounded_days(90, settings) == 30


# --- upload ---------------------------------------------------------------------------------

def _uploader(server):
    return t.Uploader(api_url=server.url, token_provider=lambda: "test-token")


def test_upload_batches_and_auth(server):
    parsed = t.parse_transcript(SESSION_FILE)
    parsed.lines = [dict(l, line_uuid=f"{l['line_uuid']}-{i}") for i in range(80) for l in parsed.lines]
    assert len(parsed.lines) == 1360
    totals = _uploader(server).upload(parsed, content_included=False)
    assert totals["batches"] == 3 and totals["lines"] == 1360
    sizes = [len(r["body"]["lines"]) for r in server.state.requests]
    assert sizes == [500, 500, 360]
    first = server.state.requests[0]
    assert first["headers"]["Authorization"] == "Bearer test-token"
    assert first["body"]["session_id"] == SESSION
    assert first["body"]["content_included"] is False
    assert "agent_id" not in first["body"]
    assert first["body"]["client"]["name"] == "ouroboros-client"


def test_subagent_upload_carries_agent_id_and_hash(server):
    parsed = t.parse_transcript(AGENT_FILE)
    _uploader(server).upload(parsed, content_included=False)
    body = server.state.requests[0]["body"]
    assert body["agent_id"] == AGENT and body["transcript_hash"] == parsed.transcript_hash


def test_upload_failure_raises(server):
    server.state.status = 500
    with pytest.raises(RuntimeError, match="HTTP 500"):
        _uploader(server).upload(t.parse_transcript(AGENT_FILE), content_included=False)


# --- CLI -----------------------------------------------------------------------------------------

@pytest.fixture()
def cli_env(server, monkeypatch, tmp_path):
    monkeypatch.setenv("OUROBOROS_API_URL", server.url)
    real_uploader = t.Uploader
    monkeypatch.setattr(
        t, "Uploader", lambda *a, **kw: real_uploader(api_url=server.url, token_provider=lambda: "tok")
    )
    return server


def test_cli_upload_metadata_only(cli_env, capsys):
    assert t.main(["upload", str(SESSION_FILE)]) == 0
    bodies = [r["body"] for r in cli_env.state.requests]
    assert len(bodies) == 2  # session + one subagent file
    assert all(b["content_included"] is False for b in bodies)
    assert all("content" not in l for b in bodies for l in b["lines"])
    out = json.loads(capsys.readouterr().out)
    assert out[0]["stats"]["emitted"] == 17 and out[1]["agent_id"] == AGENT


def test_cli_upload_by_session_id(cli_env):
    assert t.main(["upload", "--session", SESSION, "--projects-dir", str(PROJECTS)]) == 0
    assert len(cli_env.state.requests) == 2
    assert t.main(["upload", "--session", "missing", "--projects-dir", str(PROJECTS)]) == 1


def test_cli_content_requires_consent(cli_env, capsys):
    assert t.main(["upload", str(SESSION_FILE), "--content"]) == 2
    assert cli_env.state.requests == []
    assert "consent" in capsys.readouterr().err.lower()


def test_cli_consent_then_content_upload(cli_env, ouro_home):
    assert t.main(["consent", "--yes"]) == 0
    consent = json.loads((ouro_home / "consent.json").read_text())
    assert consent["transcript_content"] is True and "granted_at" in consent
    assert t.main(["upload", str(SESSION_FILE), "--content"]) == 0
    body = cli_env.state.requests[0]["body"]
    assert body["content_included"] is True
    assert any("content" in l for l in body["lines"])


def test_cli_consent_declined_records_nothing(cli_env, ouro_home, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "no")
    assert t.main(["consent"]) == 2
    assert not (ouro_home / "consent.json").exists()


def test_cli_telemetry_off_uploads_nothing(cli_env, monkeypatch):
    monkeypatch.setenv("OUROBOROS_TELEMETRY", "off")
    assert t.main(["upload", str(SESSION_FILE)]) == 0
    assert t.main(["backfill", "--days", "7"]) == 0
    assert cli_env.state.requests == []


def test_cli_refuses_never_list_paths(cli_env):
    assert t.main(["upload", str(PROJECT / "memory" / "notes.md")]) == 1
    assert cli_env.state.requests == []


def test_cli_backfill_bounded_and_filtered(cli_env, monkeypatch, tmp_path, capsys):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"cleanupPeriodDays": 30}))
    monkeypatch.setattr(t, "local_retention_days", lambda *_: 30)
    # the fixture session is dated 2026-11-08: old relative to "now"; pin now near it
    from datetime import datetime, timezone

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 10, tzinfo=timezone.utc)

    monkeypatch.setattr(t, "datetime", FakeDT)
    assert t.main(["backfill", "--days", "90", "--projects-dir", str(PROJECTS)]) == 0
    err = capsys.readouterr().err
    assert "bounded to 30" in err
    assert len(cli_env.state.requests) == 2  # session + subagent
    # a window that ends before the session: nothing uploaded
    cli_env.state.requests.clear()
    monkeypatch.setattr(t, "local_retention_days", lambda *_: 1)
    assert t.main(["backfill", "--days", "1", "--projects-dir", str(PROJECTS)]) == 0
    assert cli_env.state.requests == []


def test_cli_watch_is_a_stub(capsys):
    assert t.main(["watch"]) == 2
    assert "UserPromptSubmit" in capsys.readouterr().err
