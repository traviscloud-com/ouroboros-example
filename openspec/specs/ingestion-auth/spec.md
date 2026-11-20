# ingestion-auth

## Purpose

Authentication of ingestion requests: bearer JWT requirement, claim validation (audience/issuer/scope), identity extraction, offline `AUTH_MODE=dev` validation, and the reserved fail-closed `AUTH_MODE=entra` seam for phase 2.

## Requirements

### Requirement: Bearer JWT required on ingestion
The system SHALL require a `Authorization: Bearer <JWT>` header on `POST /v1/telemetry/events` and SHALL reject requests with a missing, malformed, expired, or signature-invalid token with `401`. Health endpoints SHALL NOT require authentication.

#### Scenario: Missing token rejected
- **WHEN** a request arrives with no Authorization header
- **THEN** the API responds `401` and persists nothing

#### Scenario: Invalid signature rejected
- **WHEN** a request carries a JWT not signed by the configured issuer's key
- **THEN** the API responds `401`

#### Scenario: Expired token rejected
- **WHEN** a request carries a JWT whose `exp` is in the past
- **THEN** the API responds `401`

#### Scenario: Health endpoints open
- **WHEN** `GET /healthz` or `GET /readyz` is called without a token
- **THEN** the request is not rejected for lack of authentication

### Requirement: Claim validation
The system SHALL validate the token's audience, issuer, and required scope (`Telemetry.Write`) against configured values, and SHALL extract `oid` and `upn` claims for identity stamping. Tokens failing claim checks SHALL be rejected with `403` (valid token, wrong claims) or `401` (unverifiable).

#### Scenario: Wrong audience rejected
- **WHEN** a validly signed token has an `aud` other than the configured audience
- **THEN** the API responds with an auth error and persists nothing

#### Scenario: Missing scope rejected
- **WHEN** a validly signed token for the right audience lacks the `Telemetry.Write` scope
- **THEN** the API responds `403`

### Requirement: Dev auth mode
When `AUTH_MODE=dev`, the system SHALL validate tokens against a locally provisioned static JWKS with configured dev issuer/audience, requiring no network access. The validation code path (signature, audience, issuer, expiry, scope, claim extraction) SHALL be the same as the future `entra` mode, differing only in key source and issuer configuration.

#### Scenario: Dev-minted token accepted offline
- **WHEN** `AUTH_MODE=dev` and a request carries a token minted by the dev token script against the mounted JWKS
- **THEN** the token validates with no external network calls and its `oid`/`upn` claims are extracted

#### Scenario: Unconfigured auth mode fails closed
- **WHEN** `AUTH_MODE` is unset or set to an unknown value
- **THEN** the service refuses to start or rejects all ingestion requests, rather than skipping validation

### Requirement: Entra mode reserved
The system SHALL accept `AUTH_MODE=entra` as a recognized configuration value whose implementation is explicitly not yet available, failing closed rather than falling back to dev behavior.

#### Scenario: Entra mode not silently downgraded
- **WHEN** `AUTH_MODE=entra` is set in this phase
- **THEN** the service fails closed (startup error or 501-class rejection) and does not validate against the dev JWKS
