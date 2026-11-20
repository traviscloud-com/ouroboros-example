# event-storage

## Purpose

Persistence of accepted telemetry events in PostgreSQL: table shape, `event_id` idempotency, server-side identity stamping from validated JWT claims, and versioned, idempotent schema migrations.

## Requirements

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

### Requirement: Interaction storage
The system SHALL persist the optional `interaction` object as a nullable JSONB column on the events table, added by an idempotent migration. Events without `interaction` store NULL. Interaction content is sensitive verbatim conversation text and SHALL be subject to the same classification and retention posture as `feedback.user_corrections`.

#### Scenario: Interaction blob round-trips
- **WHEN** a v1.1 event with `user_prompt` and `assistant_response` is persisted
- **THEN** querying the row returns the complete interaction object as submitted

#### Scenario: Absent interaction stored as NULL
- **WHEN** a v1.0 event (or a v1.1 event without `interaction`) is persisted
- **THEN** the row's interaction column is NULL

#### Scenario: Column migration is idempotent
- **WHEN** the migrations run against a database created before the interaction column existed, then run again
- **THEN** both runs succeed and existing rows are preserved with NULL interaction

### Requirement: Idempotent schema application
The API SHALL apply versioned SQL migrations from `api/migrations/NNNN_<name>.sql` in order on startup, recording each in `schema_migrations(version, name, applied_at, checksum)` and running each file in its own transaction. Migration `0001` SHALL be the baseline schema so existing databases converge; every migration SHALL be safe to re-run, and a recorded migration whose file checksum has changed SHALL be an error. The reporting service SHALL NOT apply migrations and SHALL report not-ready until `schema_migrations` reaches the version it requires.

#### Scenario: Re-applying schema preserves data
- **WHEN** the migrations run against a database that already has the events table and rows
- **THEN** they succeed without error and existing rows are unchanged

#### Scenario: Fresh database
- **WHEN** the API starts against an empty database
- **THEN** all migrations apply in order and `schema_migrations` lists each with its checksum

#### Scenario: Existing database converges
- **WHEN** the API starts against a database that holds only the original `events` table
- **THEN** migration 0001 is a no-op, later migrations apply, and existing `events` rows are untouched

#### Scenario: Reporting waits for schema
- **WHEN** the reporting pod starts before the API has applied the latest migration
- **THEN** `/readyz` fails until the required version is present
