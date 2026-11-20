"""Analytics routes over the materialized views (design D13, task 3.4):
event-time bucketing, period comparison, per-route shapes, legacy report
switch to client_ts + FTS."""

import pytest
from sqlalchemy import text

from app import themes
from app.scheduler import refresh_matviews
from app.sentiment import SentimentScorer

from .conftest import make_test_settings, seed_event
from .seeds import (
    OID, seed_artifact, seed_compaction, seed_instruction_load, seed_session, seed_skill_activation,
    seed_subagent_run, seed_tool_call, seed_turn, seed_usage, ts,
)

WEEK = {"from": "2026-11-02", "to": "2026-11-08", "compare_to": "previous"}


@pytest.fixture()
def seeded(engine):
    """Two weeks of outcomes: Nov 2-8 (current) and Oct 26-Nov 1 (previous)."""
    sid_a = seed_session(engine, started_at=ts(2026, 11, 3), project_key="alpha", oid="oid-a")
    sid_b = seed_session(engine, started_at=ts(2026, 11, 5), project_key="alpha", oid="oid-b")
    sid_old = seed_session(engine, started_at=ts(2026, 10, 27), project_key="alpha", oid="oid-a")
    # current week: 3 success, 1 user_corrected, 1 failure w/ error code
    for i in range(3):
        seed_event(engine, at=ts(2026, 11, 3, 9 + i), session_id=sid_a, oid="oid-a", latency_ms=100 + i * 100)
    seed_event(engine, at=ts(2026, 11, 5), session_id=sid_b, oid="oid-b", status="user_corrected",
               user_corrections="use the primary key index not a full scan", latency_ms=900)
    seed_event(engine, at=ts(2026, 11, 6), session_id=sid_b, oid="oid-b", status="failure", error_code="E42",
               latency_ms=50)
    # Monday event uploaded on Wednesday: must land on Monday (event time, D17)
    seed_event(engine, at=ts(2026, 11, 2, 8), received_at=ts(2026, 11, 4, 8), session_id=sid_a, oid="oid-a")
    # previous week: 2 success
    seed_event(engine, at=ts(2026, 10, 27), session_id=sid_old, oid="oid-a")
    seed_event(engine, at=ts(2026, 10, 29), session_id=sid_old, oid="oid-a", status="user_corrected",
               user_corrections="primary key index please, not a scan")
    # cost + tools + skills + agents + compaction + instructions
    pid = seed_turn(engine, sid_a, submitted_at=ts(2026, 11, 3, 9), prompt_text="hi")
    seed_turn(engine, sid_a, seq=2, submitted_at=ts(2026, 11, 3, 10), prompt_text="again")
    seed_usage(engine, sid_a, prompt_id=pid, at=ts(2026, 11, 3, 9, 1), cost_usd=0.5, input_tokens=1000,
               output_tokens=100, skill_name="SQL_Generator")
    seed_usage(engine, sid_b, at=ts(2026, 11, 5, 9), cost_usd=0.25, model="claude-opus-4-1", input_tokens=400,
               output_tokens=40)
    seed_usage(engine, sid_old, at=ts(2026, 10, 27, 9), cost_usd=1.0)
    seed_tool_call(engine, sid_a, prompt_id=pid, tool_name="Read", started_at=ts(2026, 11, 3, 9, 2), duration_ms=20)
    seed_tool_call(engine, sid_a, prompt_id=pid, tool_name="Read", started_at=ts(2026, 11, 3, 9, 3), duration_ms=40)
    seed_skill_activation(engine, sid_a, prompt_id=pid, at=ts(2026, 11, 3, 9), skill_name="SQL_Generator",
                          oid="oid-a")
    seed_subagent_run(engine, sid_a, prompt_id=pid, agent_type="Explore", started_at=ts(2026, 11, 3, 9, 5),
                      oid="oid-a")
    seed_compaction(engine, sid_a, prompt_id=pid, at=ts(2026, 11, 3, 11), pre_tokens=150000)
    aid, h = seed_artifact(engine, kind="instructions", name="CLAUDE.md", path=".claude/CLAUDE.md",
                           content_text="# rules", always_on=2000)
    seed_instruction_load(engine, sid_a, artifact_id=aid, content_hash=h, at=ts(2026, 11, 3, 9))
    seed_instruction_load(engine, sid_b, artifact_id=aid, content_hash=h, at=ts(2026, 11, 5, 9))
    skill_aid, _ = seed_artifact(engine, kind="skill", name="SQL_Generator", path="skills/sql/SKILL.md",
                                 always_on=150)
    seed_artifact(engine, kind="skill", name="Never_Used", path="skills/never/SKILL.md", always_on=300)
    refresh_matviews(engine)
    return {"sid_a": sid_a, "sid_b": sid_b, "sid_old": sid_old, "pid": pid, "instructions": aid,
            "skill": skill_aid}


def test_outcomes_with_period_comparison(client, seeded):
    body = client.get("/v1/analytics/outcomes", params=WEEK).json()
    assert body["bucket"] == "day"
    cur, prev = body["current"], body["previous"]
    assert (cur["from"], cur["to"], cur["days"]) == ("2026-11-02", "2026-11-08", 7)
    assert (prev["from"], prev["to"]) == ("2026-10-26", "2026-11-01")
    assert cur["totals"]["total"] == 6 and cur["totals"]["user_corrected"] == 1 and cur["totals"]["failure"] == 1
    assert cur["totals"]["correction_rate"] == pytest.approx(1 / 6)
    assert prev["totals"]["total"] == 2
    assert body["deltas"]["total"] == {"abs": 4, "pct": 2.0}
    assert cur["error_codes"] == [{"skill_name": "SQL_Generator", "error_code": "E42", "errors": 1}]
    days = {r["bucket"]: r["total"] for r in cur["series"]}
    assert days == {"2026-11-02": 1, "2026-11-03": 3, "2026-11-05": 1, "2026-11-06": 1}


def test_event_time_bucketing_not_received_at(client, seeded):
    body = client.get("/v1/analytics/outcomes", params={"from": "2026-11-04", "to": "2026-11-04"}).json()
    assert body["current"]["totals"]["total"] == 0  # the Monday event uploaded Wednesday is not here
    body = client.get("/v1/analytics/outcomes", params={"from": "2026-11-02", "to": "2026-11-02"}).json()
    assert body["current"]["totals"]["total"] == 1


def test_weekly_bucketing(client, seeded):
    body = client.get("/v1/analytics/outcomes", params={**WEEK, "bucket": "week"}).json()
    assert [r["bucket"] for r in body["current"]["series"]] == ["2026-11-02"]
    assert body["current"]["series"][0]["total"] == 6
    assert body["previous"]["series"][0]["bucket"] == "2026-10-26"


def test_adoption(client, seeded):
    body = client.get("/v1/analytics/adoption", params=WEEK).json()
    cur = body["current"]
    assert cur["totals"]["users"] == 2 and cur["totals"]["invocations"] == 8  # 6 events + activation + agent
    names = {(r["name"], r["kind"]) for r in cur["top"]}
    assert names == {("SQL_Generator", "skill"), ("Explore", "agent")}
    day3 = [r for r in cur["series"] if r["bucket"] == "2026-11-03" and r["kind"] == "skill"][0]
    assert day3["users"] == 1 and day3["invocations"] == 4  # 3 events + 1 activation
    assert body["previous"]["totals"]["users"] == 1
    weekly = client.get("/v1/analytics/adoption", params={**WEEK, "bucket": "week", "kind": "agent"}).json()
    assert weekly["current"]["series"] == [
        {"bucket": "2026-11-02", "name": "Explore", "kind": "agent", "users": 1, "sessions": 1, "turns": 1,
         "invocations": 1, "retained_users": 0}
    ]


def test_latency_from_views(client, seeded):
    body = client.get("/v1/analytics/latency", params={**WEEK, "kind": "tool"}).json()
    assert body["approximate"] is False
    row = body["current"]["series"][0]
    assert (row["name"], row["samples"], row["p50"]) == ("Read", 2, 30.0)
    skills = client.get("/v1/analytics/latency", params={**WEEK, "kind": "skill", "bucket": "week"}).json()
    assert skills["approximate"] is True
    assert skills["current"]["series"][0]["samples"] == 6
    assert skills["current"]["totals"]["skill_p99"] > skills["current"]["totals"]["skill_p50"]


def test_cost_grouping_and_top_sessions(client, seeded):
    body = client.get("/v1/analytics/cost", params=WEEK).json()
    cur = body["current"]
    assert cur["totals"]["cost_usd"] == pytest.approx(0.75)
    assert cur["totals"]["input_tokens"] == 1400 and cur["totals"]["sessions"] == 2
    assert body["previous"]["totals"]["cost_usd"] == pytest.approx(1.0)
    assert body["deltas"]["cost_usd"]["abs"] == pytest.approx(-0.25)
    assert cur["top_sessions"][0]["session_id"] == seeded["sid_a"]
    by_model = client.get("/v1/analytics/cost", params={**WEEK, "group_by": "model"}).json()
    groups = {(r["bucket"], r["group"]): r["cost_usd"] for r in by_model["current"]["series"]}
    assert groups == {("2026-11-03", "claude-sonnet-4-5"): pytest.approx(0.5),
                      ("2026-11-05", "claude-opus-4-1"): pytest.approx(0.25)}
    assert client.get("/v1/analytics/cost", params={**WEEK, "group_by": "bogus"}).status_code == 422


def test_context_bloat_components(client, seeded):
    body = client.get("/v1/analytics/context-bloat", params={**WEEK, "project_key": "alpha"}).json()
    assert "bloat_index" in body["formula"]
    day3 = [r for r in body["current"]["series"] if r["bucket"] == "2026-11-03"][0]
    assert day3["always_on_tokens"] == 2000 and day3["loaded_artifacts"] == 1
    assert day3["compactions"] == 1 and day3["mean_pre_tokens"] == 150000
    assert day3["bloat_index"] == pytest.approx(2000 / 160000 + 1 * 100 / 2 * 0.5 + 150000 / 200000 * 0.5)
    assert body["current"]["totals"]["sessions"] == 2
    weekly = client.get("/v1/analytics/context-bloat", params={**WEEK, "bucket": "week"}).json()
    assert weekly["approximate"] and weekly["current"]["series"][0]["sessions"] == 2


def test_themes_examples_only_for_engineers(pg_url, engine, seeded, client, leadership_client):
    themes.persist(engine, SentimentScorer(make_test_settings(pg_url)), recompute_days=35)
    body = client.get("/v1/analytics/themes", params=WEEK).json()
    cur = body["current"]
    assert {r["theme"] for r in cur["series"]} >= {"primary key", "key index"}
    primary = [r for r in cur["series"] if r["theme"] == "primary key"][0]
    assert primary["count"] == 1 and len(primary["example_event_ids"]) == 1
    assert body["previous"]["series"] and {r["theme"] for r in body["previous"]["series"]} >= {"primary key"}
    assert [r["theme"] for r in cur["top"]][0] in {"primary key", "key index", "index not", "full scan"}
    lead = leadership_client.get("/v1/analytics/themes", params=WEEK).json()
    assert all("example_event_ids" not in r for r in lead["current"]["series"] + lead["current"]["top"])
    assert lead["current"]["series"][0]["count"] == 1


def test_session_timeline(client, seeded):
    sid, pid = seeded["sid_a"], seeded["pid"]
    body = client.get(f"/v1/analytics/sessions/{sid}/timeline").json()
    assert body["session"]["project_key"] == "alpha"
    assert [t["seq"] for t in body["turns"]] == [1, 2]
    turn = body["turns"][0]
    assert turn["prompt_id"] == pid and turn["prompt_text"] == "hi"
    assert len(turn["tool_calls"]) == 2 and turn["compactions"][0]["pre_tokens"] == 150000
    assert turn["skill_activations"][0]["skill_name"] == "SQL_Generator"
    assert turn["subagent_runs"][0]["agent_type"] == "Explore"
    assert turn["usage"]["cost_usd"] == pytest.approx(0.5)
    assert body["turns"][1]["usage"] is None
    # outcome events were seeded without prompt_id: they land in `unattributed`
    assert len(body["unattributed"]["outcomes"]) == 4
    assert body["totals"]["turns"] == 2 and body["totals"]["cost_usd"] == pytest.approx(0.5)
    assert client.get("/v1/analytics/sessions/not-a-uuid/timeline").status_code == 404
    assert client.get("/v1/analytics/sessions/00000000-0000-0000-0000-000000000000/timeline").status_code == 404


def test_artifacts_registry_attribution(client, seeded):
    body = client.get("/v1/analytics/artifacts", params={"from": "2026-11-02", "to": "2026-11-08"}).json()
    assert body["total"] == 3
    by_name = {a["name"]: a for a in body["artifacts"]}
    sql = by_name["SQL_Generator"]
    # 6 outcome events (no prompt_id: each its own touch) + 1 activation on the turn
    assert sql["invocations"] == 7
    assert (sql["success"], sql["user_corrected"], sql["failure"]) == (4, 1, 1)
    assert sql["correction_rate"] == pytest.approx(1 / 6)
    assert sql["cost_usd"] == pytest.approx(0.5) and sql["cost_per_invocation"] == pytest.approx(0.5 / 7)
    assert sql["synced"] is True and sql["version_count"] == 1
    claude_md = by_name["CLAUDE.md"]
    assert claude_md["sessions_loaded"] == 2 and claude_md["always_on_tokens_total"] == 4000
    assert claude_md["never_invoked"] is True
    never = client.get("/v1/analytics/artifacts", params={"from": "2026-11-02", "to": "2026-11-08",
                                                          "never_invoked": "true"}).json()
    assert [a["name"] for a in never["artifacts"]] == ["CLAUDE.md"]
    only_skills = client.get("/v1/analytics/artifacts", params={"kind": "skill", "order": "name"}).json()
    assert [a["name"] for a in only_skills["artifacts"]] == ["Never_Used", "SQL_Generator"]

    detail = client.get(f"/v1/analytics/artifacts/{seeded['skill']}",
                        params={"from": "2026-11-02", "to": "2026-11-08", "compare_to": "previous"}).json()
    assert detail["current"]["artifact"]["name"] == "SQL_Generator"
    assert [(r["bucket"], r["invocations"]) for r in detail["current"]["series"]] == [
        ("2026-11-02", 1), ("2026-11-03", 4), ("2026-11-05", 1), ("2026-11-06", 1)]
    assert detail["previous"]["artifact"]["invocations"] == 2
    assert detail["deltas"]["invocations"]["abs"] == 5
    assert len(detail["versions"]) == 1 and detail["versions"][0]["synced"] is True
    assert client.get("/v1/analytics/artifacts/00000000-0000-0000-0000-000000000000").status_code == 404


def test_sentiment_table_route(pg_url, engine, seeded, client):
    from app.sentiment import persist_daily
    persist_daily(engine, SentimentScorer(make_test_settings(pg_url)), recompute_days=35)
    body = client.get("/v1/analytics/sentiment", params=WEEK).json()
    assert body["current"]["totals"]["event_count"] == 6
    assert body["previous"]["totals"]["event_count"] == 2
    row = [r for r in body["current"]["series"] if r["bucket"] == "2026-11-06"][0]
    assert row["sentiment"] < 0  # the failure day


def test_window_validation_and_defaults(client, seeded):
    assert client.get("/v1/analytics/outcomes", params={"from": "2026-11-08", "to": "2026-11-02"}).status_code == 422
    assert client.get("/v1/analytics/outcomes", params={"bucket": "hour"}).status_code == 422
    assert client.get("/v1/analytics/outcomes", params={"compare_to": "last_year"}).status_code == 422
    body = client.get("/v1/analytics/outcomes").json()
    assert body["current"]["days"] == 30 and body["previous"] is None and body["deltas"] is None


def test_legacy_reports_filter_on_client_ts_and_fts(client, engine):
    seed_event(engine, at=ts(2026, 11, 2, 8), received_at=ts(2026, 11, 4, 8), user_prompt="joining tables")
    seed_event(engine, at=ts(2026, 11, 4, 9), notes="quarterly report numbers")
    body = client.get("/v1/reports/events", params={"from": "2026-11-02T00:00:00Z", "to": "2026-11-02T23:59:59Z"}).json()
    assert body["total"] == 1 and body["events"][0]["interaction"]["user_prompt"] == "joining tables"
    assert client.get("/v1/reports/events", params={"q": "join"}).json()["total"] == 1  # stemmed match
    assert client.get("/v1/reports/events", params={"q": "quarterly numbers"}).json()["total"] == 1
    assert client.get("/v1/reports/events", params={"q": "\"numbers quarterly\""}).json()["total"] == 0
    overview = client.get("/v1/reports/overview", params={"from": "2026-11-02", "to": "2026-11-02T23:59:59Z"}).json()
    assert overview["totals"]["events"] == 1
    assert overview["activity"][0]["bucket"].startswith("2026-11-02")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM events WHERE interaction_tsv @@ to_tsquery('join')")).scalar_one() == 1
