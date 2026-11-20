"""GitHub integration for approved proposals (design D14, task 5.4).

Identity, preferred first:

1. **GitHub App** - ``GITHUB_APP_ID`` + ``GITHUB_APP_PRIVATE_KEY`` (PEM; literal
   ``\\n`` escapes are accepted) and optionally ``GITHUB_APP_INSTALLATION_ID``
   (otherwise looked up per repository). The service signs a 9-minute RS256
   app JWT and exchanges it for a short-lived installation token, cached until
   shortly before it expires. No long-lived credential is used for API calls.
2. **Token** - ``GITHUB_TOKEN`` (fine-grained PAT) as a fallback.
3. Neither: PR creation is disabled; approvals are still recorded.

On approval the service reads the target file from the base branch, applies
the stored diff exactly (``analyst.patch``), creates ``ouroboros/proposal-<id>``,
commits the patched file through the contents API and opens a PR whose body
links the proposal and its evidence. The webhook (``pull_request`` events,
HMAC-SHA256 over the raw body with ``GITHUB_WEBHOOK_SECRET``) or the poller
marks it ``applied`` with the merge SHA, or ``failed`` when closed unmerged.

Every call goes through one ``httpx.Client`` so tests can inject a
``MockTransport``; nothing here talks to GitHub unless configured.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from .analyst import patch
from .settings import Settings

log = logging.getLogger("ouroboros.reporting.github")

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
API_VERSION = "2022-11-28"


class GitHubError(RuntimeError):
    pass


def mode(settings: Settings) -> str:
    """``app`` | ``token`` | ``disabled``."""
    if settings.github_app_id and settings.github_app_private_key:
        return "app"
    if settings.github_token:
        return "token"
    return "disabled"


def _pem(value: str) -> str:
    return value.replace("\\n", "\n").strip() + "\n"


def app_jwt(app_id: str, private_key: str, *, now: float | None = None) -> str:
    import jwt  # PyJWT[crypto]

    now = int(now if now is not None else time.time())
    # iat backdated 60 s for clock drift; GitHub caps exp at 10 minutes.
    return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": str(app_id)}, _pem(private_key),
                      algorithm="RS256")


@dataclass
class PullRequest:
    repo: str
    number: int
    url: str
    branch: str
    base: str


class GitHub:
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.mode = mode(settings)
        self._client = httpx.Client(
            base_url=settings.github_api_url.rstrip("/"),
            transport=transport,
            timeout=30.0,
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION,
                     "User-Agent": "ouroboros-reporting"},
        )
        self._lock = threading.Lock()
        self._tokens: dict[str, tuple[str, float]] = {}  # installation id -> (token, expires epoch)

    @property
    def enabled(self) -> bool:
        return self.mode != "disabled"

    def close(self) -> None:
        self._client.close()

    # --- identity -----------------------------------------------------------------------

    def _app_headers(self) -> dict:
        token = app_jwt(self.settings.github_app_id, self.settings.github_app_private_key)
        return {"Authorization": f"Bearer {token}"}

    def _installation_id(self, repo: str) -> str:
        if self.settings.github_app_installation_id:
            return str(self.settings.github_app_installation_id)
        resp = self._client.get(f"/repos/{repo}/installation", headers=self._app_headers())
        self._check(resp, f"find the app installation for {repo}")
        return str(resp.json()["id"])

    def installation_token(self, repo: str) -> str:
        installation = self._installation_id(repo)
        with self._lock:
            cached = self._tokens.get(installation)
            if cached and cached[1] - 60 > time.time():
                return cached[0]
        resp = self._client.post(f"/app/installations/{installation}/access_tokens",
                                 headers=self._app_headers())
        self._check(resp, "mint an installation token")
        body = resp.json()
        expires = _parse_ts(body.get("expires_at")) or time.time() + 3000
        with self._lock:
            self._tokens[installation] = (body["token"], expires)
        return body["token"]

    def _auth(self, repo: str) -> dict:
        if self.mode == "app":
            return {"Authorization": f"Bearer {self.installation_token(repo)}"}
        if self.mode == "token":
            return {"Authorization": f"Bearer {self.settings.github_token}"}
        raise GitHubError("PR creation not configured")

    @staticmethod
    def _check(resp: httpx.Response, what: str) -> None:
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("message", "")
            except Exception:
                detail = resp.text[:200]
            raise GitHubError(f"GitHub could not {what}: HTTP {resp.status_code} {detail}".strip())

    def _req(self, method: str, repo: str, path: str, what: str, **kwargs) -> httpx.Response:
        resp = self._client.request(method, path, headers=self._auth(repo), **kwargs)
        self._check(resp, what)
        return resp

    # --- pull requests ------------------------------------------------------------------

    def open_pull_request(self, *, repo: str, path: str, diff: str, proposal_id: str, title: str,
                          body: str) -> PullRequest:
        if not REPO_RE.match(repo or ""):
            raise GitHubError(f"artifact owner_repo {repo!r} is not an 'owner/name' GitHub repository")
        path = path.lstrip("/")
        fp = patch.single_file_patch(diff, path)
        base = self.settings.github_base_branch
        if not base:
            base = self._req("GET", repo, f"/repos/{repo}", "read the repository").json()["default_branch"]
        ref = self._req("GET", repo, f"/repos/{repo}/git/ref/heads/{quote(base, safe='')}",
                        f"read branch {base}").json()
        base_sha = ref["object"]["sha"]
        contents_path = f"/repos/{repo}/contents/{quote(path)}"
        current = self._req("GET", repo, contents_path, f"read {path} on {base}", params={"ref": base}).json()
        original = base64.b64decode(current["content"]).decode("utf-8")
        try:
            patched = patch.apply(original, fp)
        except patch.PatchError as exc:
            raise GitHubError(f"the stored diff does not apply to {path} on {base}: {exc}") from exc
        branch = f"ouroboros/proposal-{proposal_id[:8]}"
        self._req("POST", repo, f"/repos/{repo}/git/refs", f"create branch {branch}",
                  json={"ref": f"refs/heads/{branch}", "sha": base_sha})
        self._req("PUT", repo, contents_path, f"commit {path}", json={
            "message": f"{title}\n\nOuroboros proposal {proposal_id}",
            "content": base64.b64encode(patched.encode("utf-8")).decode("ascii"),
            "sha": current["sha"],
            "branch": branch,
        })
        pr = self._req("POST", repo, f"/repos/{repo}/pulls", "open the pull request", json={
            "title": title, "head": branch, "base": base, "body": body, "maintainer_can_modify": True,
        }).json()
        return PullRequest(repo=repo, number=int(pr["number"]), url=pr["html_url"], branch=branch, base=base)

    def pull_request_state(self, repo: str, number: int) -> dict:
        pr = self._req("GET", repo, f"/repos/{repo}/pulls/{number}", f"read PR #{number}").json()
        return {"state": pr.get("state"), "merged": bool(pr.get("merged")),
                "merge_sha": pr.get("merge_commit_sha"), "merged_at": pr.get("merged_at")}


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """GitHub's ``X-Hub-Signature-256: sha256=<hex>`` over the raw request body."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):].strip())


def pr_body(proposal: dict, *, base_url: str | None) -> str:
    """PR description: links back to the proposal and its evidence. The
    rationale is included only when the analyst saw no verbatim user text."""
    pid = str(proposal["proposal_id"])
    link = f"{base_url.rstrip('/')}/proposals/{pid}" if base_url else f"/proposals/{pid}"
    effect = proposal.get("expected_effect") or {}
    evidence = proposal.get("evidence") or {}
    lines = [
        f"Ouroboros improvement proposal [`{pid[:8]}`]({link}) for `{proposal.get('target_path')}` "
        f"(risk **{proposal['risk_class']}**), approved by {proposal.get('decided_by')}.",
        "",
    ]
    if evidence.get("sees_sensitive"):
        lines += ["The rationale is available on the proposal page (it may cite user text).", ""]
    else:
        lines += ["### Rationale", "", str(proposal.get("rationale") or ""), ""]
    if effect:
        lines += ["### Expected effect", ""]
        if effect.get("summary"):
            lines.append(str(effect["summary"]))
        if effect.get("tokens_saved") is not None:
            lines.append(f"- always-on tokens saved per loading session: {effect['tokens_saved']}")
        if effect.get("correction_rate_delta") is not None:
            lines.append(f"- correction-rate delta: {effect['correction_rate_delta']:+.3f}")
        lines.append("")
    ids = proposal.get("evidence_event_ids") or []
    lines += ["### Evidence", ""]
    lines.append(f"- [Proposal with metrics and diff]({link})")
    for eid in ids[:20]:
        target = f"{base_url.rstrip('/')}/engineering/inspector?q={eid}" if base_url else f"event {eid}"
        lines.append(f"- [{str(eid)[:13]}]({target})" if base_url else f"- {target}")
    lines += ["", "Merging is a human decision; Ouroboros measures the effect over the "
              "following window and reports the verdict on the proposal page."]
    return "\n".join(lines)
