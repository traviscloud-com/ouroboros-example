# analytics-aggregations (delta)

## ADDED Requirements

### Requirement: Materialized analytics views refreshed on a schedule
The reporting service SHALL maintain, in an `analytics` schema, materialized views for adoption (DAU/WAU, sessions, turns, invocations per skill/agent and weekly retention), outcomes and error codes, latency percentiles (p50/p95/p99 for skill latency and tool duration), cost and tokens per day/model/skill/agent/session and context-bloat inputs, plus scheduler-written tables for daily sentiment and weekly correction themes. An in-process scheduler SHALL refresh them concurrently every `AGGREGATE_REFRESH_SECONDS` (default 300) and SHALL expose `POST /internal/refresh` for an optional CronJob.

#### Scenario: Fresh data appears within one refresh interval
- **WHEN** new hook and OTel data are ingested
- **THEN** the adoption and cost views reflect them after at most one refresh interval without blocking readers

#### Scenario: Percentiles computed server-side
- **WHEN** `/v1/analytics/latency` is requested for a skill
- **THEN** p50, p95 and p99 come from the view and no per-request Python aggregation over raw rows occurs

### Requirement: Event-time bucketing and period comparison
Every analytics endpoint SHALL bucket on event time (`client_ts`/`at`), never `received_at`, SHALL accept `from`, `to` and `compare_to` (a previous window of equal length) and SHALL return both windows so the UI can show deltas.

#### Scenario: Spooled data lands on its own day
- **WHEN** hook events created on Monday are uploaded on Wednesday
- **THEN** Monday's buckets include them and Wednesday's do not

#### Scenario: Comparison window returned
- **WHEN** `/v1/analytics/adoption?from=2026-11-01&to=2026-11-07&compare_to=previous` is requested
- **THEN** the response carries series for both 2026-11-01..07 and 2026-10-25..31

### Requirement: Context-bloat index
The system SHALL compute a per-day, per-project context-bloat index combining always-on tokens of artifacts loaded at session start, compaction frequency per 100 turns, and mean pre-compaction tokens relative to the model's context window, SHALL expose its components alongside the index, and SHALL document the formula as a recalibratable heuristic.

#### Scenario: Index decomposable
- **WHEN** `/v1/analytics/context-bloat` is requested
- **THEN** each point includes `always_on_tokens`, `loaded_artifacts`, `compactions`, `mean_pre_tokens` and `bloat_index`

#### Scenario: Trimmed description lowers the index
- **WHEN** a skill's always-on tokens drop in a new version and sessions load the new version
- **THEN** the following days' `always_on_tokens` and `bloat_index` decrease for that project with other inputs unchanged

### Requirement: Modeling export
The reporting service SHALL expose `GET /v1/export/{dataset}` for `sessions`, `turns`, `tool_calls`, `usage_facts`, `events` and `artifact_versions` streaming NDJSON (and Parquet when the optional extra is installed) for a date range, to `engineer`+ roles only, with a `redact=1` option that produces a shareable redacted file, a row cap `EXPORT_MAX_ROWS`, and an `audit_log` entry per export.

#### Scenario: Engineer exports verbatim turns
- **WHEN** an `engineer` requests `/v1/export/turns?format=ndjson`
- **THEN** the stream contains prompt and response text and an audit row records who exported what range

#### Scenario: Leadership cannot export
- **WHEN** a `leadership` principal requests any export dataset
- **THEN** the service returns 403

#### Scenario: Redacted export for sharing
- **WHEN** an `engineer` requests an export with `redact=1`
- **THEN** the stream contains redaction markers instead of verbatim text columns

### Requirement: Correction themes
The service SHALL derive weekly correction themes from negative-sentiment user corrections using keyword/bigram clustering, store them in `analytics.correction_themes` with example event ids, and SHALL keep LLM-based theme scoring behind an off-by-default `THEMES_LLM` setting.

#### Scenario: Themes with examples
- **WHEN** `/v1/analytics/themes?from&to` is requested
- **THEN** each theme carries a count and example event ids that an engineer can open; a leadership principal sees the theme label and count only
