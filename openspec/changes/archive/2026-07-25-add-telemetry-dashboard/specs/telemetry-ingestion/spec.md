# telemetry-ingestion (delta)

## MODIFIED Requirements

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
