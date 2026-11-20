# reporting-api

## Purpose

HTTP contract of the reporting service for querying stored telemetry, read-only with respect to the ingested facts: event search with filters and full-text search, aggregate reports (skill usage and outcome rates, global overview), and sentiment rollups consumed by the dashboard. Every `/v1` route requires a Gateway-authenticated principal with a role.

## Requirements

### Requirement: Event search endpoint
The reporting service SHALL expose `GET /v1/reports/events` to authenticated principals, returning stored events filtered by any combination of `skill_name`, `status`, `session_id`, `upn`, `error_code`, time range (`from`/`to` on `client_ts`, event time), and a free-text query `q` matched with full-text search against feedback and interaction text, with limit/offset pagination (bounded page size) and a total match count, applying role-based redaction to `feedback` and `interaction` text.

#### Scenario: Filtered search returns matching events
- **WHEN** a client requests events with `skill_name=SQL_Generator&status=failure`
- **THEN** the response contains only failure events for that skill, newest first, with a total count

#### Scenario: Free-text search matches feedback and interaction content
- **WHEN** a client searches `q=join` and a stored event's `user_corrections` or `user_prompt` contains "JOIN"
- **THEN** that event is included in the results via the tsvector index

#### Scenario: Pagination bounds results
- **WHEN** a client requests `limit=10&offset=10` against more than twenty matches
- **THEN** exactly ten events are returned along with the total match count

#### Scenario: Event-time filtering
- **WHEN** `from`/`to` are given
- **THEN** rows are selected by `client_ts`, not `received_at`

#### Scenario: Redacted for leadership
- **WHEN** a `leadership` principal searches events
- **THEN** `feedback.notes`, `feedback.user_corrections` and `interaction.*` are redaction markers with lengths

### Requirement: Skill usage and outcome aggregates
The reporting service SHALL expose `GET /v1/reports/skills` returning, per skill: total event count, counts and rates for `success`, `failure`, and `user_corrected`, ordered by usage, over an optional time range on event time (`client_ts`).

#### Scenario: Per-skill rates computed
- **WHEN** a skill has 8 success, 1 failure, and 1 user_corrected events in range
- **THEN** its entry reports 10 events with rates 0.8 / 0.1 / 0.1

#### Scenario: Most-used skills first
- **WHEN** the skills report is requested
- **THEN** skills are ordered by descending event count

### Requirement: Global overview
The reporting service SHALL expose `GET /v1/reports/overview` returning totals (events, distinct sessions, distinct users, distinct skills), a global status breakdown, and event counts bucketed over event time (`client_ts`; `bucket` = `hour`, `day` or `week`) for the requested range.

#### Scenario: Overview totals and breakdown
- **WHEN** the overview is requested
- **THEN** the response includes total events, distinct session/user/skill counts, per-status counts, and a time-bucketed activity series

### Requirement: Sentiment rollups endpoint
The reporting service SHALL expose `GET /v1/reports/sentiment` returning per-skill and per-session sentiment rollups as defined by the sentiment-analysis capability: blended score, text-score coverage, and event/correction/failure counts, over an optional time range.

#### Scenario: Sentiment rollup per skill and session
- **WHEN** the sentiment report is requested
- **THEN** each skill and each session in range carries a blended score in [-1, 1] plus its event, user_corrected, and failure counts

### Requirement: Read-only surface
The reporting service SHALL expose no endpoint that creates, mutates, or deletes telemetry facts (`events`, `turns`, `tool_calls`, `usage_facts` and the other ingested tables); its only writes are to `principals`, `improvement_proposals` state, `improvement_outcomes`, `analyst_runs`, `audit_log`, the scheduler's aggregate refreshes (materialized views, daily sentiment and correction themes) and retention, each behind role checks or the scheduler. It SHALL expose `GET /healthz` and `GET /readyz` (database- and schema-version-gated) probes.

#### Scenario: No mutating endpoints
- **WHEN** any POST/PUT/PATCH/DELETE is attempted against `/v1/reports/*`, `/v1/analytics/*` or `/v1/export/*`
- **THEN** the service responds with 405 or 404 and stored data is unchanged

#### Scenario: Readiness gated on database
- **WHEN** the database is unreachable and `GET /readyz` is called
- **THEN** the service responds with a non-200 status

#### Scenario: Facts immutable via reporting
- **WHEN** any request to the reporting service attempts to modify `events`, `turns`, `usage_facts` or other fact tables
- **THEN** no such route exists and the request fails with 404/405
