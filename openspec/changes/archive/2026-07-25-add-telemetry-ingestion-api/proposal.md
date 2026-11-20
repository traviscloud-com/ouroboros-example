# Proposal: add-telemetry-ingestion-api

## Why

Ouroboros needs its first working slice: today there is only a plan (PLAN.md) and no running code, so nothing can accept skill telemetry events. Milestone phase 1 calls for a deployable ingestion path — FastAPI service, validated event schema, Postgres storage — running on the local k3s cluster in `AUTH_MODE=dev`, proving the end-to-end loop before any Entra ID or Azure investment.

## What Changes

- New FastAPI ingestion service (`api/`) exposing `POST /v1/telemetry/events`, `GET /healthz`, and `GET /readyz`
- Pydantic models for the v1.0 event payload (batch of 1..N events, `session_id` in the body, `feedback` blob with sensitive `user_corrections` field)
- PostgreSQL schema/migration: events table with `unique(event_id)` for idempotent ingestion and JSONB feedback column
- Ingestion-time validation: JWT presence/claims (dev-mode mock issuer), payload schema, timestamp freshness window (default 300s)
- `AUTH_MODE=dev` support: fully offline token validation against a mock issuer; `AUTH_MODE=entra` is stubbed but out of scope for this change
- Dockerfile for the API and k3s manifests (`deploy/`) for API + Postgres, deployable to the local Rancher Desktop cluster
- End-to-end verification path: curl a signed dev-mode request into the cluster and observe the row in Postgres

Out of scope (later changes): real Entra ID JWT validation, Python client SDK, SKILL.md template, Azure promotion, Redis nonce cache, dashboards.

## Capabilities

### New Capabilities

- `telemetry-ingestion`: HTTP API contract for accepting batched skill telemetry events — endpoint shape, payload schema, validation rules (schema, freshness, batch semantics), idempotency behavior, and response codes
- `event-storage`: Persistence of accepted events in PostgreSQL — table shape, `event_id` uniqueness/dedup, identity stamping from the validated token, JSONB feedback storage, retention posture
- `ingestion-auth`: Authentication of ingestion requests — bearer JWT requirement, `AUTH_MODE=dev` mock-issuer validation, claim extraction (`oid`/`upn`), and the switch point for future `AUTH_MODE=entra`
- `deployment`: Running the service on k3s — container image, manifests for API and Postgres, configuration via environment, health/readiness probes

### Modified Capabilities

_None — `openspec/specs/` is empty; this is the first change._

## Impact

- New top-level directories: `api/` (FastAPI service + Dockerfile), `deploy/` (k3s manifests)
- New runtime dependencies: FastAPI, Pydantic v2, a Postgres driver (psycopg/asyncpg), PyJWT or python-jose for dev-mode token checks
- Infrastructure: PostgreSQL and the API deployed to the existing Rancher Desktop k3s cluster (context `rancher-desktop`, node `my-node`)
- No existing code is modified (repo currently has no application code); no breaking changes
- Security/privacy posture per PLAN.md: no static secrets (D1/SR-1), `user_corrections` treated as sensitive (PR-1), 30-day retention target for the PoC (PR-3)
