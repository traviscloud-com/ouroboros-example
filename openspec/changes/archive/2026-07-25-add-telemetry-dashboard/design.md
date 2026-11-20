# Design: add-telemetry-dashboard

## Context

Phase 1 delivered the ingestion path: FastAPI → Postgres on the local k3s cluster, dev-mode JWT auth, events with structured feedback. Nothing reads the data yet. This change adds the read side — search, reporting, sentiment — plus a deliberate capture expansion (user prompt/response text) chosen to make sentiment measurable from real user language. There is still no client SDK (phase 2 was deferred), so schema v1.1 defines the contract producers will adopt later; seeding/verification uses dev tokens and curl/scripts.

## Goals / Non-Goals

**Goals:**
- Event schema v1.1: optional sensitive `interaction { user_prompt, assistant_response }`; v1.0 remains accepted unchanged
- Read-only reporting API: event search (filters + free text + pagination), skill usage/outcome aggregates, global overview, sentiment rollups
- Deterministic offline sentiment: text sentiment over user-authored content blended with behavioral signals (corrections, failures)
- React SPA with three views — search/browse, reporting graphs, sentiment — served by the reporting service on k3s

**Non-Goals:**
- Entra ID auth and the client SDK (phase 2, unchanged)
- Skill-side production of `interaction` data (needs the SDK + disclosure template; this change ships the contract and storage)
- LLM-based sentiment, alerting, exports (Snowflake/Datadog), dashboard authentication (PoC-local risk, accepted)
- Retention automation (30-day posture unchanged from phase 1)

## Decisions

### D1: `interaction` as an optional JSONB blob, schema_version "1.1"
Events MAY include `interaction.user_prompt` / `interaction.assistant_response` (both optional strings). Ingestion accepts `schema_version` `"1.0"` or `"1.1"`; an event with `interaction` must declare `1.1`. Stored as a nullable JSONB column (idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`). Alternative — scalar text columns — rejected: the blob keeps the sensitive payload in one classifiable field, matching the `feedback` pattern.

### D2: Separate read-only `reporting/` service, not new routes on the ingestion API
The ingestion service keeps its narrow write-only, token-scoped surface. The reporting service is a second FastAPI app with its own deployment: different authz posture (unauthenticated PoC vs `Telemetry.Write`), different scaling/failure profile, and it also serves the built SPA (StaticFiles). They share only the database. Alternative — one app with mixed auth — rejected as posture erosion for marginal ops savings.

### D3: Sentiment = VADER over user-authored text + behavioral penalty blend
- **Text score**: VADER compound score in [-1, 1] over the concatenation of `interaction.user_prompt`, `feedback.user_corrections`, `feedback.notes`. `assistant_response` is stored for display/context but never scored — user sentiment must come from user-authored text only.
- **Behavioral score**: `status == failure` → −1.0, `user_corrected` → −0.6, `success` → +0.3 (weights configurable via env).
- **Blended event score**: `0.6 × text + 0.4 × behavioral` when user text exists; behavioral only (weight 1.0) when it doesn't. Bounded [-1, 1]; events are flagged `has_text` so rollups can show coverage.
- **Rollups**: session = mean blended score + event/correction/failure counts; skill = same aggregated per skill.
VADER is offline, deterministic, dependency-light — right for a PoC; an LLM scorer is a drop-in future upgrade behind the same scorer interface.

### D4: Sentiment computed at query time, cached in-process
No sentiment column yet. The reporting API scores rows on demand with a small TTL cache (keyed by query params). At PoC volume (thousands of rows) this is milliseconds; a persisted score column plus backfill is the documented scale-up path. Alternative — score at ingestion — rejected: couples the write path to an evolving scorer and complicates re-scoring when weights change.

### D5: SPA stack — Vite + React + TypeScript + Recharts, built into the reporting image
Multi-stage Dockerfile: node stage builds `dashboard/` → static assets copied into the Python reporting image, served at `/` with the API under `/v1/`. Same-origin means no CORS. Recharts covers bars/lines/distributions. The dataviz skill will be loaded at implementation time for chart design.

### D6: Search via SQL filters + `ILIKE` free text
Filters (skill, status, session_id, upn, time range) map to indexed columns; free-text `q` does `ILIKE` across `feedback` and `interaction` text extracted via JSONB operators. Offset/limit pagination with a bounded page size. Trigram/GIN indexes deferred until volume demands them.

### D7: Repo layout
```
reporting/
├── app/            # FastAPI: main.py, queries.py, sentiment.py, settings.py
├── tests/
├── pyproject.toml
└── Dockerfile      # multi-stage: builds ../dashboard, serves static + API
dashboard/          # Vite + React + TS SPA (src/views: Search, Reports, Sentiment)
deploy/40-reporting.yaml
```

## Risks / Trade-offs

- [Sensitive prompt/response text now stored and displayed unauthenticated] → Local single-user cluster only; PR-1 classification + 30-day retention apply; dashboard auth is a hard prerequisite for any promotion (recorded in spec as PoC scope). The SKILL.md template (phase 3) must disclose capture before producers send `interaction`.
- [Lexicon sentiment is crude (sarcasm, domain jargon)] → Scores are directional, blended with hard behavioral signals; scorer is swappable (D3) and weights configurable.
- [Query-time scoring gets slow with volume] → TTL cache now; persisted score column + backfill is the defined upgrade path (D4).
- [`ILIKE` search degrades at scale] → Acceptable at PoC volume; trigram index is a drop-in later.
- [No producers of `interaction` yet] → Verification seeds realistic v1.1 events; contract is ready for the phase-2 SDK.

## Migration Plan

1. Apply updated `schema.sql` (idempotent `ADD COLUMN IF NOT EXISTS interaction JSONB`) via existing startup path — no data migration.
2. Deploy ingestion image update (v1.1 acceptance), then reporting service (`kubectl apply -f deploy/`), then verify.
3. Rollback: remove reporting deployment; ingestion rollback is safe (v1.0 clients unaffected; the column is additive and ignored).

## Open Questions

- None blocking. Chart specifics (exact bucket sizes, colors) resolved at implementation with the dataviz skill.
