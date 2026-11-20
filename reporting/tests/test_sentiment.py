import uuid

from app.sentiment import SentimentScorer, rollup

from .conftest import make_test_settings, seed_event


def make_scorer(pg_url, **overrides):
    return SentimentScorer(make_test_settings(pg_url, **overrides))


def row(status="success", notes=None, user_corrections=None, user_prompt=None,
        assistant_response=None, session_id=None, skill_name="S"):
    feedback = {}
    if notes is not None:
        feedback["notes"] = notes
    if user_corrections is not None:
        feedback["user_corrections"] = user_corrections
    interaction = {}
    if user_prompt is not None:
        interaction["user_prompt"] = user_prompt
    if assistant_response is not None:
        interaction["assistant_response"] = assistant_response
    return {
        "event_id": str(uuid.uuid4()),
        "session_id": session_id or str(uuid.uuid4()),
        "skill_name": skill_name,
        "status": status,
        "feedback": feedback,
        "interaction": interaction or None,
    }


def test_negative_user_text_scores_negative(pg_url):
    scorer = make_scorer(pg_url)
    scored = scorer.score(
        row(user_prompt="this is wrong again, it keeps failing, terrible")
    )
    assert scored.text_score is not None and scored.text_score < 0
    assert scored.blended < 0


def test_assistant_text_never_scored(pg_url):
    scorer = make_scorer(pg_url)
    scored = scorer.score(
        row(assistant_response="Wonderful! I am delighted to help, this is great!")
    )
    assert scored.text_score is None
    assert scored.has_text is False


def test_deterministic(pg_url):
    scorer = make_scorer(pg_url)
    r = row(user_prompt="mixed feelings: nice output but slow and buggy")
    assert scorer.score(r).blended == scorer.score(r).blended


def test_correction_drags_score_down(pg_url):
    scorer = make_scorer(pg_url)
    text = "great result, love it"
    ok = scorer.score(row(status="success", notes=text))
    corrected = scorer.score(row(status="user_corrected", notes=text))
    assert corrected.blended < ok.blended
    assert corrected.text_score == ok.text_score


def test_blended_bounded(pg_url):
    scorer = make_scorer(
        pg_url, sentiment_weight_failure=-5.0, sentiment_behavior_weight=1.0
    )
    scored = scorer.score(row(status="failure", notes="awful horrible broken useless"))
    assert scored.blended >= -1.0


def test_no_text_uses_behavioral_only(pg_url):
    scorer = make_scorer(pg_url)
    scored = scorer.score(row(status="failure"))
    assert scored.text_score is None
    assert scored.blended == -1.0  # clamp(default failure weight)


def test_rollup_math(pg_url):
    scorer = make_scorer(pg_url)
    session = "33333333-3333-3333-3333-333333333333"
    events = [
        scorer.score(row(status="success", notes="works great, thanks", session_id=session)),
        scorer.score(row(status="failure", session_id=session)),
        scorer.score(row(status="user_corrected", user_corrections="wrong table, fix it", session_id=session)),
    ]
    (session_rollup,) = rollup(events, "session_id")
    assert session_rollup["event_count"] == 3
    assert session_rollup["failure_count"] == 1
    assert session_rollup["user_corrected_count"] == 1
    assert 0 < session_rollup["text_coverage"] < 1
    expected_mean = sum(e.blended for e in events) / 3
    assert abs(session_rollup["sentiment"] - expected_mean) < 1e-9


def test_sentiment_endpoint_rollups(client, engine):
    happy_session = "44444444-4444-4444-4444-444444444444"
    grumpy_session = "55555555-5555-5555-5555-555555555555"
    for _ in range(3):
        seed_event(engine, skill_name="Good_Skill", status="success",
                   notes="excellent, exactly what I wanted", session_id=happy_session)
    for _ in range(2):
        seed_event(engine, skill_name="Bad_Skill", status="user_corrected",
                   user_corrections="no, this is wrong again", session_id=grumpy_session)
    seed_event(engine, skill_name="Bad_Skill", status="failure", session_id=grumpy_session)

    body = client.get("/v1/reports/sentiment").json()
    skills = {s["skill_name"]: s for s in body["skills"]}
    assert skills["Bad_Skill"]["sentiment"] < skills["Good_Skill"]["sentiment"]
    assert skills["Bad_Skill"]["user_corrected_count"] == 2
    assert skills["Bad_Skill"]["failure_count"] == 1
    sessions = {s["session_id"]: s for s in body["sessions"]}
    assert sessions[grumpy_session]["sentiment"] < sessions[happy_session]["sentiment"]
    assert body["overall"]["event_count"] == 6
    assert sum(t["event_count"] for t in body["trend"]) == 6
    assert all(-1.0 <= t["sentiment"] <= 1.0 for t in body["trend"])
