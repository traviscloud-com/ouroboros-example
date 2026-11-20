# Design: add-telemetry-ingestion-api

## Context

Ouroboros (PLAN.md v1) defines a skill-telemetry loop: Claude Skills report outcomes and feedback to a central API, stored in Postgres, later mined for a recurring patching process. Nothing is built yet — this change delivers milestone phase 1: a FastAPI ingestion service with the v1.0 event schema, Postgres persistence, dev-mode auth, and k3s deployment on the existing Rancher Desktop cluster (context `rancher-desktop`, k3s v1.34.5). The decision log in PLAN.md already fixes the big choices (no HMAC, FastAPI, Postgres, local PoC first, event_id idempotency, JWT-derived identity); this design fills in the implementation-level decisions.

## Goals / Non-Goals

**Goals:**
- `POST /v1/telemetry/events` accepting 1..N events per request, returning `202 Accepted`
- Strict Pydantic v2 validation of the documented v1.0 payload
- Idempotent inserts keyed on `event_id`; freshness window rejection (default 300s, configurable)
- `AUTH_MODE=dev`: offline JWT validation against a locally-generated mock issuer key; identity (`oid`/`upn`) stamped server-side onto stored rows
- Containerized API + Postgres deployed via plain k3s manifests; `/healthz` and `/readyz` wired to probes
- End-to-end verification: mint a dev token, curl through the cluster ingress/NodePort, observe the row in Postgres

**Non-Goals:**
- Real Entra ID validation (`AUTH_MODE=entra` is a config enum value and a code seam, not a working path yet)
- Client SDK, spooling, SKILL.md template (phases 2–3)
- Redis nonce cache, Snowflake/Datadog exports, dashboards, Azure infra (phase 4)
- Automated retention enforcement — 30-day retention is documented posture; a cleanup job can come with production hardening

## Decisions

### D-impl-1: Single `events` table, JSONB for feedback
One table: scalar columns for queryable fields (`event_id` unique, `session_id`, `client_ts`, `received_at`, `skill_name`, `skill_version`, `status`, `model`, `latency_ms`, `error_code`, `auth_oid`, `auth_upn`) plus a `feedback JSONB` column. The weekly patching loop queries by skill/status/session; feedback is read as a blob. Alternative — normalized feedback table — rejected as premature for a PoC with one consumer.

### D-impl-2: Migrations via a single idempotent SQL init script, not Alembic
One `schema.sql` applied by a Kubernetes init container / `psql -f` (using `CREATE TABLE IF NOT EXISTS`). Alembic adds tooling weight with zero benefit at one table; adopt it when the schema starts evolving. Alternative — Alembic from day one — deferred.

### D-impl-3: Sync SQLAlchemy Core + psycopg 3, no ORM
Ingestion is a single `INSERT ... ON CONFLICT (event_id) DO NOTHING` per batch. SQLAlchemy Core gives pooling and parameter safety without ORM ceremony; FastAPI handles it in a threadpool. Alternative — asyncpg — more moving parts (async session management) for no measurable PoC gain.

### D-impl-4: Batch semantics — per-event dedup, all-or-nothing validation
Schema validation failures reject the whole request (`422`) — a malformed batch signals a client bug. Stale events (outside freshness window) reject the whole request (`400`) with the offending `event_id`s listed. Duplicate `event_id`s are silently dropped via `ON CONFLICT DO NOTHING` and the response still says `202` — replays are an idempotency non-event, not an error (PLAN.md D5). Alternative — per-event partial acceptance with a 207-style report — more contract surface than the SDK needs; the SDK treats any 2xx as success.

### D-impl-5: Dev auth = locally minted RS256 JWTs against a static JWKS
`AUTH_MODE=dev` validates real JWT structure (signature, `aud`, `iss`, `exp`, scope claim) against a JWKS generated at deploy time and mounted into the pod; a helper script `scripts/dev_token.py` mints tokens with arbitrary `oid`/`upn`. This keeps the validation code path identical for `AUTH_MODE=entra` later — only the JWKS source (static file vs. Entra metadata URL) and issuer/audience config change. Alternative — `AUTH_MODE=dev` skips auth entirely — rejected: it would leave the claim-extraction and stamping logic untested until phase 2.

### D-impl-6: Configuration via environment variables only
`DATABASE_URL`, `AUTH_MODE`, `JWT_AUDIENCE`, `JWT_ISSUER`, `JWKS_PATH` (dev) , `FRESHNESS_WINDOW_SECONDS` (default 300). Pydantic Settings loads them; k8s manifests supply them via ConfigMap + Secret (Postgres password). No config files to mount beyond the JWKS.

### D-impl-7: Plain Kubernetes manifests, not Helm
`deploy/` holds raw YAML: Postgres (StatefulSet-lite: Deployment + PVC is fine for a single-node PoC), API Deployment + Service, ConfigMap, Secret, and an Ingress (Traefik ships with k3s). Helm templating earns its keep at multi-env time (phase 4). TLS: Traefik default self-signed is acceptable for the PoC per SR-4; plain HTTP inside the cluster.

### D-impl-8: Repo layout
```
api/
├── app/            # FastAPI package: main.py, auth.py, models.py, db.py, settings.py
├── schema.sql      # idempotent DDL
├── scripts/        # dev_token.py, gen_jwks.py
├── tests/          # pytest: schema validation, freshness, dedup, auth
├── pyproject.toml
└── Dockerfile
deploy/             # k8s manifests (kustomize-free, kubectl apply -f deploy/)
```

## Risks / Trade-offs

- [Dev JWKS private key mounted in-cluster] → It signs nothing sensitive and is PoC-only; generated per-deploy, never committed. `AUTH_MODE=entra` removes it entirely.
- [Whole-request 422 on one bad event] → A buggy skill could lose good events in the same batch; acceptable because the SDK spools failures locally and batches are small. Revisit if partial acceptance is ever needed.
- [Single Postgres replica, PVC on one node] → Data loss on node wipe; tolerable for PoC telemetry. Retention/backup deferred with the Azure promotion.
- [No rate limiting] → Cluster is local and unexposed; add at ingress in production phase.
- [Clock skew rejecting honest events] → 300s window configurable via env; NTP assumed per PLAN.md.

## Migration Plan

1. `kubectl apply -f deploy/` brings up Postgres, then the API (readiness gate: `/readyz` checks a `SELECT 1`).
2. Schema applied idempotently on API startup (or init container) — safe to re-apply.
3. Rollback = `kubectl delete -f deploy/`; no existing consumers, no data to preserve.

## Open Questions

- None blocking. Whether schema application lives in an init container vs. app startup is a task-level call (default: app startup, simplest).
