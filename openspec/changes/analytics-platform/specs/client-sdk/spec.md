# client-sdk (delta)

## MODIFIED Requirements

### Requirement: Spool-first durability and drain
The SDK SHALL write every outcome event to `~/.ouroboros/spool/` and every hook-event record to `~/.ouroboros/spool/hooks/` before any submission attempt, delete spool entries only after a 2xx response, retry remaining entries on subsequent activity, and bound each spool to a configurable maximum (outcomes 500, hook events 5,000; oldest dropped). Outcome events older than the server freshness window SHALL be submitted separately so their rejection cannot poison fresh batches, and dropped after rejection; hook events SHALL NOT be stale-dropped client-side because the server accepts them up to its `HOOK_EVENTS_MAX_AGE_SECONDS`.

#### Scenario: Spooled events drain when API returns
- **WHEN** events were spooled during an outage and any hook runs or `record()` is called after the API is reachable again
- **THEN** both spools are submitted and their files removed on success

#### Scenario: Crash-safe resend is idempotent
- **WHEN** the process dies after a successful POST but before spool deletion, and the batch is re-sent later
- **THEN** the server deduplicates by `event_id` / `hook_event_id` and no duplicate row results

#### Scenario: Stale spool cannot poison fresh events
- **WHEN** the outcome spool contains events older than the freshness window alongside fresh ones
- **THEN** fresh events are submitted in their own batch and accepted regardless of the stale events' rejection

#### Scenario: Offline hook events survive
- **WHEN** hook events accumulate for 3 days with no connectivity
- **THEN** they are uploaded with their original `client_ts` once the API is reachable

## ADDED Requirements

### Requirement: Hook-event batching to the structured endpoint
The background sender SHALL submit hook-event records in batches of at most 200 to `POST /v1/telemetry/hook-events` with the configured token provider, a timeout of at most 3 seconds, and the same never-raise semantics as outcome submission. The sender SHALL be nudged by hook runs through a lightweight out-of-process mechanism that keeps the hook itself network-free.

#### Scenario: Hook run triggers upload off the hook path
- **WHEN** a hook event is spooled and the hook process exits
- **THEN** the upload happens in a separate SDK process or the next `record()`/`report` invocation, never inside the hook process

### Requirement: Additional CLI commands
The SDK SHALL ship `python3 -m ouroboros_client.setup_otel`, `python3 -m ouroboros_client.transcript` and `python3 -m ouroboros_client.sync_artifacts`, all configured from the same environment variables as `report`, all inert under `OUROBOROS_TELEMETRY=off`, and all importing only stdlib plus `httpx` (minting a dev token additionally needs PyJWT from the `dev` extra).

#### Scenario: Opt-out silences every command
- **WHEN** `OUROBOROS_TELEMETRY=off` and any of the new commands is run
- **THEN** no file outside `~/.ouroboros` is modified and no request is made

### Requirement: Client version stamping
Every outcome event, hook-event record and OTel resource configured by the SDK SHALL carry the SDK and plugin versions so the server can select parsers by version.

#### Scenario: Version present
- **WHEN** a hook-event batch is uploaded
- **THEN** each record carries `client_version` matching the installed `ouroboros-client` version
