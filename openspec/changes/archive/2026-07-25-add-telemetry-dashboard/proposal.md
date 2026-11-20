# Proposal: add-telemetry-dashboard

## Why

Telemetry now flows into Postgres, but there is no way to see it: no search, no aggregate view of which skills are used and how often they fail, and no read on how users actually feel while using them. A dashboard turns the stored events into the feedback signal the whole Ouroboros loop exists to produce. Per explicit decision, this change also expands capture to include user prompt/response text so sentiment can be measured from what users actually said, not only from structured feedback.

## What Changes

- **Event schema v1.1**: events MAY carry a new optional `interaction` object (`user_prompt`, `assistant_response`) — verbatim user/assistant text, classified sensitive like `user_corrections`. Ingestion accepts both `1.0` and `1.1`; existing clients are unaffected (non-breaking)
- **Storage**: new JSONB `interaction` column on the events table (idempotent migration)
- **New read-only reporting API** (`reporting/`, FastAPI): event search with filters + pagination, skill usage / error / success-rate aggregates, global interaction overview, and sentiment rollups
- **Sentiment analysis**: deterministic offline scorer (lexicon-based) over interaction text + feedback text, blended with behavioral signals (user-corrected and failure rates) into per-event, per-session, and per-skill sentiment
- **Dashboard SPA** (React + Vite, served by the reporting service): search/browse view, reporting view (most-used skills, error/success rates, global overview graphs), and a user-sentiment view (text sentiment plus correction/failure burden per skill and per session)
- **Deployment**: reporting service (API + built SPA) containerized and deployed to the k3s cluster alongside the ingestion stack

Out of scope: Entra ID validation and the client SDK (phase 2, still pending), skill-side capture of prompts/responses (no SDK exists yet — the schema and disclosure posture land now, producers adopt later), LLM-based sentiment (future upgrade of the scorer), dashboard authentication (PoC is a local single-user cluster; flagged as a risk).

## Capabilities

### New Capabilities

- `reporting-api`: read-only HTTP contract for querying telemetry — event search (filters, free text, pagination), aggregate reports (skill usage, error/success rates, global overview), and sentiment rollups
- `sentiment-analysis`: how sentiment is derived — text scoring of interaction/feedback content, behavioral signals (corrections, failures), blending into a per-event score and session/skill rollups
- `dashboard-ui`: the SPA — search/browse, reporting charts, and the sentiment component

### Modified Capabilities

- `telemetry-ingestion`: payload schema validation gains v1.1 with the optional sensitive `interaction` object (v1.0 remains accepted)
- `event-storage`: events table additionally persists the `interaction` blob (new requirement; existing requirements unchanged)
- `deployment`: reporting/dashboard service added to the k3s stack (new requirement)

## Impact

- `api/` — ingestion models accept `schema_version: "1.1"` + optional `interaction`; `schema.sql` gains the column (idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`)
- New top-level dirs: `reporting/` (FastAPI read service + sentiment scorer + Dockerfile) and `dashboard/` (Vite/React SPA, built into the reporting image)
- New dependencies: a lexicon sentiment library (Python, offline), React/Vite/Recharts toolchain (build-time only)
- `deploy/` — new manifests for the reporting service + ingress host
- **Privacy (PR-1/PR-2/PR-3)**: `interaction` fields are sensitive verbatim content — same classification as `user_corrections`, same 30-day PoC retention; the phase-3 SKILL.md template MUST disclose prompt/response capture before any producer starts sending it; the dashboard displays this text unauthenticated on the local cluster (accepted PoC risk, revisit before any promotion)
