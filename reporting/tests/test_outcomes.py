"""Outcome measurement (design D14, task 5.5): due detection after the
after-window, equal before/after tstzranges, the verdict rules, and how
outcomes surface on the proposal and the summary card."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.analyst import outcomes
from tests.conftest import make_test_settings, seed_event
from tests.seeds import seed_proposal, seed_skill_artifact

NAME = "telemetry-reporting"


@pytest.fixture()
def settings(pg_url):
    return make_test_settings(pg_url)


def _events(engine, *, start, n, corrected):
    for i in range(n):
        seed_event(engine, skill_name=NAME, status="user_corrected" if i < corrected else "success",
                   at=start + timedelta(hours=i))


def test_verdict_rules(settings):
    base = {"invocations": 40, "sessions_loaded": 0, "correction_rate": 0.30, "failure_rate": 0.05,
            "always_on_tokens": 400, "cost_per_invocation": 0.01}
    assert outcomes.verdict(base, {**base, "correction_rate": 0.10}, settings)[0] == "improved"
    assert outcomes.verdict(base, {**base, "correction_rate": 0.45}, settings)[0] == "worse"
    assert outcomes.verdict(base, {**base, "correction_rate": 0.31}, settings)[0] == "neutral"
    assert outcomes.verdict(base, {**base, "always_on_tokens": 200}, settings)[0] == "improved"
    # improvement bought with a collapse in invocations is not "held"
    mixed = outcomes.verdict(base, {**base, "correction_rate": 0.1, "invocations": 20}, settings)
    assert mixed[0] == "neutral" and mixed[1]["regressed"] == ["invocations"]
    small = {**base, "invocations": 5}
    v, detail = outcomes.verdict(small, {**base, "correction_rate": 0.0}, settings)
    assert v == "insufficient_data" and detail["sample_before"] == 5
    # a never-invoked artifact is sampled by the sessions that loaded it
    loaded = {"invocations": 0, "sessions_loaded": 50, "always_on_tokens": 400}
    assert outcomes.verdict(loaded, {**loaded, "always_on_tokens": 100}, settings)[0] == "improved"


def test_measure_after_window_improved(client, engine, settings):
    aid, vid = seed_skill_artifact(engine, name=NAME)
    applied = datetime.now(timezone.utc) - timedelta(days=15)
    pid = seed_proposal(engine, aid, version_id=vid, status="applied", applied_at=applied)
    not_due = seed_proposal(engine, aid, version_id=vid, status="applied",
                            applied_at=datetime.now(timezone.utc) - timedelta(days=5))
    _events(engine, start=applied - timedelta(days=10), n=30, corrected=12)
    _events(engine, start=applied + timedelta(days=1), n=30, corrected=3)

    assert outcomes.measure(engine, settings) == {"measured": 1, "verdicts": {"improved": 1}}
    assert outcomes.measure(engine, settings)["measured"] == 0  # idempotent
    with engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM improvement_outcomes")).mappings().one()
        assert conn.execute(text("SELECT count(*) FROM improvement_outcomes WHERE proposal_id = :p"),
                            {"p": not_due}).scalar_one() == 0
        audit = conn.execute(text("SELECT actor_email, action, details FROM audit_log")).mappings().one()
    assert str(row["proposal_id"]) == pid and row["verdict"] == "improved"
    assert row["before_window"].upper == row["after_window"].lower
    assert row["after_window"].upper - row["after_window"].lower == timedelta(days=14)
    assert row["before_window"].upper - row["before_window"].lower == timedelta(days=14)
    assert row["metrics_before"]["outcomes"] == 30 and row["metrics_after"]["user_corrected"] == 3
    assert audit["actor_email"] == "system" and audit["details"]["verdict"] == "improved"

    detail = client.get(f"/v1/proposals/{pid}").json()
    out = detail["outcome"]
    assert out["verdict"] == "improved" and out["before_window"]["days"] == 14
    assert out["before_window"]["bounds"] == "[)" and out["after_window"]["from"] == out["before_window"]["to"]
    assert "_verdict" not in out["metrics_before"] and out["verdict_detail"]["improved"] == ["correction_rate"]
    summary = client.get("/v1/proposals/summary").json()
    assert summary["verdicts"]["improved"] == 1 and summary["recent_outcomes"][0]["proposal_id"] == pid


def test_too_little_data_is_insufficient(engine, settings):
    aid, vid = seed_skill_artifact(engine, name=NAME)
    applied = datetime.now(timezone.utc) - timedelta(days=20)
    seed_proposal(engine, aid, version_id=vid, status="applied", applied_at=applied)
    _events(engine, start=applied - timedelta(days=3), n=4, corrected=3)
    _events(engine, start=applied + timedelta(days=1), n=40, corrected=0)
    assert outcomes.measure(engine, settings)["verdicts"] == {"insufficient_data": 1}


def test_scheduler_tick_fills_outcomes(engine, settings):
    from app.scheduler import Scheduler
    from app.sentiment import SentimentScorer

    aid, vid = seed_skill_artifact(engine, name=NAME)
    seed_proposal(engine, aid, version_id=vid, status="applied",
                  applied_at=datetime.now(timezone.utc) - timedelta(days=30))
    result = Scheduler(settings, lambda: engine, SentimentScorer(settings)).tick_sync()
    assert result["outcomes"] == {"measured": 1, "verdicts": {"insufficient_data": 1}}
    assert "github_poll" not in result  # GitHub not configured
