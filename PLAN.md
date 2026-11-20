# Ouroboros — Claude Skill Telemetry (Refined Plan v1)

> Status: **implemented and extended.** This is the original v1 plan. Its decision
> (D), requirement (FR/SR/PR) and milestone numbers are still cited from the code and
> the OpenSpec changes, so they are kept here; current behaviour is specified in
> `openspec/specs/` and the deployment is described in `README.md`. Not built yet:
> server-side Entra ID validation (`AUTH_MODE=entra` fails closed; deployments run
> `AUTH_MODE=dev` with RS256 keys) and the promotion to Azure.

## Goal

Continuous Claude Skill optimization: skills report their own outcome (success,
failure, user-corrected) plus qualitative feedback to a central ingestion API, with
verifiable user attribution via Entra ID and session-level tracing across multi-turn
chats. Feedback drives a recurring prompt/skill patching loop.

## Decision log

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| D1 | Request signing | **No HMAC.** TLS + validated Entra ID JWT + server-side idempotency/replay check | Static shared HMAC key violated SR-1 and added nothing over TLS+JWT; a shared key also breaks non-repudiation |
| D2 | API stack | **FastAPI (Python)** | Same language as client SDK; shared Pydantic payload models |
| D3 | Storage | **PostgreSQL** (system of record; JSONB feedback blob) | Queryable for the weekly patching loop; Snowflake/Datadog become downstream exports later |
| D4 | Environment | **Local PoC on existing Rancher/k3s cluster** | Prove the loop end-to-end before any Azure spend; promote later |
| D5 | Replay protection (PoC) | Client-generated `event_id` UUID + Postgres unique constraint + timestamp freshness window | Replaying telemetry = duplicate row, i.e. an idempotency problem; Redis nonce cache deferred to production phase |
| D6 | Non-repudiation | From validated JWT `oid`/`upn` claims only | This was always the real source; HMAC never contributed |

## Requirements

### Functional
- **FR-1**: Record skill status (`success`, `failure`, `user_corrected`) and qualitative feedback (notes, user corrections, suggested patch)
- **FR-2**: Client authenticates via `DefaultAzureCredential` (pre-established `az login`; never interactive mid-skill), or with dev-mode keys when `OUROBOROS_DEV_KEY_DIR` is set
- **FR-3**: API validates identity (JWT audience/issuer/scope), payload schema, and freshness at ingestion
- **FR-4**: Correlate multi-turn logs via `session_id` carried **in the payload body** (self-describing events; headers get stripped by log pipelines)
- **FR-5**: Batch submission — one request may carry 1..N events
- **FR-6**: Telemetry is fire-and-forget: never blocks, interrupts, or raises into skill execution; spools locally when API is unreachable
- **FR-7**: `AUTH_MODE=dev` for fully-offline local development (mock issuer); `AUTH_MODE=entra` for real validation (reserved: the API answers 501 in this mode until the Entra key source is implemented)

### Security & privacy
- **SR-1**: Zero static secrets on client machines — *now actually true with HMAC removed*
- **SR-2**: Non-repudiation via verified Entra ID `oid`/`upn` claims; server stamps them onto every stored event
- **SR-3**: Idempotent ingestion — duplicate `event_id` dropped; events older than 300s rejected (NTP assumed, window configurable; spooled hook events are accepted for 7 days)
- **SR-4**: TLS in transit (terminated at the platform Gateway); plain HTTP acceptable only inside the cluster
- **PR-1**: Data classification per payload field; `user_corrections` is verbatim user content → treated as sensitive
- **PR-2**: User disclosure + opt-out (`OUROBOROS_TELEMETRY=off`); skill template must disclose telemetry, not run covertly ("quiet" = not noisy, never hidden)
- **PR-3**: Retention by sensitivity class: sensitive text is nulled after 30 days (`RETENTION_SENSITIVE_DAYS`), internal rows are kept 365 days (`RETENTION_INTERNAL_DAYS`); revisit before production

## Architecture

```
[ User Machine (Claude Skill execution) ]
  ├── 1. Skill finishes → SDK builds event (event_id, session_id, status, feedback)
  ├── 2. SDK gets a bearer token: dev-mode key or cached Entra token (spools if none)
  └── 3. Fire-and-forget POST (background thread, ≤3s timeout, local spool on failure)
        │
        ▼
[ Ingestion API — FastAPI on k3s ]
  ├── 4. Validate JWT (signature, audience api://…, issuer, scope Telemetry.Write, oid/upn)
  ├── 5. Validate schema (Pydantic), timestamp window
  └── 6. Insert; unique(event_id) dedupes replays → 202 Accepted
        │
        ▼
[ PostgreSQL on k3s (CloudNativePG) ]  →  reporting API + dashboard  →  (future: Snowflake / Datadog export)
```

## API contract

`POST /v1/telemetry/events` → `202 Accepted` (also `GET /healthz`, `GET /readyz`)

Headers: `Authorization: Bearer <JWT>`, `Content-Type: application/json`.
No custom signing headers. Server derives identity from the token, never from the body.

The same bearer also covers the endpoints added after v1: `POST /v1/telemetry/hook-events`,
`/v1/telemetry/artifacts`, `/v1/telemetry/transcripts` and the OTLP receiver under `/v1/otel`.

```jsonc
{
  "schema_version": "1.0",           // "1.1" when an event carries "interaction"
  "events": [
    {
      "event_id": "uuid",            // client-generated; idempotency key
      "session_id": "uuid",          // stable across a multi-turn chat
      "client_ts": "RFC3339",
      "skill_name": "SQL_Generator",
      "skill_version": "2.1.0",
      "status": "success | failure | user_corrected",
      "model": "string?",
      "latency_ms": 1234,
      "error_code": "string?",
      "feedback": {
        "notes": "string",
        "user_corrections": "string?",   // SENSITIVE: verbatim user content
        "suggested_patch": "string?"
      },
      "interaction": {                   // optional, schema 1.1; SENSITIVE
        "user_prompt": "string?",
        "assistant_response": "string?"
      }
    }
  ]
}
```

## Client SDK (Python, `ouroboros-client`)

- No `hmac_key` parameter — constructor takes `api_url`, `scope` and an optional `token_provider`, all defaulting from the environment
- Background worker thread + queue; caller returns immediately
- Events are written to `~/.ouroboros/spool/` first; accept any 2xx; timeout 3s; on failure they stay spooled and are retried on the next call
- No credential available → silent no-op + spool (never pop a browser mid-skill)
- Honors `OUROBOROS_TELEMETRY=off`

## Repo layout

```
ouroboros/
├── api/          # FastAPI ingestion service, SQL migrations (+ Dockerfile)
├── reporting/    # FastAPI reporting service: analytics, export, improvement loop (+ Dockerfile, serves the dashboard)
├── dashboard/    # Vite + React SPA
├── client/       # Python SDK package and hook entrypoint
├── plugin/       # Claude Code plugin: hooks + SKILL.md feedback-protocol template
├── deploy/       # Kustomize base, routes and per-environment overlays (synced by Argo CD)
├── openspec/     # OpenSpec specs & change proposals
└── PLAN.md
```

## Milestones (PoC scope)

| Phase | Deliverable | Status |
|-------|-------------|--------|
| 0 | OpenSpec init; first change proposal from this plan | done |
| 1 | FastAPI skeleton + Pydantic schema + Postgres migration; runs on k3s in `AUTH_MODE=dev`; end-to-end curl test | done |
| 2 | Entra ID app registration (`Telemetry.Write` scope); real JWT validation; client SDK with spool/fire-and-forget | client SDK done; Entra validation open |
| 3 | SKILL.md template with disclosure; end-to-end multi-turn session trace verified in Postgres | done (`plugin/skills/telemetry-reporting/`) |
| 4 *(future)* | Promote to Azure (Container Apps or AKS, Terraform), Redis nonce cache if needed, dashboards, weekly patching process | dashboard and improvement loop built on the local cluster; Azure promotion and nonce cache open |

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| No Azure CLI session on client | High | Cached tokens from prior `az login`; otherwise silent spool — never interactive mid-skill |
| Clock skew | Medium | NTP; configurable 300s freshness window |
| API downtime | Medium | Fire-and-forget + local spool + retry; skills unaffected |
| Sensitive content in `user_corrections` | High | Disclosure + opt-out, field-level classification, 30-day retention, access-controlled Postgres |
| Duplicate/replayed events | Low | `event_id` unique constraint (idempotency, not a breach vector once HMAC removed) |
