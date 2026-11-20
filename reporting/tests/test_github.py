"""GitHub integration (design D14, task 5.4): app-token minting, PR creation
through the REST API, the HMAC-verified webhook and the merge poller. All HTTP
goes through ``httpx.MockTransport``; GitHub is never called."""

import base64
import hashlib
import hmac
import json

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text

from app import github as gh
from app.proposals import poll_open_prs
from tests.conftest import UNTRUSTED_PEER, make_client, make_test_settings
from tests.seeds import LOW_RISK_DIFF, SKILL_PATH, SKILL_TEXT, seed_proposal, seed_skill_artifact

WEBHOOK_SECRET = "test-webhook-secret"  # test fixture, not a credential


@pytest.fixture(scope="module")
def rsa_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return key, pem


class FakeGitHub:
    """Minimal GitHub REST double: one repo, one file, records every request."""

    def __init__(self, *, content=SKILL_TEXT, default_branch="main", merged=False, state="open"):
        self.content = content
        self.default_branch = default_branch
        self.requests: list[httpx.Request] = []
        self.committed: str | None = None
        self.pr_body: dict | None = None
        self.merged, self.state = merged, state

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path == "/app/installations/42/access_tokens" and method == "POST":
            return httpx.Response(201, json={"token": "ghs_installation", "expires_at": "2099-01-01T00:00:00Z"})
        if path == "/repos/org/repo/installation":
            return httpx.Response(200, json={"id": 42})
        if path == "/repos/org/repo" and method == "GET":
            return httpx.Response(200, json={"default_branch": self.default_branch})
        if path == f"/repos/org/repo/git/ref/heads/{self.default_branch}":
            return httpx.Response(200, json={"object": {"sha": "base-sha"}})
        if path == f"/repos/org/repo/contents/{SKILL_PATH}" and method == "GET":
            return httpx.Response(200, json={"sha": "file-sha", "content": base64.b64encode(self.content.encode()).decode()})
        if path == "/repos/org/repo/git/refs" and method == "POST":
            return httpx.Response(201, json={"ref": json.loads(request.content)["ref"]})
        if path == f"/repos/org/repo/contents/{SKILL_PATH}" and method == "PUT":
            body = json.loads(request.content)
            self.committed = base64.b64decode(body["content"]).decode()
            return httpx.Response(200, json={"commit": {"sha": "c1"}})
        if path == "/repos/org/repo/pulls" and method == "POST":
            self.pr_body = json.loads(request.content)
            return httpx.Response(201, json={"number": 7, "html_url": "https://github.example/org/repo/pull/7"})
        if path == "/repos/org/repo/pulls/7":
            return httpx.Response(200, json={"state": self.state, "merged": self.merged,
                                             "merge_commit_sha": "merge-sha" if self.merged else None,
                                             "merged_at": "2026-11-01T10:00:00Z" if self.merged else None})
        return httpx.Response(404, json={"message": f"unmocked {method} {path}"})


def test_mode_prefers_app_over_token(pg_url, rsa_key):
    _, pem = rsa_key
    assert gh.mode(make_test_settings(pg_url)) == "disabled"
    assert gh.mode(make_test_settings(pg_url, github_token="t")) == "token"
    assert gh.mode(make_test_settings(pg_url, github_token="t", github_app_id="1", github_app_private_key=pem)) == "app"


def test_app_jwt_and_installation_token_minting(pg_url, rsa_key):
    key, pem = rsa_key
    token = gh.app_jwt("12345", pem.replace("\n", "\\n"), now=1_000_000)  # escaped-newline env form
    claims = jwt.decode(token, key.public_key(), algorithms=["RS256"], options={"verify_exp": False})
    assert claims == {"iat": 999_940, "exp": 1_000_540, "iss": "12345"}

    fake = FakeGitHub()
    client = gh.GitHub(make_test_settings(pg_url, github_app_id="12345", github_app_private_key=pem),
                       transport=httpx.MockTransport(fake))
    assert client.installation_token("org/repo") == "ghs_installation"
    assert client.installation_token("org/repo") == "ghs_installation"  # cached
    mints = [r for r in fake.requests if r.url.path.endswith("/access_tokens")]
    assert len(mints) == 1
    assert mints[0].headers["authorization"].startswith("Bearer ey")  # the app JWT
    assert [r.url.path for r in fake.requests].count("/repos/org/repo/installation") == 2


def test_open_pull_request_applies_the_stored_diff(pg_url, rsa_key):
    _, pem = rsa_key
    fake = FakeGitHub()
    client = gh.GitHub(make_test_settings(pg_url, github_app_id="1", github_app_private_key=pem,
                                          github_app_installation_id="42"),
                       transport=httpx.MockTransport(fake))
    pr = client.open_pull_request(repo="org/repo", path=SKILL_PATH, diff=LOW_RISK_DIFF,
                                  proposal_id="abcdef12-0000-0000-0000-000000000000", title="t", body="b")
    assert pr.number == 7 and pr.branch == "ouroboros/proposal-abcdef12" and pr.base == "main"
    assert "description: Query Ouroboros telemetry reports\n" in fake.committed
    assert fake.pr_body["head"] == pr.branch and fake.pr_body["base"] == "main"
    api_calls = [r for r in fake.requests if r.url.path.startswith("/repos/")]
    assert all(r.headers["authorization"] == "Bearer ghs_installation" for r in api_calls)


def test_open_pull_request_rejects_drifted_base_and_bad_repo(pg_url):
    fake = FakeGitHub(content=SKILL_TEXT.replace("anything at all", "something"))
    client = gh.GitHub(make_test_settings(pg_url, github_token="t"), transport=httpx.MockTransport(fake))
    with pytest.raises(gh.GitHubError, match="does not apply"):
        client.open_pull_request(repo="org/repo", path=SKILL_PATH, diff=LOW_RISK_DIFF, proposal_id="x" * 36,
                                 title="t", body="b")
    assert not any(r.method == "POST" for r in fake.requests)  # no branch created
    with pytest.raises(gh.GitHubError, match="owner/name"):
        client.open_pull_request(repo="", path=SKILL_PATH, diff=LOW_RISK_DIFF, proposal_id="x" * 36,
                                 title="t", body="b")


def test_approve_opens_pr_and_audits(pg_url, engine):
    aid, vid = seed_skill_artifact(engine)
    pid = seed_proposal(engine, aid, version_id=vid)
    fake = FakeGitHub()
    settings = make_test_settings(pg_url, github_token="t", public_base_url="https://ouroboros.example.com")
    with make_client(pg_url, settings=settings) as c:
        c.app.state.github = gh.GitHub(settings, transport=httpx.MockTransport(fake))
        body = c.post(f"/v1/proposals/{pid}/approve").json()
    assert body["status"] == "pr_opened" and body["pr_url"] == "https://github.example/org/repo/pull/7"
    assert body["pr_number"] == 7 and body["status_note"] is None
    assert [a["action"] for a in body["audit"]] == ["proposal.approve", "proposal.pr_opened"]
    assert f"https://ouroboros.example.com/proposals/{pid}" in fake.pr_body["body"]


def test_pr_failure_marks_failed(pg_url, engine):
    aid, vid = seed_skill_artifact(engine)
    pid = seed_proposal(engine, aid, version_id=vid)
    fake = FakeGitHub(content="# completely different file\n")
    settings = make_test_settings(pg_url, github_token="t")
    with make_client(pg_url, settings=settings) as c:
        c.app.state.github = gh.GitHub(settings, transport=httpx.MockTransport(fake))
        body = c.post(f"/v1/proposals/{pid}/approve").json()
    assert body["status"] == "failed" and "does not apply" in body["failure_reason"]
    assert [a["action"] for a in body["audit"]] == ["proposal.approve", "proposal.failed"]


def _sign(body: bytes, secret=WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _merged_event(merged=True) -> bytes:
    return json.dumps({
        "action": "closed",
        "repository": {"full_name": "org/repo"},
        "pull_request": {"number": 7, "merged": merged, "merge_commit_sha": "merge-sha" if merged else None,
                         "merged_at": "2026-11-01T10:00:00Z" if merged else None},
    }).encode()


def test_signature_verification():
    body = b'{"a": 1}'
    assert gh.verify_signature(WEBHOOK_SECRET, body, _sign(body))
    assert not gh.verify_signature(WEBHOOK_SECRET, body, _sign(body, "other"))
    assert not gh.verify_signature(WEBHOOK_SECRET, body, None)
    assert not gh.verify_signature("", body, _sign(body))


def test_webhook_marks_applied_without_gateway_identity(pg_url, engine):
    aid, vid = seed_skill_artifact(engine)
    pid = seed_proposal(engine, aid, version_id=vid, status="pr_opened", pr_repo="org/repo", pr_number=7)
    settings = make_test_settings(pg_url, github_webhook_secret=WEBHOOK_SECRET)
    # Untrusted peer, no X-Auth-Request-Email: the route authenticates by HMAC alone.
    with make_client(pg_url, settings=settings, email=None, peer=UNTRUSTED_PEER) as c:
        body = _merged_event()
        bad = c.post("/v1/proposals/github-webhook", content=body,
                     headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(body, "wrong")})
        assert bad.status_code == 401
        ping = b'{"zen": "x"}'
        assert c.post("/v1/proposals/github-webhook", content=ping,
                      headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": _sign(ping)}).json()["event"] == "ping"
        ok = c.post("/v1/proposals/github-webhook", content=body,
                    headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(body)})
        assert ok.status_code == 200 and ok.json()["proposal_id"] == pid
        # other /v1 routes stay gated for this peer
        assert c.get("/v1/proposals").status_code == 401
    with engine.connect() as conn:
        row = conn.execute(text("SELECT status, merge_sha, applied_at FROM improvement_proposals")).mappings().one()
        audit = conn.execute(text("SELECT actor_email, action FROM audit_log")).all()
    assert row["status"] == "applied" and row["merge_sha"] == "merge-sha" and row["applied_at"] is not None
    assert audit == [("github-webhook", "proposal.applied")]


def test_webhook_disabled_without_secret_and_closed_unmerged_fails(pg_url, engine):
    aid, vid = seed_skill_artifact(engine)
    seed_proposal(engine, aid, version_id=vid, status="pr_opened", pr_repo="org/repo", pr_number=7)
    body = _merged_event(merged=False)
    with make_client(pg_url, email=None, peer=UNTRUSTED_PEER) as c:
        assert c.post("/v1/proposals/github-webhook", content=body,
                      headers={"X-Hub-Signature-256": _sign(body)}).status_code == 503
    settings = make_test_settings(pg_url, github_webhook_secret=WEBHOOK_SECRET)
    with make_client(pg_url, settings=settings, email=None, peer=UNTRUSTED_PEER) as c:
        c.post("/v1/proposals/github-webhook", content=body,
               headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(body)})
    with engine.connect() as conn:
        row = conn.execute(text("SELECT status, failure_reason FROM improvement_proposals")).mappings().one()
    assert row["status"] == "failed" and row["failure_reason"] == "pull request closed without merge"


def test_poller_marks_merged_prs_applied(pg_url, engine):
    aid, vid = seed_skill_artifact(engine)
    seed_proposal(engine, aid, version_id=vid, status="pr_opened", pr_repo="org/repo", pr_number=7)
    client = gh.GitHub(make_test_settings(pg_url, github_token="t"),
                       transport=httpx.MockTransport(FakeGitHub(merged=True, state="closed")))
    assert poll_open_prs(engine, client) == {"checked": 1, "updated": 1, "errors": 0}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT status FROM improvement_proposals")).scalar_one() == "applied"
    assert poll_open_prs(engine, None) == {"skipped": "PR creation not configured"}
