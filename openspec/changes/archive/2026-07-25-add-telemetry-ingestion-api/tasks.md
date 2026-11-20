# Tasks: add-telemetry-ingestion-api

## 1. Project scaffolding

- [x] 1.1 Create `api/` package layout (`app/`, `scripts/`, `tests/`) with `pyproject.toml` declaring FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy, psycopg, PyJWT (with crypto), uvicorn, pytest, httpx
- [x] 1.2 Implement `app/settings.py` (Pydantic Settings): `DATABASE_URL`, `AUTH_MODE`, `JWT_AUDIENCE`, `JWT_ISSUER`, `JWKS_PATH`, `FRESHNESS_WINDOW_SECONDS` (default 300); unknown/missing `AUTH_MODE` fails startup

## 2. Event schema and storage

- [x] 2.1 Implement Pydantic models for the v1.0 payload: batch envelope (`schema_version` literal "1.0", non-empty `events`), event (UUID `event_id`/`session_id`, RFC3339 `client_ts`, `skill_name`, `skill_version`, `status` enum, optional `model`/`latency_ms`/`error_code`), feedback (`notes` required, optional `user_corrections`/`suggested_patch`)
- [x] 2.2 Write idempotent `api/schema.sql`: events table with `unique(event_id)`, scalar columns incl. `received_at`, `auth_oid`, `auth_upn`, and `feedback JSONB`
- [x] 2.3 Implement `app/db.py`: SQLAlchemy Core engine, startup schema application, batch insert with `ON CONFLICT (event_id) DO NOTHING`, `SELECT 1` readiness check

## 3. Auth (dev mode)

- [x] 3.1 Implement `scripts/gen_jwks.py` (RS256 keypair → JWKS + private key files) and `scripts/dev_token.py` (mint tokens with configurable `oid`/`upn`/scope/expiry against that key)
- [x] 3.2 Implement `app/auth.py`: bearer extraction, signature/`aud`/`iss`/`exp` validation, `Telemetry.Write` scope check, `oid`/`upn` extraction; key source selected by `AUTH_MODE` (`dev` = mounted JWKS file; `entra` = fail closed with explicit not-implemented error)
- [x] 3.3 Wire auth dependency onto the ingestion route only (health endpoints unauthenticated); map failures to 401/403 per spec

## 4. API endpoints

- [x] 4.1 Implement `POST /v1/telemetry/events`: schema validation (422 whole batch), freshness check listing stale `event_id`s (400 whole batch), identity stamping, dedup insert, `202 Accepted`
- [x] 4.2 Implement `GET /healthz` (process liveness) and `GET /readyz` (DB reachability)

## 5. Tests

- [x] 5.1 Schema validation tests: valid single/multi batch, empty events array, bad status, malformed UUID, unknown `schema_version`
- [x] 5.2 Freshness tests: stale event rejects batch with `event_id`s listed; window configurable via env
- [x] 5.3 Idempotency tests: replayed batch → 202 with no duplicate rows; mixed new+duplicate batch persists only the new event
- [x] 5.4 Auth tests: missing/invalid-signature/expired token → 401; wrong audience/scope → 401/403; body cannot spoof stamped identity; health endpoints open; `AUTH_MODE=entra` fails closed

## 6. Container and k3s deployment

- [x] 6.1 Write `api/Dockerfile` (slim Python base, non-root, uvicorn entrypoint)
- [x] 6.2 Write `deploy/` manifests: Postgres Deployment + PVC + Service + Secret (password), API ConfigMap + Deployment (JWKS mounted, liveness `/healthz`, readiness `/readyz`) + Service + Traefik Ingress
- [x] 6.3 Build the image, load it into the rancher-desktop cluster, and `kubectl apply -f deploy/`; confirm Postgres and API reach Ready

## 7. End-to-end verification

- [x] 7.1 Document and run the verification script: generate JWKS, deploy, mint dev token, curl a sample batch through the ingress → `202`, and verify the row (incl. stamped `oid`/`upn`) in Postgres
- [x] 7.2 Verify replay: re-run the same curl and confirm 202 with row count unchanged; verify Postgres pod restart preserves data
