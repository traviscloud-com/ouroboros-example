# Tasks: add-telemetry-dashboard

## 1. Event schema v1.1 (ingestion + storage)

- [x] 1.1 Extend `api/app/models.py`: `Interaction` model (`user_prompt`/`assistant_response`, both optional), `schema_version` accepts `"1.0" | "1.1"`, batch-level rule rejecting `interaction` on v1.0 events
- [x] 1.2 Extend `api/schema.sql` with idempotent `ALTER TABLE events ADD COLUMN IF NOT EXISTS interaction JSONB`; write `interaction` (or NULL) in `api/app/db.py` inserts
- [x] 1.3 Ingestion tests: v1.1 with interaction accepted + round-trips, v1.0 unchanged, interaction-on-v1.0 rejected 422, migration idempotent against a pre-column database

## 2. Reporting service core

- [x] 2.1 Scaffold `reporting/` (FastAPI app, settings: `DATABASE_URL` + sentiment weight envs, pyproject, healthz/readyz gated on DB) sharing no code with `api/` except conventions
- [x] 2.2 Implement `GET /v1/reports/events`: filters (skill, status, session_id, upn, from/to), free-text `q` over feedback + interaction JSONB text, newest-first, bounded limit/offset, total count
- [x] 2.3 Implement `GET /v1/reports/skills` (per-skill counts + success/failure/user_corrected rates, usage-ordered) and `GET /v1/reports/overview` (totals, distinct sessions/users/skills, status breakdown, time-bucketed activity)
- [x] 2.4 Reporting API tests against seeded Postgres: search filters, free-text matches, pagination + counts, rates math, overview totals, read-only surface (no mutating routes)

## 3. Sentiment analysis

- [x] 3.1 Implement `reporting/app/sentiment.py`: VADER text score over user-authored fields only (user_prompt, user_corrections, notes; never assistant_response), `has_text` flag, behavioral weights from env, 0.6/0.4 blend bounded [-1, 1]
- [x] 3.2 Implement session and skill rollups + `GET /v1/reports/sentiment` (mean blended score, text coverage, correction/failure counts) with TTL cache
- [x] 3.3 Sentiment tests: negative text scores negative, assistant text ignored, determinism, correction drags score down, bounds, rollup math per session/skill

## 4. Dashboard SPA

- [x] 4.1 Scaffold `dashboard/` (Vite + React + TS + Recharts), API client for the reporting endpoints, layout with Search / Reports / Sentiment views (load the dataviz skill before building charts)
- [x] 4.2 Search view: filter controls + free-text box + paginated table + event detail (feedback and interaction text)
- [x] 4.3 Reports view: most-used-skills chart with outcome rates, global overview tiles + activity-over-time chart
- [x] 4.4 Sentiment view: overall sentiment + trend, per-skill sentiment with correction/failure burden, session drill-down

## 5. Container and deployment

- [x] 5.1 `reporting/Dockerfile` multi-stage (node builds `dashboard/` → assets served by FastAPI StaticFiles at `/`, API under `/v1/`), non-root
- [x] 5.2 `deploy/40-reporting.yaml`: Deployment (DATABASE_URL from existing secret, probes on /healthz and /readyz), Service, ingress host `dashboard.ouroboros.local`
- [x] 5.3 Build, deploy alongside existing stack with `kubectl apply -f deploy/`, confirm Ready

## 6. End-to-end verification

- [x] 6.1 Seed script: POST a realistic mix of v1.0 and v1.1 events (multiple skills, sessions, users, statuses, sentiment-varied prompt/correction text) through the ingestion API with dev tokens
- [x] 6.2 Verify through the dashboard ingress: search finds seeded text, skills report rates match the seed distribution, overview totals correct, sentiment view shows lower scores for the correction/failure-heavy skill; extend `deploy/verify.sh` or add `deploy/verify_dashboard.sh`
