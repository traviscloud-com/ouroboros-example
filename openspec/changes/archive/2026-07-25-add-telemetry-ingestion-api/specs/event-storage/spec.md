# event-storage

## ADDED Requirements

### Requirement: Events table with idempotency constraint
The system SHALL persist accepted events in a PostgreSQL table with a unique constraint on `event_id`, scalar columns for queryable fields (`session_id`, `client_ts`, `received_at`, `skill_name`, `skill_version`, `status`, `model`, `latency_ms`, `error_code`), and the `feedback` object stored as JSONB.

#### Scenario: Duplicate insert is a no-op
- **WHEN** an insert is attempted with an `event_id` that already exists
- **THEN** the insert affects zero rows and the original row is unchanged

#### Scenario: Feedback blob round-trips
- **WHEN** an event with `notes`, `user_corrections`, and `suggested_patch` is persisted
- **THEN** querying the row returns the complete feedback object as submitted

### Requirement: Server-side identity stamping
The system SHALL stamp each stored event with the `oid` and `upn` claims from the validated JWT of the submitting request. Identity MUST come only from the token, never from the request body.

#### Scenario: Stored row carries token identity
- **WHEN** a request authenticated as a given `oid`/`upn` is ingested
- **THEN** every row from that batch has those claim values in its identity columns

#### Scenario: Body cannot spoof identity
- **WHEN** a request body contains fields purporting to name a different user
- **THEN** stored identity columns reflect the token claims, and body-supplied identity is ignored

### Requirement: Server receipt timestamp
The system SHALL record a server-side `received_at` timestamp on every stored event, independent of the client-supplied `client_ts`.

#### Scenario: Received timestamp set on insert
- **WHEN** an event is persisted
- **THEN** its `received_at` column is set by the server at ingestion time

### Requirement: Idempotent schema application
The database schema SHALL be applied by an idempotent script that is safe to run repeatedly against an existing database.

#### Scenario: Re-applying schema preserves data
- **WHEN** the schema script runs against a database that already has the events table and rows
- **THEN** the script succeeds without error and existing rows are unchanged
