# client-sdk

## Purpose

The Python reporting client (`ouroboros-client`) skills use to submit telemetry: fire-and-forget semantics, spool-first durability for outcome events and hook-event records, pluggable token providers, interaction attachment at report time, and opt-out.

## Requirements

### Requirement: Fire-and-forget reporting
The SDK SHALL expose a `record()` call that validates the event locally, persists it to the spool, and returns immediately. It SHALL never raise into the caller, never block on network I/O, and never prompt for credentials interactively. Network submission SHALL happen on a background sender with a timeout of at most 3 seconds per request, treating any 2xx response as success.

#### Scenario: record returns without network
- **WHEN** `record()` is called while the API is unreachable
- **THEN** the call returns promptly, no exception reaches the caller, and the event is on disk in the spool

#### Scenario: Sender failure never surfaces
- **WHEN** the background sender hits a timeout or non-2xx response
- **THEN** the calling application observes no exception and the event remains spooled for retry

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

### Requirement: Pluggable token providers
The SDK constructor SHALL accept `api_url`, `scope`, and an optional `token_provider` callable returning a bearer token or None, with `api_url` defaulting to `OUROBOROS_API_URL` (unset means the client is inert). A dev provider SHALL mint tokens from a local JWKS key directory and SHALL be the default when `OUROBOROS_DEV_KEY_DIR` is set; otherwise the default provider SHALL obtain tokens from `DefaultAzureCredential` using cached credentials only (no interactive flows), with `azure-identity` as an optional install extra. When no token is available the SDK SHALL silently leave events spooled.

#### Scenario: No credential means silent spool
- **WHEN** the token provider returns None
- **THEN** no request is attempted, events remain spooled, and no error or prompt reaches the user

#### Scenario: Dev provider works against the PoC stack
- **WHEN** the SDK is constructed with the dev token provider pointing at the local JWKS key directory
- **THEN** submitted events are accepted by the dev-mode ingestion API

### Requirement: Interaction attachment at report time
When recording an outcome, the SDK SHALL look up the newest complete captured interaction for the event's `session_id` in the local interaction cache; if one exists it SHALL attach it as `interaction` and declare `schema_version` `1.1`, marking the cache entry consumed so it is not attached twice. With no captured interaction the event SHALL be submitted as `schema_version` `1.0` without an `interaction` field.

#### Scenario: Captured turn rides on the outcome event
- **WHEN** the hook cache holds a complete prompt/response pair for the session and a skill records an outcome
- **THEN** the submitted event is v1.1 carrying that pair, and recording a second outcome does not attach the same pair again

#### Scenario: No capture degrades to v1.0
- **WHEN** no interaction is cached for the session
- **THEN** the event is submitted as v1.0 with no interaction field and is accepted

### Requirement: Telemetry opt-out
The SDK SHALL honor `OUROBOROS_TELEMETRY=off`: `record()` becomes a no-op that writes nothing (no spool, no cache reads, no network).

#### Scenario: Opt-out silences the SDK
- **WHEN** `OUROBOROS_TELEMETRY=off` is set and `record()` is called
- **THEN** no file is written and no request is made
