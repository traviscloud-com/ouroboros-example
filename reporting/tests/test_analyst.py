"""Analyst job (design D14, tasks 5.1/5.2): evidence packs, the Claude request,
schema enforcement and server-side validation, redaction by default, and the
disabled-without-key behaviour. The Claude API is never called: a fake client
records the request and returns scripted responses."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.analyst import evidence, job, patch
from tests.conftest import make_client, make_test_settings, seed_event
from tests.seeds import LOW_RISK_DIFF, SKILL_PATH, SKILL_TEXT, seed_instruction_load, seed_session, \
    seed_skill_artifact

SECRET = "SECRET-USER-CORRECTION-TEXT"
PROMPT = "SECRET-USER-PROMPT-TEXT"


class FakeMessages:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        out = self.outputs.pop(0) if self.outputs else self.outputs_default
        if isinstance(out, SimpleNamespace):
            return out
        return response(out)

    outputs_default = None


class FakeClient:
    def __init__(self, *outputs):
        self.beta = SimpleNamespace(messages=FakeMessages(outputs))

    @property
    def calls(self):
        return self.beta.messages.calls


def response(payload, *, stop_reason="end_turn"):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=raw)],
        stop_reason=stop_reason,
        model="claude-sonnet-5-5",
        usage=SimpleNamespace(input_tokens=1200, output_tokens=300, cache_read_input_tokens=900,
                              cache_creation_input_tokens=0),
    )


def output(aid, *, diff=LOW_RISK_DIFF, risk="low", evidence_ids=(), **overrides):
    body = {
        "target": {"artifact_id": aid, "path": SKILL_PATH},
        "rationale": "Loaded in most sessions; the description is long and broad.",
        "expected_effect": {"tokens_saved": 18, "correction_rate_delta": -0.05, "summary": "Trim the description"},
        "evidence_ids": list(evidence_ids),
        "unified_diff": diff,
        "risk_class": risk,
    }
    body.update(overrides)
    return body


@pytest.fixture()
def seeded(engine):
    aid, vid = seed_skill_artifact(engine)
    events = []
    for i in range(3):
        sid = seed_session(engine)
        seed_instruction_load(engine, sid, artifact_id=aid, content_hash="h", file_path=SKILL_PATH)
        events.append(seed_event(engine, skill_name="telemetry-reporting", status="user_corrected",
                                 session_id=sid, notes=f"note {i}", user_corrections=SECRET,
                                 user_prompt=PROMPT, age_seconds=3600 * (i + 1)))
    return {"aid": aid, "vid": vid, "events": events}


def analyst_settings(pg_url, **overrides):
    return make_test_settings(pg_url, anthropic_api_key="test-key-not-real", **overrides)


def test_pack_redacts_sensitive_text_by_default(engine, seeded):
    rows = evidence.candidates(engine, end=_now(), window_days=30, limit=5)
    assert [str(r["artifact_id"]) for r in rows] == [seeded["aid"]]
    pack = evidence.build_pack(engine, rows[0], max_examples=10)
    blob = json.dumps(pack, default=str)
    assert SECRET not in blob and PROMPT not in blob
    assert pack["examples_redacted"] is True
    assert pack["examples"][0]["feedback"]["user_corrections"] == {"redacted": True, "length": len(SECRET)}
    assert pack["version"]["content_text"] == SKILL_TEXT  # artifact text is internal class
    assert pack["metrics"]["user_corrected"] == 3 and pack["metrics"]["sessions_loaded"] == 3


def test_run_sends_no_sensitive_text_and_stores_a_proposal(pg_url, engine, seeded):
    settings = analyst_settings(pg_url)
    client = FakeClient(output(seeded["aid"], evidence_ids=[seeded["events"][0], "not-in-pack"]))
    result = job.run(engine, settings, client=client)
    assert result["status"] == "succeeded" and result["proposals_created"] == 1
    call = client.calls[0]
    sent = json.dumps(call, default=str)
    assert SECRET not in sent and PROMPT not in sent
    assert '"redacted": true' in call["messages"][0]["content"]
    # request shape: fixed cached system prompt, schema-enforced output, default Sonnet + fallback
    assert call["model"] == "claude-sonnet-5-5"
    assert call["system"][0]["text"] == job.SYSTEM_PROMPT
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["output_config"]["format"] == {"type": "json_schema", "schema": job.OUTPUT_SCHEMA}
    assert call["fallbacks"] == "default" and call["betas"] == [job.FALLBACK_BETA]
    assert "thinking" not in call and "temperature" not in call
    with engine.connect() as conn:
        p = conn.execute(text("SELECT * FROM improvement_proposals")).mappings().one()
        run = conn.execute(text("SELECT * FROM analyst_runs")).mappings().one()
        actions = [r[0] for r in conn.execute(text("SELECT action FROM audit_log ORDER BY id"))]
    assert p["status"] == "proposed" and p["created_by"] == "loop" and p["risk_class"] == "low"
    assert str(p["base_version_id"]) == seeded["vid"] and p["target_path"] == SKILL_PATH
    assert [str(e) for e in p["evidence_event_ids"]] == [seeded["events"][0]]  # unknown id dropped
    assert p["evidence"]["sees_sensitive"] is False and p["evidence"]["dropped_evidence_ids"] == 1
    assert run["status"] == "succeeded" and run["input_tokens"] == 1200 and run["cache_read_tokens"] == 900
    assert actions == ["proposal.create", "analyst.run"]
    # an artifact with an open proposal is not analysed again
    again = job.run(engine, settings, client=FakeClient())
    assert again["artifacts_considered"] == 0


def test_sees_sensitive_on_sends_verbatim_examples(pg_url, engine, seeded):
    client = FakeClient(output(seeded["aid"]))
    job.run(engine, analyst_settings(pg_url, analyst_sees_sensitive=True), client=client)
    assert SECRET in client.calls[0]["messages"][0]["content"]


@pytest.mark.parametrize(
    "bad, reason",
    [
        ("not json at all", "schema"),
        ({"rationale": "missing everything else"}, "schema"),
        ("EXTRA", "schema"),
        ("WRONG_TARGET", "target artifact_id"),
        ("OTHER_PATH", "diff: diff touches"),
        ("NOT_APPLYING", "diff: context mismatch"),
        ("EMPTY_DIFF", "no_change"),
        ("BAD_RISK", "schema"),
    ],
)
def test_schema_and_diff_enforcement_discards_bad_outputs(pg_url, engine, seeded, bad, reason):
    aid = seeded["aid"]
    if bad == "EXTRA":
        bad = {**output(aid), "extra_field": 1}
    elif bad == "WRONG_TARGET":
        bad = output("00000000-0000-0000-0000-000000000000")
    elif bad == "OTHER_PATH":
        bad = output(aid, diff=LOW_RISK_DIFF.replace(SKILL_PATH, ".github/workflows/ci.yml"))
    elif bad == "NOT_APPLYING":
        bad = output(aid, diff=LOW_RISK_DIFF.replace("about telemetry", "about TELEMETRY"))
    elif bad == "EMPTY_DIFF":
        bad = output(aid, diff="")
    elif bad == "BAD_RISK":
        bad = output(aid, risk="none")
    result = job.run(engine, analyst_settings(pg_url), client=FakeClient(bad))
    assert result["proposals_created"] == 0 and result["outputs_discarded"] == 1
    assert result["discarded"][0]["reason"].startswith(reason)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM improvement_proposals")).scalar_one() == 0
        run = conn.execute(text("SELECT outputs_discarded, details FROM analyst_runs")).mappings().one()
    assert run["outputs_discarded"] == 1 and run["details"]["discarded"][0]["artifact_id"] == aid


def test_refusal_and_truncation_are_discarded(pg_url, engine, seeded):
    refused = response("", stop_reason="refusal")
    result = job.run(engine, analyst_settings(pg_url), client=FakeClient(refused))
    assert result["discarded"][0]["reason"] == "refusal"


def test_risk_class_is_raised_for_body_edits_and_kinds(pg_url, engine, seeded):
    body_diff = (
        f"--- a/{SKILL_PATH}\n+++ b/{SKILL_PATH}\n@@ -6,2 +6,2 @@\n \n"
        "-Query the reporting API and summarize.\n+Query the reporting API, then summarize briefly.\n"
    )
    job.run(engine, analyst_settings(pg_url), client=FakeClient(output(seeded["aid"], diff=body_diff)))
    with engine.connect() as conn:
        p = conn.execute(text("SELECT risk_class, evidence FROM improvement_proposals")).mappings().one()
    assert p["risk_class"] == "medium" and p["evidence"]["risk_raised_from"] == "low"
    fp = patch.single_file_patch(LOW_RISK_DIFF, SKILL_PATH)
    assert job.risk_floor("skill", SKILL_TEXT, fp) == "low"
    assert job.risk_floor("hook", SKILL_TEXT, fp) == "high"
    assert job.risk_floor("instructions", SKILL_TEXT, fp) == "high"


def test_max_artifacts_caps_the_run(pg_url, engine, seeded):
    seed_skill_artifact(engine, name="second", path="plugin/skills/second/SKILL.md", always_on=900)
    sid = seed_session(engine)
    with engine.connect() as conn:
        second = conn.execute(text("SELECT artifact_id FROM artifacts WHERE name = 'second'")).scalar_one()
    seed_instruction_load(engine, sid, artifact_id=second, content_hash="h2")
    client = FakeClient(output(seeded["aid"]))
    result = job.run(engine, analyst_settings(pg_url, analyst_max_artifacts=1), client=client)
    assert result["artifacts_considered"] == 1 and len(client.calls) == 1


def test_disabled_without_api_key(pg_url, engine, seeded):
    with make_client(pg_url) as c:
        run = c.post("/v1/proposals/run")
        assert run.status_code == 503
        assert run.json()["detail"] == "analyst disabled: ANTHROPIC_API_KEY is not set"
        status = c.get("/v1/proposals/status").json()
        assert status["analyst"]["status"] == "disabled"
        assert status["analyst"]["reason"] == "ANTHROPIC_API_KEY is not set"
        assert status["github"]["status"] == "disabled" and status["github"]["webhook"] == "disabled"
        assert c.get("/v1/me").json()["features"]["analyst"] == "disabled"
        scheduler = c.app.state.scheduler
    settings = make_test_settings(pg_url, analyst_schedule="daily")
    assert job.disabled_reason(settings) == "ANTHROPIC_API_KEY is not set"
    from app.scheduler import Scheduler

    sched = Scheduler(settings, lambda: engine, scheduler.scorer)
    assert sched.analyst_due(_now()) is False


def test_run_route_admin_only_and_synchronous_with_wait(pg_url, engine, seeded):
    from tests.conftest import grant

    grant(engine, "eng@example.com", "engineer")
    settings = analyst_settings(pg_url)
    fake = FakeClient(output(seeded["aid"]))
    with make_client(pg_url, settings=settings) as c:
        c.app.state.scheduler.analyst_client_factory = lambda s: fake
        assert c.get("/v1/proposals/status").json()["analyst"]["status"] == "enabled"
        resp = c.post("/v1/proposals/run", params={"wait": "true"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["proposals_created"] == 1
        assert c.get("/v1/proposals").json()["total"] == 1
        last = c.get("/v1/proposals/status").json()["analyst"]["last_runs"][0]
        assert last["trigger"] == "manual" and last["actor_email"] == "admin@example.com"
    with make_client(pg_url, settings=settings, email="eng@example.com") as c:
        assert c.post("/v1/proposals/run").status_code == 403


def test_scheduled_run_is_due_only_with_key_and_schedule(pg_url, engine, seeded):
    from app.scheduler import Scheduler
    from app.sentiment import SentimentScorer

    settings = analyst_settings(pg_url, analyst_schedule="weekly")
    sched = Scheduler(settings, lambda: engine, SentimentScorer(settings))
    assert sched.analyst_due(_now()) is True
    manual_only = Scheduler(analyst_settings(pg_url), lambda: engine, SentimentScorer(settings))
    assert manual_only.analyst_due(_now()) is False


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
