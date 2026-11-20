"""Proposals API (design D14, task 5.3): role rules, the state machine, audit
rows, leadership redaction, and the "PR creation not configured" path."""

import pytest
from sqlalchemy import text

from app.auth import Principal
from app.proposals import NOTE_PR_NOT_CONFIGURED, InvalidTransition, TRANSITIONS, transition
from tests.conftest import grant, make_client
from tests.seeds import seed_proposal, seed_skill_artifact


@pytest.fixture()
def artifact(engine):
    return seed_skill_artifact(engine)


def audit_actions(engine, pid):
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(
            text("SELECT actor_email, action FROM audit_log WHERE target_id = :p ORDER BY id"), {"p": pid})]


def test_list_detail_filters_and_404(client, engine, artifact):
    aid, vid = artifact
    low = seed_proposal(engine, aid, version_id=vid)
    high = seed_proposal(engine, aid, version_id=vid, risk_class="high", status="rejected")
    body = client.get("/v1/proposals").json()
    assert body["total"] == 2 and {p["proposal_id"] for p in body["proposals"]} == {low, high}
    assert body["proposals"][0]["target_artifact_name"] == "telemetry-reporting"
    assert client.get("/v1/proposals", params={"status": "proposed"}).json()["total"] == 1
    assert client.get("/v1/proposals", params={"risk_class": "high"}).json()["proposals"][0]["proposal_id"] == high
    assert client.get("/v1/proposals", params={"target_artifact_id": aid}).json()["total"] == 2
    assert client.get("/v1/proposals", params={"status": "bogus"}).status_code == 422
    detail = client.get(f"/v1/proposals/{low}").json()
    assert detail["diff"].startswith("--- a/") and detail["outcome"] is None and detail["audit"] == []
    assert client.get("/v1/proposals/not-a-uuid").status_code == 404
    assert client.get("/v1/proposals/00000000-0000-0000-0000-000000000000").status_code == 404


def test_admin_approves_any_risk_without_github(client, engine, artifact):
    aid, vid = artifact
    pid = seed_proposal(engine, aid, version_id=vid, risk_class="high")
    resp = client.post(f"/v1/proposals/{pid}/approve")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved" and body["decided_by"] == "admin@example.com"
    assert body["status_note"] == NOTE_PR_NOT_CONFIGURED and body["pr_url"] is None
    assert audit_actions(engine, pid) == [("admin@example.com", "proposal.approve"),
                                         ("admin@example.com", "proposal.pr_skipped")]
    # a second decision is a conflict, and the state is unchanged
    assert client.post(f"/v1/proposals/{pid}/approve").status_code == 409
    assert client.post(f"/v1/proposals/{pid}/reject").status_code == 409
    # open-pr needs GitHub
    assert client.post(f"/v1/proposals/{pid}/open-pr").status_code == 503


def test_engineer_approves_low_only(engineer_client, engine, artifact):
    aid, vid = artifact
    low = seed_proposal(engine, aid, version_id=vid, risk_class="low")
    high = seed_proposal(engine, aid, version_id=vid, risk_class="high")
    medium = seed_proposal(engine, aid, version_id=vid, risk_class="medium")
    denied = engineer_client.post(f"/v1/proposals/{high}/approve")
    assert denied.status_code == 403 and "engineer cannot approve a high risk" in denied.json()["detail"]
    assert engineer_client.post(f"/v1/proposals/{medium}/approve").status_code == 403
    assert engineer_client.get(f"/v1/proposals/{high}").json()["status"] == "proposed"
    assert audit_actions(engine, high) == []
    assert engineer_client.post(f"/v1/proposals/{low}/approve").json()["status"] == "approved"
    # engineers may reject any risk class (rejection keeps the status quo)
    rejected = engineer_client.post(f"/v1/proposals/{high}/reject", json={"reason": "too broad"}).json()
    assert rejected["status"] == "rejected" and rejected["decision_reason"] == "too broad"
    assert audit_actions(engine, high) == [("eng@example.com", "proposal.reject")]


def test_leadership_is_read_only_and_redacted(pg_url, engine, artifact):
    aid, vid = artifact
    grant(engine, "lead@example.com", "leadership")
    plain_pid = seed_proposal(engine, aid, version_id=vid, rationale="aggregates only")
    sensitive_pid = seed_proposal(engine, aid, version_id=vid, rationale="quotes a user correction",
                                  evidence={"sees_sensitive": True})
    with make_client(pg_url, email="lead@example.com") as c:
        assert c.get(f"/v1/proposals/{plain_pid}").json()["rationale"] == "aggregates only"
        redacted = c.get(f"/v1/proposals/{sensitive_pid}").json()["rationale"]
        assert redacted == {"redacted": True, "length": len("quotes a user correction")}
        assert c.post(f"/v1/proposals/{plain_pid}/approve").status_code == 403
        assert c.post(f"/v1/proposals/{plain_pid}/reject").status_code == 403
        assert c.post("/v1/proposals/run").status_code == 403
        assert c.get("/v1/proposals/summary").status_code == 200
    with make_client(pg_url) as c:  # admins see it verbatim
        assert c.get(f"/v1/proposals/{sensitive_pid}").json()["rationale"] == "quotes a user correction"


def test_state_machine_transitions_are_enforced(engine, artifact):
    aid, vid = artifact
    pid = seed_proposal(engine, aid, version_id=vid)
    actor = Principal(email="admin@example.com", role="admin")
    allowed = {(f, t) for f, ts in TRANSITIONS.items() for t in ts}
    assert allowed == {
        ("proposed", "approved"), ("proposed", "rejected"), ("proposed", "superseded"),
        ("approved", "pr_opened"), ("approved", "failed"),
        ("pr_opened", "applied"), ("pr_opened", "failed"),
    }
    with engine.begin() as conn:
        p = {"proposal_id": pid, "status": "proposed"}
        with pytest.raises(InvalidTransition):
            transition(conn, dict(p), "applied", actor, "x")
        transition(conn, p, "approved", actor, "proposal.approve")
        transition(conn, p, "pr_opened", actor, "proposal.pr_opened", fields={"pr_number": 3})
        transition(conn, p, "applied", actor, "proposal.applied")
        with pytest.raises(InvalidTransition):
            transition(conn, p, "failed", actor, "x")
    assert [a for _, a in audit_actions(engine, pid)] == ["proposal.approve", "proposal.pr_opened", "proposal.applied"]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT status, pr_number FROM improvement_proposals")).one() == ("applied", 3)


def test_summary_counts(client, engine, artifact):
    aid, vid = artifact
    seed_proposal(engine, aid, version_id=vid)
    seed_proposal(engine, aid, version_id=vid, status="applied")
    body = client.get("/v1/proposals/summary").json()
    assert body["by_status"]["proposed"] == 1 and body["by_status"]["applied"] == 1
    assert body["by_status"]["pr_opened"] == 0
    assert body["verdicts"] == {"improved": 0, "neutral": 0, "worse": 0, "insufficient_data": 0}
