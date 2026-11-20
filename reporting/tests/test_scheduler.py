"""Scheduler (design D13) and retention (design D11): matview refresh,
persisted sentiment/themes, nightly retention nulling, audit rows."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app import scheduler as sched
from app.scheduler import Scheduler, list_matviews, refresh_matviews, run_retention
from app.sentiment import SentimentScorer

from .conftest import ADMIN_EMAIL, make_test_settings, seed_event
from .seeds import (
    seed_artifact, seed_compaction, seed_hook_event, seed_session, seed_tool_call, seed_transcript_line,
    seed_turn, seed_turn_error, ts,
)

NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=40)       # past the 30-day sensitive clock
ANCIENT = NOW - timedelta(days=400)  # past the 365-day internal clock
FRESH = NOW - timedelta(days=2)


def test_refresh_matviews_concurrently_when_populated(engine):
    seed_event(engine, at=ts(2026, 11, 3), status="failure")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM analytics.outcomes_daily")).scalar_one() == 0
    results = refresh_matviews(engine)
    assert {r["view"] for r in results} == {
        "analytics.adoption_daily", "analytics.adoption_weekly", "analytics.outcomes_daily",
        "analytics.error_codes_daily", "analytics.latency_daily", "analytics.cost_daily",
        "analytics.cost_per_session", "analytics.context_bloat_daily",
    }
    assert all(r["concurrently"] for r in results)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT day, failure FROM analytics.outcomes_daily")).one()
    assert (row.day.isoformat(), row.failure) == ("2026-11-03", 1)


def test_refresh_falls_back_to_plain_for_unpopulated_view(engine):
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("CREATE MATERIALIZED VIEW analytics.zz_probe AS SELECT 1 AS one WITH NO DATA"))
        conn.execute(text("CREATE UNIQUE INDEX uq_zz_probe ON analytics.zz_probe (one)"))
    try:
        probe = [v for v in list_matviews(engine) if v["name"] == "zz_probe"][0]
        assert probe["ispopulated"] is False and probe["has_unique_index"] is True
        first = [r for r in refresh_matviews(engine) if r["view"].endswith("zz_probe")][0]
        assert first["concurrently"] is False
        second = [r for r in refresh_matviews(engine) if r["view"].endswith("zz_probe")][0]
        assert second["concurrently"] is True
    finally:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("DROP MATERIALIZED VIEW analytics.zz_probe"))


def test_tick_persists_sentiment_and_themes_and_bootstraps(pg_url, engine):
    seed_event(engine, at=ts(2026, 11, 3), status="user_corrected",
               user_corrections="use the primary key index not a full scan")
    seed_event(engine, at=ts(2026, 11, 3), notes="works great")
    settings = make_test_settings(pg_url, admin_emails=[ADMIN_EMAIL])
    s = Scheduler(settings, lambda: engine, SentimentScorer(settings))
    result = s.tick_sync()
    assert result["bootstrap_admins"] == 1 and len(result["matviews"]) == 8
    assert result["sentiment"] == {"events_scored": 2, "rows_written": 1, "since": None}
    assert result["themes"]["themes_written"] >= 2
    assert "retention" in result  # never ran before: due
    with engine.connect() as conn:
        day = conn.execute(text("SELECT * FROM analytics.sentiment_daily")).mappings().one()
        assert day["event_count"] == 2 and day["text_count"] == 2 and float(day["text_coverage"]) == 1.0
        assert float(day["behavioral_sentiment"]) == pytest.approx((0.3 - 0.6) / 2)
        assert day["text_sentiment"] is not None
        themes = conn.execute(text("SELECT theme, count, example_event_ids FROM analytics.correction_themes")).mappings().all()
        assert {t["theme"] for t in themes} >= {"primary key", "key index", "full scan"}
        assert all(len(t["example_event_ids"]) == 1 for t in themes)
        assert conn.execute(text("SELECT role FROM principals WHERE email = :e"), {"e": ADMIN_EMAIL}).scalar_one() == "admin"
    # second tick: incremental, retention not due again
    result2 = s.tick_sync()
    assert "bootstrap_admins" not in result2 and "retention" not in result2
    assert result2["sentiment"]["since"] is not None


def test_positive_corrections_are_not_themes(pg_url, engine):
    seed_event(engine, at=ts(2026, 11, 3), status="success",
               user_corrections="wonderful excellent perfect brilliant fantastic work")
    settings = make_test_settings(pg_url)
    from app import themes
    assert themes.persist(engine, SentimentScorer(settings), recompute_days=35)["themes_written"] == 0


def test_retention_nulls_sensitive_and_deletes_internal(pg_url, engine):
    settings = make_test_settings(pg_url)
    old_sid = seed_session(engine, started_at=OLD, title="old title",
                           raw={"session_title": "old title", "source": "startup"})
    new_sid = seed_session(engine, started_at=FRESH, title="new title")
    old_pid = seed_turn(engine, old_sid, submitted_at=OLD, prompt_text="old prompt", response_text="old answer",
                        raw={"prompt": "old prompt", "prompt_length": 10})
    new_pid = seed_turn(engine, new_sid, submitted_at=FRESH, prompt_text="new prompt", response_text="new answer")
    seed_tool_call(engine, old_sid, prompt_id=old_pid, started_at=OLD, input_content="in", output_content="out")
    seed_tool_call(engine, new_sid, prompt_id=new_pid, started_at=FRESH, input_content="in", output_content="out")
    seed_compaction(engine, old_sid, at=OLD, summary_text="summary")
    seed_turn_error(engine, old_sid, at=OLD, message="stack trace")
    seed_transcript_line(engine, old_sid, timestamp=OLD, content="transcript text")
    seed_transcript_line(engine, new_sid, timestamp=FRESH, content="fresh transcript")
    old_event = seed_event(engine, at=OLD, session_id=old_sid, notes="old notes", user_corrections="fix it",
                           user_prompt="old prompt")
    new_event = seed_event(engine, at=FRESH, session_id=new_sid, notes="new notes")
    old_hook = seed_hook_event(engine, old_sid, client_ts=OLD, payload={"prompt": "old prompt", "prompt_length": 10,
                                                                        "tool_input": {"cmd": "ls"}})
    new_hook = seed_hook_event(engine, new_sid, client_ts=FRESH, payload={"prompt": "new prompt"})
    ancient_hook = seed_hook_event(engine, old_sid, client_ts=ANCIENT, payload={"prompt_length": 3})
    seed_transcript_line(engine, old_sid, timestamp=ANCIENT, content=None)
    seed_turn_error(engine, old_sid, at=ANCIENT, message=None)
    _, h = seed_artifact(engine, kind="skill", name="Retired", content_text="retired body",
                         seen_from=ANCIENT - timedelta(days=5), seen_until=ANCIENT)
    seed_artifact(engine, kind="skill", name="Current", content_text="current body")

    details = run_retention(engine, settings, now=NOW)
    assert details["retention_sensitive_days"] == 30 and details["retention_internal_days"] == 365
    c = details["counts"]
    assert c["sensitive"]["turns.text"] == 1 and c["sensitive"]["events.feedback_interaction"] == 1
    assert c["sensitive"]["hook_events_raw.payload"] == 1 and c["sensitive"]["transcript_lines.content"] == 1
    assert c["sensitive"]["sessions.raw"] == 1 and c["sensitive"]["turns.raw"] == 1
    assert c["internal"]["hook_events_raw"] == 1 and c["internal"]["transcript_lines"] == 1
    assert c["internal"]["turn_errors"] == 1 and c["internal"]["artifact_versions.content_text"] == 1

    with engine.connect() as conn:
        q = lambda sql, **p: conn.execute(text(sql), p).one()  # noqa: E731
        assert q("SELECT prompt_text, response_text, prompt_length, raw FROM turns WHERE prompt_id = :p", p=old_pid) == (
            None, None, 10, {"prompt_length": 10, "retention_redacted": True})
        assert q("SELECT prompt_text FROM turns WHERE prompt_id = :p", p=new_pid) == ("new prompt",)
        assert q("SELECT input_content, output_content FROM tool_calls WHERE session_id = :s", s=old_sid) == (None, None)
        assert q("SELECT input_content FROM tool_calls WHERE session_id = :s", s=new_sid) == ("in",)
        assert q("SELECT summary_text, summary_length FROM compactions WHERE session_id = :s", s=old_sid)[0] is None
        assert q("SELECT title_redacted, raw FROM sessions WHERE session_id = :s", s=old_sid) == (
            None, {"source": "startup", "retention_redacted": True})
        assert q("SELECT title_redacted FROM sessions WHERE session_id = :s", s=new_sid) == ("new title",)
        assert q("SELECT content, content_chars FROM transcript_lines WHERE session_id = :s AND timestamp = :t",
                 s=old_sid, t=OLD) == (None, 15)
        assert q("SELECT content FROM transcript_lines WHERE session_id = :s", s=new_sid) == ("fresh transcript",)
        assert q("SELECT feedback, interaction FROM events WHERE event_id = :e", e=old_event) == (
            {"retention_redacted": True}, {"retention_redacted": True})
        assert q("SELECT feedback FROM events WHERE event_id = :e", e=new_event) == ({"notes": "new notes"},)
        assert q("SELECT payload FROM hook_events_raw WHERE hook_event_id = :h", h=old_hook) == (
            {"prompt_length": 10, "retention_redacted": True},)
        assert q("SELECT payload FROM hook_events_raw WHERE hook_event_id = :h", h=new_hook) == ({"prompt": "new prompt"},)
        assert conn.execute(text("SELECT count(*) FROM hook_events_raw WHERE hook_event_id = :h"),
                            {"h": ancient_hook}).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM transcript_lines")).scalar_one() == 2
        assert conn.execute(text("SELECT count(*) FROM turn_errors")).scalar_one() == 1
        assert q("SELECT message FROM turn_errors") == (None,)
        assert q("SELECT content_text FROM artifact_versions WHERE content_hash = :h", h=h) == (None,)
        assert conn.execute(text("SELECT count(*) FROM artifact_versions WHERE content_text IS NOT NULL")).scalar_one() == 1
        # the facts themselves survive (aggregates recompute from them)
        assert conn.execute(text("SELECT count(*) FROM turns")).scalar_one() == 2
        assert conn.execute(text("SELECT count(*) FROM events")).scalar_one() == 2
        audit = conn.execute(text("SELECT actor_email, action, details FROM audit_log")).one()
    assert (audit.actor_email, audit.action) == ("system", "retention.run")
    assert audit.details["counts"] == c
    # FTS columns followed the nulling
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM turns WHERE prompt_tsv @@ to_tsquery('old')")).scalar_one() == 0


def test_retention_due_tracks_audit_log(pg_url, engine):
    settings = make_test_settings(pg_url)
    s = Scheduler(settings, lambda: engine, SentimentScorer(settings))
    assert s.retention_due(NOW) is True
    run_retention(engine, settings, now=NOW)
    s2 = Scheduler(settings, lambda: engine, SentimentScorer(settings))
    assert s2.retention_due(datetime.now(timezone.utc)) is False
    assert s2.retention_due(datetime.now(timezone.utc) + timedelta(days=1, seconds=1)) is True


def test_ticker_disabled_when_interval_zero_and_runs_when_set(pg_url, engine):
    settings = make_test_settings(pg_url, aggregate_refresh_seconds=0)
    s = Scheduler(settings, lambda: engine, SentimentScorer(settings))

    async def go():
        s.start()
        assert s._task is None

    asyncio.run(go())

    settings = make_test_settings(pg_url, aggregate_refresh_seconds=3600)
    s = Scheduler(settings, lambda: engine, SentimentScorer(settings))

    async def go2():
        s.start()
        assert s._task is not None
        for _ in range(200):
            if s.last_result is not None:
                break
            await asyncio.sleep(0.05)
        await s.stop()
        assert s._task is None

    asyncio.run(go2())
    assert s.last_result is not None and len(s.last_result["matviews"]) == 8


def test_internal_refresh_route_returns_tick(client, engine):
    seed_event(engine, at=ts(2026, 11, 3))
    body = client.post("/internal/refresh", params={"retention": "true"}).json()
    assert body["trigger"] == "manual" and len(body["matviews"]) == 8 and "retention" in body
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM analytics.outcomes_daily")).scalar_one() == 1
        actions = conn.execute(text("SELECT action FROM audit_log ORDER BY id")).scalars().all()
    assert actions == ["retention.run", "refresh.manual"]


def test_payload_keys_cover_client_whitelist():
    assert {"prompt", "last_assistant_message", "compact_summary", "tool_input", "tool_response",
            "session_title"} <= set(sched.PAYLOAD_TEXT_KEYS)
