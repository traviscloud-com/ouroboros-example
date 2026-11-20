# Ouroboros

> **Example repository.** This is a sanitized public copy of a pattern that runs in a private
> setup. Every hostname, organisation, tenant, subscription, client ID, vault, registry and
> storage name is a placeholder (`example.com`, `demo`, synthetic GUIDs); nothing here
> points at a real deployment, and it contains no secrets. Where the text says a repo is private,
> that describes the original setup. Companion repos:
> [manifests](https://github.com/traviscloud-com/cloud-platform-manifests-example),
> [infra](https://github.com/traviscloud-com/cloud-platform-infra-example),
> [catalog](https://github.com/traviscloud-com/cloud-platform-catalog-example),
> [modules](https://github.com/traviscloud-com/cloud-platform-modules-example),
> [ouroboros](https://github.com/traviscloud-com/ouroboros-example).

Continuous Claude Skill telemetry: skills report their outcome (success /
failure / user-corrected) and qualitative feedback to a central API; captured
conversation turns power a sentiment view; the data drives a recurring skill
patching loop. See `openspec/specs/` for the full capability specs and
**PLAN.md** for the original plan, whose decision and requirement numbers
(D1, FR-6, PR-1, ...) the code and specs still cite.

| Piece | Where |
|---|---|
| Ingestion API (FastAPI, JWT auth, Postgres) | `api/` |
| Reporting API (analytics, sentiment, export, improvement loop) + dashboard SPA | `reporting/`, `dashboard/` |
| Client SDK (`ouroboros-client`) + hook capture | `client/` |
| Claude Code plugin (hooks + skill template), marketplace manifest | `plugin/`, `.claude-plugin/` |
| Kubernetes manifests (synced by Argo CD) | `deploy/apps/ouroboros/` |

## Production (Kubernetes)

The platform runs Ouroboros on the `rancherlocal` cluster from `deploy/apps/ouroboros/` in this repo; Argo CD is pointed at it by
`traviscloud-com/cloud-platform-manifests-example` (`environments/rancherlocal/apps/ouroboros.yaml`):

* **Images**: `ci.yml` runs the tests and the dashboard build on pull requests
  and on pushes to `main`, then builds `api/` and `reporting/` (which bundles
  the dashboard SPA). Pushes to `main` publish them to the platform's Azure
  Container Registry as `ouroboros/api` and `ouroboros/reporting`, tagged
  `sha-<short>` and `main`; pull requests only build. Registry auth is
  GitHub OIDC against a managed identity with AcrPush; no secret is stored.
  The push is skipped while the repository variable `ACR_LOGIN_SERVER` is
  unset, so a fresh fork still passes. Deploying a build is bumping the tag in
  `deploy/apps/ouroboros/overlays/<env>/kustomization.yaml`.
* **Database**: a CloudNativePG `Cluster` in the `ouroboros` namespace. The
  operator generates the credentials; `DATABASE_URL` comes from its app secret
  (a plain `postgresql://` URL, which both services pin to the psycopg driver).
* **URLs** (one hostname, `https://ouroboros.<env>.azure.example.com`):
  `/v1/telemetry/*` and `/v1/otel/*` are the ingestion API, authenticated by
  the bearer JWT it validates itself (`AUTH_MODE=dev`, public JWKS from a
  ConfigMap, the private key stays with whoever mints tokens);
  `/v1/proposals/github-webhook` reaches the reporting service directly and is
  verified by its HMAC signature; everything else is the reporting API +
  dashboard, behind the platform Gateway's Google login plus an account
  allow-list. The reporting service trusts the Gateway's
  `X-Auth-Request-Email` header only from `TRUSTED_PROXY_CIDRS` and maps the
  email to a role (`ADMIN_EMAILS` or a `principals` row).
* **Health**: `/healthz` (alive) and `/readyz` (database reachable) on both services.

**Known gap**: the client sends `client_version` with every skill outcome, but the API does not
store it on the outcome row. Hook events, transcripts and sessions do record the client version.

## Local development

```bash
# each line from the repo root
cd api && uv venv .venv && VIRTUAL_ENV=$PWD/.venv uv pip install -e '.[dev]' && .venv/bin/python -m pytest
cd reporting && uv venv .venv && VIRTUAL_ENV=$PWD/.venv uv pip install -e '.[dev]' && .venv/bin/python -m pytest
cd client && uv venv .venv && VIRTUAL_ENV=$PWD/.venv uv pip install -e '.[dev]' && .venv/bin/python -m pytest
cd dashboard && npm ci && npm run dev          # proxies /v1 to a reporting service on :8000
```

The api/reporting tests start a throw-away Postgres with docker unless
`TEST_DATABASE_URL` is set (CI sets it to a service container). Mint a token
for your own curls with `api/scripts/dev_token.py --key-dir <dir with the
private key>`; `api/scripts/gen_jwks.py` creates a keypair.
