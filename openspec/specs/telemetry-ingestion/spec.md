# telemetry-ingestion

## Purpose

HTTP contract for accepting batched Claude Skill telemetry events: endpoint shape, payload validation, freshness enforcement, idempotent replay handling, and health endpoints.

## Requirements

### Requirement: Batch event submission endpoint
The system SHALL expose `POST /v1/telemetry/events` accepting a JSON body with `schema_version` and an `events` array of 1..N event objects, and SHALL respond `202 Accepted` when the batch is accepted.

#### Scenario: Valid single-event batch accepted
- **WHEN** an authenticated client POSTs a schema-valid body containing one fresh event
- **THEN** the API responds `202 Accepted` and the event is persisted

#### Scenario: Valid multi-event batch accepted
- **WHEN** an authenticated client POSTs a schema-valid body containing multiple fresh events
- **THEN** the API responds `202 Accepted` and every event in the batch is persisted

#### Scenario: Empty events array rejected
- **WHEN** an authenticated client POSTs a body whose `events` array is empty
- **THEN** the API responds `422` and persists nothing

### Requirement: Payload schema validation
The system SHALL validate every event against the declared schema version — required fields `event_id` (UUID), `session_id` (UUID), `client_ts` (RFC3339), `skill_name`, `skill_version`, `status` (one of `success`, `failure`, `user_corrected`), and a `feedback` object with required `notes` and optional `user_corrections` / `suggested_patch`; optional scalars `model`, `latency_ms`, `error_code`. Supported `schema_version` values are `1.0` and `1.1`. Version `1.1` additionally allows an optional `interaction` object with optional string fields `user_prompt` and `assistant_response` (verbatim conversation content, classified sensitive like `user_corrections`); an event carrying `interaction` MUST declare `schema_version` `1.1`. A request containing any schema-invalid event SHALL be rejected in full with `422` and no events from it persisted.

#### Scenario: Invalid status value rejects whole batch
- **WHEN** a batch contains one event whose `status` is not `success`, `failure`, or `user_corrected` alongside otherwise valid events
- **THEN** the API responds `422` with a field-level error and persists none of the batch

#### Scenario: Malformed UUID rejected
- **WHEN** an event's `event_id` or `session_id` is not a valid UUID
- **THEN** the API responds `422` and persists nothing

#### Scenario: Unknown schema_version rejected
- **WHEN** the body's `schema_version` is not a supported version (`1.0` or `1.1`)
- **THEN** the API responds `422`

#### Scenario: v1.1 event with interaction accepted
- **WHEN** a `schema_version: "1.1"` batch contains an event with `interaction.user_prompt` and/or `interaction.assistant_response`
- **THEN** the API responds `202 Accepted` and the interaction content is persisted with the event

#### Scenario: v1.0 events remain accepted unchanged
- **WHEN** an existing client POSTs a valid `schema_version: "1.0"` batch without `interaction`
- **THEN** the API responds `202 Accepted` exactly as before this change

#### Scenario: interaction on a v1.0 event rejected
- **WHEN** a `schema_version: "1.0"` batch contains an event with an `interaction` object
- **THEN** the API responds `422` and persists nothing

### Requirement: Timestamp freshness enforcement
The system SHALL reject events whose `client_ts` is older than the configured freshness window (default 300 seconds) relative to server receipt time, responding `400` and identifying the stale `event_id`s. The window MUST be configurable via environment variable.

#### Scenario: Stale event rejected
- **WHEN** a batch contains an event with `client_ts` older than the freshness window
- **THEN** the API responds `400` listing the stale `event_id`(s) and persists none of the batch

#### Scenario: Fresh event within window accepted
- **WHEN** every event's `client_ts` is within the freshness window
- **THEN** freshness validation passes and processing continues

### Requirement: Idempotent replay handling
The system SHALL treat a resubmitted `event_id` as a no-op: duplicate events are silently dropped, previously stored data is unchanged, and the response remains `202 Accepted`.

#### Scenario: Replayed batch returns 202 without duplicate rows
- **WHEN** an already-ingested batch is POSTed a second time
- **THEN** the API responds `202 Accepted` and the events table still contains exactly one row per `event_id`

#### Scenario: Mixed batch of new and duplicate events
- **WHEN** a batch contains one new event and one previously ingested `event_id`
- **THEN** the API responds `202 Accepted`, the new event is persisted, and the duplicate is dropped without modifying the existing row

### Requirement: Health and readiness endpoints
The system SHALL expose `GET /healthz` returning `200` when the process is alive, and `GET /readyz` returning `200` only when the database is reachable.

#### Scenario: Readiness fails when database is down
- **WHEN** the database is unreachable and `GET /readyz` is called
- **THEN** the API responds with a non-200 status

#### Scenario: Liveness independent of database
- **WHEN** the process is running and `GET /healthz` is called
- **THEN** the API responds `200` regardless of database state
