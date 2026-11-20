"""POST /v1/telemetry/artifacts: auth, version history on content change (design D10)."""

import hashlib
import uuid

from sqlalchemy import text


def h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def make_artifact(name: str, body: str = "body v1", **over) -> dict:
    a = {
        "kind": "skill",
        "name": name,
        "owner_repo": "acme/demo",
        "path": f"skills/{name}/SKILL.md",
        "source": "plugin",
        "content_hash": h(body),
        "frontmatter": {"name": name, "description": "d"},
        "estimated_tokens_always_on": 12,
        "estimated_tokens_body": 40,
        "content_text": body,
        "commit_sha": "a" * 40,
    }
    a.update(over)
    return a


def post(client, token, artifacts):
    return client.post(
        "/v1/telemetry/artifacts",
        json={"artifacts": artifacts, "client": {"name": "ouroboros-client", "version": "0.1.0"}},
        headers={"Authorization": f"Bearer {token}"},
    )


def versions(engine, name):
    with engine.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT v.content_hash, v.seen_until, v.content_text, v.commit_sha,
                       v.estimated_tokens_always_on, a.last_seen, a.first_seen, a.source
                FROM artifact_versions v JOIN artifacts a USING (artifact_id)
                WHERE a.name = :name ORDER BY v.seen_from
                """
            ),
            {"name": name},
        ).mappings().all()


def test_requires_bearer(client):
    resp = client.post("/v1/telemetry/artifacts", json={"artifacts": [make_artifact("x")]})
    assert resp.status_code == 401
    resp = client.post(
        "/v1/telemetry/artifacts",
        json={"artifacts": [make_artifact("x")]},
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert resp.status_code == 401


def test_validation(client, token):
    assert post(client, token, []).status_code == 422
    assert post(client, token, [make_artifact("x", kind="widget")]).status_code == 422
    assert post(client, token, [make_artifact("x", content_hash="nope")]).status_code == 422
    assert post(client, token, [make_artifact("x")] * 101).status_code == 422


def test_first_upload_creates_versions(client, token, engine):
    name = f"skill-{uuid.uuid4().hex[:8]}"
    hook = {
        "kind": "hook",
        "name": "Stop#0",
        "owner_repo": f"acme/{name}",  # unique per run: the test database is shared
        "path": "hooks/hooks.json",
        "source": "plugin",
        "content_hash": h("{}"),
        "content_text": "{}",
        "estimated_tokens_always_on": 1,
        "estimated_tokens_body": 1,
    }
    resp = post(client, token, [make_artifact(name), hook])
    assert resp.status_code == 202
    assert resp.json() == {"accepted": 2, "unchanged": 0, "versions_created": 2}

    rows = versions(engine, name)
    assert len(rows) == 1
    assert rows[0]["content_hash"] == h("body v1")
    assert rows[0]["seen_until"] is None
    assert rows[0]["content_text"] == "body v1"  # internal class: stored verbatim
    assert rows[0]["commit_sha"] == "a" * 40
    assert rows[0]["estimated_tokens_always_on"] == 12
    assert rows[0]["source"] == "plugin"
    with engine.connect() as conn:
        auth = conn.execute(
            text("SELECT auth_oid, auth_upn FROM artifacts WHERE name = :n"), {"n": name}
        ).one()
    assert auth.auth_oid and auth.auth_upn


def test_unchanged_reupload_creates_no_version(client, token, engine):
    name = f"skill-{uuid.uuid4().hex[:8]}"
    post(client, token, [make_artifact(name)])
    before = versions(engine, name)[0]

    resp = post(client, token, [make_artifact(name, commit_sha="b" * 40)])
    assert resp.status_code == 202
    assert resp.json() == {"accepted": 1, "unchanged": 1, "versions_created": 0}

    rows = versions(engine, name)
    assert len(rows) == 1
    assert rows[0]["seen_until"] is None
    assert rows[0]["commit_sha"] == "a" * 40  # first commit that carried this hash wins
    assert rows[0]["last_seen"] >= before["last_seen"]
    assert rows[0]["first_seen"] == before["first_seen"]


def test_changed_hash_closes_old_and_opens_new(client, token, engine):
    name = f"skill-{uuid.uuid4().hex[:8]}"
    post(client, token, [make_artifact(name, "body v1")])
    resp = post(client, token, [make_artifact(name, "body v2", commit_sha="c" * 40)])
    assert resp.json() == {"accepted": 1, "unchanged": 0, "versions_created": 1}

    rows = versions(engine, name)
    assert [r["content_hash"] for r in rows] == [h("body v1"), h("body v2")]
    assert rows[0]["seen_until"] is not None
    assert rows[1]["seen_until"] is None
    assert rows[1]["commit_sha"] == "c" * 40
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM artifacts WHERE name = :n"), {"n": name}
        ).scalar_one()
    assert count == 1  # one artifact row, two versions


def test_reverting_to_an_old_hash_reopens_it(client, token, engine):
    name = f"skill-{uuid.uuid4().hex[:8]}"
    post(client, token, [make_artifact(name, "body v1")])
    post(client, token, [make_artifact(name, "body v2")])
    resp = post(client, token, [make_artifact(name, "body v1")])
    # Reopening an existing row is not a new version.
    assert resp.json() == {"accepted": 1, "unchanged": 1, "versions_created": 0}
    open_rows = [r for r in versions(engine, name) if r["seen_until"] is None]
    assert len(open_rows) == 1 and open_rows[0]["content_hash"] == h("body v1")


def test_natural_key_separates_same_name_in_other_repo(client, token, engine):
    name = f"skill-{uuid.uuid4().hex[:8]}"
    post(client, token, [make_artifact(name), make_artifact(name, owner_repo="other/repo")])
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM artifacts WHERE name = :n"), {"n": name}
        ).scalar_one()
    assert count == 2
