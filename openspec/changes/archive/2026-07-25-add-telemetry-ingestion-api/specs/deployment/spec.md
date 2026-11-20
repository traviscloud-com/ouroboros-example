# deployment

## ADDED Requirements

### Requirement: Containerized API image
The system SHALL provide a Dockerfile that builds the FastAPI service into a runnable container image configured entirely via environment variables (`DATABASE_URL`, `AUTH_MODE`, `JWT_AUDIENCE`, `JWT_ISSUER`, `JWKS_PATH`, `FRESHNESS_WINDOW_SECONDS`).

#### Scenario: Image runs with env-only configuration
- **WHEN** the built image is started with the required environment variables set
- **THEN** the API serves requests without any baked-in or file-based configuration except the mounted JWKS

### Requirement: k3s manifests for API and Postgres
The system SHALL provide Kubernetes manifests under `deploy/` that stand up PostgreSQL (with a PersistentVolumeClaim) and the API (Deployment, Service, Ingress) on the local k3s cluster via `kubectl apply -f deploy/`, with the Postgres password supplied through a Kubernetes Secret and non-sensitive settings through a ConfigMap.

#### Scenario: Single apply brings up the stack
- **WHEN** `kubectl apply -f deploy/` runs against the rancher-desktop context
- **THEN** Postgres and the API reach Ready, and the API is reachable through the cluster ingress

#### Scenario: Postgres data survives pod restart
- **WHEN** the Postgres pod is deleted and rescheduled
- **THEN** previously ingested events are still present, backed by the PVC

### Requirement: Probe wiring
The API Deployment SHALL use `GET /healthz` as its liveness probe and `GET /readyz` as its readiness probe, so the API does not receive traffic until the database is reachable.

#### Scenario: API not ready before database
- **WHEN** the API starts while Postgres is not yet accepting connections
- **THEN** the API pod reports NotReady and receives no ingress traffic until `/readyz` succeeds

### Requirement: End-to-end dev-mode verification
The change SHALL include a documented, scriptable verification path: generate the dev JWKS, deploy, mint a dev token, POST a sample batch through the ingress with curl, and confirm the stored row (including stamped identity) in Postgres.

#### Scenario: Curl round-trip lands in Postgres
- **WHEN** the documented verification steps are executed against a fresh deployment
- **THEN** the curl receives `202 Accepted` and a matching row with the token's `oid`/`upn` is present in the events table
