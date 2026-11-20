"""POST /v1/telemetry/transcripts (design D8/D9, task 2.6): auth, idempotency on
(session_id, line_uuid), fill-nulls-only convergence into turns and
subagent_runs, and content excluded unless the batch opts in."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

PROMPT = "aaaaaaaa-0000-4000-8000-000000000001"
AGENT = "0123456789abcdef0"
T0 = datetime(2026, 11, 8, 11, 6, 40, tzinfo=timezone.utc)


def ts(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def line(n: int, kind: str, *, parent: int | None, when: float, **extra) -> dict:
    rec = {
        "line_uuid": f"00000000-0000-4000-8000-{n:012d}",
        "parent_uuid": f"00000000-0000-4000-8000-{parent:012d}" if parent else None,
        "seq": n - 1,
        "type": kind,
        "timestamp": ts(when),
        "prompt_id": PROMPT,
        "version": "2.1.319",
    }
    rec.update(extra)
    return rec


def usage(out: int) -> dict:
    return {"input_tokens": 32, "output_tokens": out, "cache_read_tokens": 1000, "cache_creation_tokens": 10}


def session_lines() -> list[dict]:
    return [
        line(1, "user", parent=None, when=0, subtype="prompt", role="user", content_chars=56,
             content="the prompt text"),
        line(2, "attachment", parent=1, when=-0.4, subtype="date", content_chars=27),
        line(3, "assistant", parent=2, when=1.0, subtype="thinking", role="assistant", model="claude-fable-5-1",
             message_id="msg_1", usage=usage(1200), content_chars=0),
        line(4, "assistant", parent=3, when=1.1, subtype="text", role="assistant", model="claude-fable-5-1",
             message_id="msg_1", content_chars=55, content="reply text"),
        line(5, "assistant", parent=4, when=1.2, subtype="tool_use", role="assistant", model="claude-fable-5-1",
             message_id="msg_1", tool_use_ids=["toolu_1"], tool_names=["Bash"], content_chars=60),
        line(6, "user", parent=5, when=3.0, subtype="tool_result", role="user", tool_use_ids=["toolu_1"],
             tool_names=["Bash"], is_error=False, content_chars=17),
        # synthetic error line: must not become the turn's model
        line(7, "assistant", parent=6, when=4.0, subtype="text", role="assistant", model="<synthetic>",
             message_id="err", is_error=True, usage={"input_tokens": 0, "output_tokens": 0}, content_chars=25),
        # final assistant line has an EARLIER timestamp than line 7 (clock step-back); chain order decides
        line(8, "assistant", parent=7, when=3.9, subtype="text", role="assistant", model="claude-fable-5-1",
             message_id="msg_2", usage=usage(90), stop_reason="end_turn", content_chars=62),
        line(9, "system", parent=8, when=5.0, subtype="turn_duration", content_chars=0),
    ]


def batch(session_id: str, lines: list[dict], **extra) -> dict:
    body = {
        "session_id": session_id,
        "lines": lines,
        "content_included": False,
        "client": {"name": "ouroboros-client", "version": "0.1.0"},
    }
    body.update(extra)
    return body


def post(client, token, body):
    return client.post("/v1/telemetry/transcripts", json=body, headers={"Authorization": f"Bearer {token}"})


def rows(engine, table, session_id):
    with engine.connect() as conn:
        return conn.execute(
            text(f"SELECT * FROM {table} WHERE session_id = :sid ORDER BY received_at, seq NULLS LAST")
            if table == "transcript_lines"
            else text(f"SELECT * FROM {table} WHERE session_id = :sid"),
            {"sid": session_id},
        ).mappings().all()


# --- auth and validation ---------------------------------------------------------------

def test_requires_bearer(client):
    resp = client.post("/v1/telemetry/transcripts", json=batch(str(uuid.uuid4()), session_lines()))
    assert resp.status_code == 401
    resp = client.post(
        "/v1/telemetry/transcripts",
        json=batch(str(uuid.uuid4()), session_lines()),
        headers={"Authorization": "Bearer not-a-token"},
    )
    assert resp.status_code == 401


def test_rejects_oversized_or_unknown_type(client, token):
    sid = str(uuid.uuid4())
    too_many = [line(i, "system", parent=None, when=i, subtype="x") for i in range(1, 502)]
    assert post(client, token, batch(sid, too_many)).status_code == 422
    bad = [line(1, "api-request-blob", parent=None, when=0)]
    assert post(client, token, batch(sid, bad)).status_code == 422
    assert post(client, token, batch(sid, [])).status_code == 422


# --- storage and idempotency ------------------------------------------------------------

def test_lines_stored_and_replay_reports_duplicates(client, token, engine):
    sid = str(uuid.uuid4())
    resp = post(client, token, batch(sid, session_lines()))
    assert resp.status_code == 202
    assert resp.json() == {"accepted": 9, "duplicates": 0}

    stored = rows(engine, "transcript_lines", sid)
    assert len(stored) == 9
    by_uuid = {r["line_uuid"][-1]: r for r in stored}
    assert by_uuid["3"]["output_tokens"] == 1200 and by_uuid["4"]["output_tokens"] is None
    assert by_uuid["5"]["tool_use_ids"] == ["toolu_1"] and by_uuid["5"]["tool_names"] == ["Bash"]
    assert by_uuid["8"]["stop_reason"] == "end_turn" and by_uuid["8"]["seq"] == 7
    assert all(r["prompt_id"] == PROMPT for r in stored)
    assert all(r["auth_upn"] == "dev@ouroboros.local" for r in stored)
    assert all(r["is_sidechain"] is False and r["agent_id"] is None for r in stored)
    assert "content" not in by_uuid["1"]["raw"]  # raw never carries text

    # the stub session row exists so the FK holds in any arrival order
    assert len(rows(engine, "sessions", sid)) == 1

    # replaying the batch twice changes nothing
    resp = post(client, token, batch(sid, session_lines()))
    assert resp.status_code == 202 and resp.json() == {"accepted": 0, "duplicates": 9}
    partial = session_lines()[:3] + [line(10, "system", parent=9, when=6, subtype="stop_hook_summary")]
    resp = post(client, token, batch(sid, partial))
    assert resp.json() == {"accepted": 1, "duplicates": 3}
    assert len(rows(engine, "transcript_lines", sid)) == 10


# --- content gating -----------------------------------------------------------------------

def test_content_excluded_unless_batch_opts_in(client, token, engine):
    sid = str(uuid.uuid4())
    # lines carry a content field, but the batch does not declare content_included
    assert post(client, token, batch(sid, session_lines())).status_code == 202
    stored = {r["line_uuid"][-1]: r for r in rows(engine, "transcript_lines", sid)}
    assert stored["1"]["content"] is None and stored["4"]["content"] is None
    assert stored["1"]["content_chars"] == 56

    # a later opt-in upload fills the null content of the same lines
    assert post(client, token, batch(sid, session_lines(), content_included=True)).status_code == 202
    stored = {r["line_uuid"][-1]: r for r in rows(engine, "transcript_lines", sid)}
    assert stored["1"]["content"] == "the prompt text" and stored["4"]["content"] == "reply text"
    assert stored["3"]["content"] is None


# --- convergence into turns ---------------------------------------------------------------

def test_turn_created_from_transcript_when_absent(client, token, engine):
    sid = str(uuid.uuid4())
    prompt = str(uuid.uuid4())
    lines = [dict(l, prompt_id=prompt) for l in session_lines()]
    assert post(client, token, batch(sid, lines)).status_code == 202
    with engine.connect() as conn:
        turn = conn.execute(text("SELECT * FROM turns WHERE prompt_id = :p"), {"p": prompt}).mappings().one()
    assert str(turn["session_id"]) == sid
    assert turn["model"] == "claude-fable-5-1"  # the <synthetic> error line is skipped
    assert turn["response_length"] == 0 + 55 + 25 + 62  # text/thinking chars, tool_use excluded
    assert turn["prompt_length"] == 56
    assert turn["submitted_at"] == T0
    # stopped_at follows chain order (line 8), not the latest timestamp (line 7)
    assert turn["stopped_at"] == T0 + timedelta(seconds=3.9)
    assert turn["raw"]["transcript"]["assistant_lines"] == 5


def test_turn_from_hooks_keeps_its_values(client, token, engine):
    sid = str(uuid.uuid4())
    prompt = str(uuid.uuid4())
    hook_submitted = T0 - timedelta(seconds=1)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO sessions (session_id, auth_oid, auth_upn) VALUES (:sid, 'oid', 'u')"),
            {"sid": sid},
        )
        conn.execute(
            text(
                "INSERT INTO turns (session_id, prompt_id, seq, submitted_at, model, prompt_length, auth_oid, auth_upn) "
                "VALUES (:sid, :p, 1, :at, 'hook-model', 999, 'oid', 'u')"
            ),
            {"sid": sid, "p": prompt, "at": hook_submitted},
        )
    lines = [dict(l, prompt_id=prompt) for l in session_lines()]
    assert post(client, token, batch(sid, lines)).status_code == 202
    with engine.connect() as conn:
        turn = conn.execute(text("SELECT * FROM turns WHERE prompt_id = :p"), {"p": prompt}).mappings().one()
    # existing values untouched, nulls filled
    assert turn["model"] == "hook-model" and turn["prompt_length"] == 999 and turn["seq"] == 1
    assert turn["submitted_at"] == hook_submitted
    assert turn["response_length"] == 142 and turn["stopped_at"] == T0 + timedelta(seconds=3.9)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM turns WHERE prompt_id = :p"), {"p": prompt}).scalar_one() == 1


# --- convergence into subagent_runs -----------------------------------------------------

def subagent_lines() -> list[dict]:
    return [
        line(1, "user", parent=None, when=4.0, subtype="prompt", role="user", is_sidechain=True, content_chars=36),
        line(2, "assistant", parent=1, when=5.0, subtype="text", role="assistant", model="claude-haiku-5-5",
             message_id="msg_100", usage=usage(800), is_sidechain=True, content_chars=34),
        line(3, "assistant", parent=2, when=5.1, subtype="tool_use", role="assistant", model="claude-haiku-5-5",
             message_id="msg_100", tool_use_ids=["toolu_100"], tool_names=["Write"], is_sidechain=True,
             content_chars=63),
        line(4, "user", parent=3, when=6.0, subtype="tool_result", role="user", tool_use_ids=["toolu_100"],
             tool_names=["Write"], is_sidechain=True, content_chars=13),
        line(5, "assistant", parent=4, when=7.0, subtype="tool_use", role="assistant", model="claude-haiku-5-5",
             message_id="msg_101", usage=usage(60), tool_use_ids=["toolu_101"], tool_names=["SubagentHandback"],
             is_sidechain=True, content_chars=67),
        line(6, "user", parent=5, when=8.0, subtype="tool_result", role="user", tool_use_ids=["toolu_101"],
             is_sidechain=True, content_chars=19),
    ]


def test_subagent_batch_converges_into_subagent_runs(client, token, engine):
    sid = str(uuid.uuid4())
    agent = uuid.uuid4().hex[:17]
    body = batch(sid, subagent_lines(), agent_id=agent, transcript_hash="ab" * 32)
    assert post(client, token, body).status_code == 202

    stored = rows(engine, "transcript_lines", sid)
    assert len(stored) == 6 and all(r["agent_id"] == agent and r["is_sidechain"] for r in stored)

    with engine.connect() as conn:
        run = conn.execute(text("SELECT * FROM subagent_runs WHERE agent_id = :a"), {"a": agent}).mappings().one()
    assert str(run["session_id"]) == sid and run["prompt_id"] == PROMPT
    assert run["total_tokens"] == (32 + 800 + 1000 + 10) + (32 + 60 + 1000 + 10)
    assert run["total_tool_uses"] == 2
    assert run["transcript_hash"] == "ab" * 32
    assert run["started_at"] == T0 + timedelta(seconds=4) and run["stopped_at"] == T0 + timedelta(seconds=8)
    # subagent lines never create or alter a turn for the parent's prompt id
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT count(*) FROM turns WHERE session_id = :sid"), {"sid": sid}
        ).scalar_one() == 0

    # a hook-created run keeps its agent_type and totals; the transcript only fills nulls
    agent2 = uuid.uuid4().hex[:17]
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO subagent_runs (agent_id, session_id, agent_type, total_tokens, auth_oid, auth_upn) "
                "VALUES (:a, :sid, 'general-purpose', 50321, 'oid', 'u')"
            ),
            {"a": agent2, "sid": sid},
        )
    assert post(client, token, batch(sid, subagent_lines(), agent_id=agent2)).status_code == 202
    with engine.connect() as conn:
        run = conn.execute(text("SELECT * FROM subagent_runs WHERE agent_id = :a"), {"a": agent2}).mappings().one()
    assert run["agent_type"] == "general-purpose" and run["total_tokens"] == 50321
    assert run["total_tool_uses"] == 2 and run["transcript_hash"] is None
