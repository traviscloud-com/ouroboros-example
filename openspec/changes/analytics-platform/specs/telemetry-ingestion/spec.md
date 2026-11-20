# telemetry-ingestion (delta)

## MODIFIED Requirements

### Requirement: Timestamp freshness enforcement
`POST /v1/telemetry/events` SHALL reject events whose `client_ts` is older than the configured freshness window (default 300 seconds) relative to server receipt time, responding `400` and identifying the stale `event_id`s; the window MUST be configurable via environment variable. `POST /v1/telemetry/hook-events` SHALL instead accept `client_ts` up to `HOOK_EVENTS_MAX_AGE_SECONDS` (default 604800) old and reject only the older records, individually, so spooled offline work is retained while outcome replay protection is unchanged. `POST /v1/telemetry/transcripts` SHALL apply no age limit: backfills are bounded by the client's local transcript retention.

#### Scenario: Stale event rejected
- **WHEN** an outcome batch contains an event with `client_ts` older than the freshness window
- **THEN** the API responds `400` listing the stale `event_id`(s) and persists none of the batch

#### Scenario: Fresh event within window accepted
- **WHEN** every outcome event's `client_ts` is within the freshness window
- **THEN** freshness validation passes and processing continues

#### Scenario: Week-old hook events accepted
- **WHEN** a hook-event batch arrives with `client_ts` 5 days old
- **THEN** the API returns 202 and stores the records with their original timestamps

## ADDED Requirements

### Requirement: New ingestion endpoints
The API SHALL expose `POST /v1/telemetry/hook-events`, `POST /v1/telemetry/transcripts`, `POST /v1/telemetry/artifacts`, `POST /v1/otel/v1/metrics` and `POST /v1/otel/v1/logs`, all requiring the `Telemetry.Write` bearer token, all stamping `auth_oid`/`auth_upn` server-side, and all idempotent on their natural ids. Request bodies up to 8 MiB SHALL be accepted.

#### Scenario: Identity stamped on structured data
- **WHEN** a hook-event batch is accepted
- **THEN** every stored row carries the token's `oid` and `upn`, not any client-supplied identity

#### Scenario: Large OTel batch
- **WHEN** a 5 MiB protobuf metrics batch arrives
- **THEN** it is accepted within the configured timeout
