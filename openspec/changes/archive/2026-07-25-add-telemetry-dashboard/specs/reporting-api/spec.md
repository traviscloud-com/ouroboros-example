# reporting-api (delta)

## ADDED Requirements

### Requirement: Event search endpoint
The reporting service SHALL expose `GET /v1/reports/events` returning stored events (including feedback and interaction content) filtered by any combination of `skill_name`, `status`, `session_id`, `upn`, time range (`from`/`to` on `received_at`), and a free-text query `q` matched case-insensitively against feedback and interaction text, with limit/offset pagination (bounded page size) and a total match count.

#### Scenario: Filtered search returns matching events
- **WHEN** a client requests events with `skill_name=SQL_Generator&status=failure`
- **THEN** the response contains only failure events for that skill, newest first, with a total count

#### Scenario: Free-text search matches feedback and interaction content
- **WHEN** a client searches `q=join` and a stored event's `user_corrections` or `user_prompt` contains "JOIN"
- **THEN** that event is included in the results

#### Scenario: Pagination bounds results
- **WHEN** a client requests `limit=10&offset=10` against more than twenty matches
- **THEN** exactly ten events are returned along with the total match count

### Requirement: Skill usage and outcome aggregates
The reporting service SHALL expose `GET /v1/reports/skills` returning, per skill: total event count, counts and rates for `success`, `failure`, and `user_corrected`, ordered by usage, over an optional time range.

#### Scenario: Per-skill rates computed
- **WHEN** a skill has 8 success, 1 failure, and 1 user_corrected events in range
- **THEN** its entry reports 10 events with rates 0.8 / 0.1 / 0.1

#### Scenario: Most-used skills first
- **WHEN** the skills report is requested
- **THEN** skills are ordered by descending event count

### Requirement: Global overview
The reporting service SHALL expose `GET /v1/reports/overview` returning totals (events, distinct sessions, distinct users, distinct skills), a global status breakdown, and event counts bucketed over time for the requested range.

#### Scenario: Overview totals and breakdown
- **WHEN** the overview is requested
- **THEN** the response includes total events, distinct session/user/skill counts, per-status counts, and a time-bucketed activity series

### Requirement: Sentiment rollups endpoint
The reporting service SHALL expose `GET /v1/reports/sentiment` returning per-skill and per-session sentiment rollups as defined by the sentiment-analysis capability: blended score, text-score coverage, and event/correction/failure counts, over an optional time range.

#### Scenario: Sentiment rollup per skill and session
- **WHEN** the sentiment report is requested
- **THEN** each skill and each session in range carries a blended score in [-1, 1] plus its event, user_corrected, and failure counts

### Requirement: Read-only surface
The reporting service SHALL expose no endpoint that creates, mutates, or deletes telemetry data, and SHALL expose `GET /healthz` and `GET /readyz` (database-gated) probes.

#### Scenario: No mutating endpoints
- **WHEN** any POST/PUT/PATCH/DELETE is attempted against reporting endpoints
- **THEN** the service responds with 405 or 404 and stored data is unchanged

#### Scenario: Readiness gated on database
- **WHEN** the database is unreachable and `GET /readyz` is called
- **THEN** the service responds with a non-200 status
