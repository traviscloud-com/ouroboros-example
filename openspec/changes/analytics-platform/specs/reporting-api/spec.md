# reporting-api (delta)

## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: Analytics, timeline and registry endpoints
The service SHALL expose `/v1/analytics/adoption`, `/outcomes`, `/latency`, `/cost`, `/context-bloat`, `/themes`, `/sentiment`, `/sessions`, `/sessions/{id}/timeline`, `/artifacts`, `/artifacts/{id}`, all authenticated, all reading from the `analytics` schema or indexed fact tables. The series endpoints and the artifact detail SHALL accept `from`/`to`/`compare_to`; the session and artifact lists SHALL accept `from`/`to` with limit/offset pagination.

#### Scenario: Timeline assembled from all tables
- **WHEN** `/v1/analytics/sessions/{id}/timeline` is requested
- **THEN** the response lists the session's turns in order, each with its tool calls, subagent runs, compactions, instruction loads, model switches, usage and outcome events (matched by `prompt_id`), and lists records without a matching turn at session level

### Requirement: SPA fallback for client routes
The service SHALL serve `index.html` for unknown paths outside `/v1`, `/internal`, `/assets` and the health probes so react-router routes load on refresh; a missing hashed asset under `/assets` SHALL be a 404, never `index.html`.

#### Scenario: Deep link
- **WHEN** a browser requests `/engineering/sessions/abc` directly
- **THEN** the SPA is served and the router renders the session page
