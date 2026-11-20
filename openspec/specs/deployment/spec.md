# deployment

## Purpose

Running the stack on Kubernetes: the two container images and the CI workflow that publishes them, the Kustomize manifests under `deploy/apps/ouroboros` that Argo CD syncs to the rancherlocal k3s cluster, the CloudNativePG database, Gateway routing and access policies, probe wiring, and the dev-mode token tooling.

## Requirements

### Requirement: Containerized API image
The system SHALL provide `api/Dockerfile`, which builds the FastAPI ingestion service (including its SQL migrations) into a container image that runs as a non-root user on port 8000 and is configured entirely via environment variables (`DATABASE_URL`, `AUTH_MODE`, `JWT_AUDIENCE`, `JWT_ISSUER`, `JWKS_PATH`, `FRESHNESS_WINDOW_SECONDS`, `HOOK_EVENTS_MAX_AGE_SECONDS`).

#### Scenario: Image runs with env-only configuration
- **WHEN** the built image is started with the required environment variables set
- **THEN** the API serves requests without any baked-in or file-based configuration except the mounted JWKS

### Requirement: Images built and published by CI
The `ci` workflow (`.github/workflows/ci.yml`) SHALL run the api, reporting and client test suites and the dashboard build on every pull request and every push to `main`, and SHALL build both images. On pushes to `main` it SHALL push them to the platform's Azure Container Registry as `ouroboros/api` and `ouroboros/reporting`, tagged `sha-<short>` and `main`, authenticating through GitHub OIDC with no stored registry secret. When the `ACR_LOGIN_SERVER` repository variable is unset the images SHALL be built but not pushed.

#### Scenario: Push to main publishes both images
- **WHEN** a commit lands on `main` with the registry variables configured
- **THEN** `ouroboros/api` and `ouroboros/reporting` are pushed with the `sha-<short>` and `main` tags

#### Scenario: Pull request only builds
- **WHEN** the workflow runs for a pull request
- **THEN** tests run and both images build, and nothing is pushed

### Requirement: Kustomize manifests synced by Argo CD
The system SHALL provide Kubernetes manifests as plain Kustomize under `deploy/apps/ouroboros`: `base/` (namespace, API and reporting Deployments and Services, the database cluster, the registry pull ExternalSecret, the JWKS ConfigMap, NetworkPolicies), `routes/` (HTTPRoutes and their SecurityPolicies) and `overlays/rancherlocal/` (hostname, allow-list, JWKS, registry and image tags, reporting environment). The platform's Argo CD SHALL sync `deploy/apps/ouroboros/overlays/rancherlocal`, which includes `base` and `routes`; deploying a build SHALL be a change of the image tags in the overlay. No secret value SHALL be committed: the registry pull credential is an ExternalSecret reading the `azure-kv` ClusterSecretStore, other secrets are referenced by name only, and the JWKS in Git is public key material.

#### Scenario: Overlay renders the whole application
- **WHEN** `kustomize build deploy/apps/ouroboros/overlays/rancherlocal` runs
- **THEN** the output contains the namespace, both Deployments and Services, the database cluster, the HTTPRoutes and the SecurityPolicies with the overlay's hostname and image tags

#### Scenario: Sync brings up the stack
- **WHEN** Argo CD syncs the overlay to the rancherlocal cluster
- **THEN** the database, the API and the reporting service reach Ready

#### Scenario: Secrets not in Git
- **WHEN** the manifests are inspected
- **THEN** they reference Secret names only; no password, API key or token value is present

### Requirement: CloudNativePG database
PostgreSQL SHALL run as a CloudNativePG `Cluster` named `ouroboros-db` (PostgreSQL 17, one instance on the local cluster, persistent storage) in the `ouroboros` namespace. Both services SHALL read `DATABASE_URL` from the `uri` key of the operator-generated `ouroboros-db-app` Secret, so no database password exists outside the cluster. A NetworkPolicy SHALL admit database connections only from the API and reporting pods and from the operator.

#### Scenario: Data survives pod restart
- **WHEN** the database pod is deleted and rescheduled
- **THEN** previously ingested events are still present, backed by the cluster's persistent volume

#### Scenario: Plain connection string accepted
- **WHEN** a service starts with the `postgresql://` URL from the operator's Secret
- **THEN** it connects using its bundled psycopg driver

### Requirement: Gateway routing and access policies
The application SHALL be exposed on one hostname through HTTPRoutes attached to the platform Gateway (Envoy Gateway). `/v1/telemetry` and `/v1/otel` SHALL route to the API under a route-level SecurityPolicy that bypasses the Gateway login, because the API validates bearer tokens itself. `/v1/proposals/github-webhook` SHALL route to the reporting service under its own allow policy, because the service verifies the webhook signature itself. Every other path SHALL route to the reporting service behind the Gateway login plus an account allow-list patched per environment. A NetworkPolicy SHALL admit traffic to the API and reporting pods only from the Gateway's proxy pods.

#### Scenario: Ingestion bypasses the login
- **WHEN** a client posts to `/v1/telemetry/events` on the application hostname with a bearer token
- **THEN** the request reaches the API without a login redirect

#### Scenario: Dashboard behind login and allow-list
- **WHEN** a browser opens the application hostname
- **THEN** the Gateway login is required and only allow-listed accounts reach the dashboard

### Requirement: Probe wiring
The API Deployment SHALL use `GET /healthz` as its liveness probe and `GET /readyz` as its readiness probe, so the API does not receive traffic until the database is reachable.

#### Scenario: API not ready before database
- **WHEN** the API starts while Postgres is not yet accepting connections
- **THEN** the API pod reports NotReady and receives no traffic until `/readyz` succeeds

### Requirement: Reporting service deployment
The system SHALL provide a container image for the reporting service (multi-stage build from the repository root: the Vite/Tailwind SPA is compiled, then served by the FastAPI app from `STATIC_DIR`) and a single-replica Deployment in `deploy/apps/ouroboros/base` with liveness/readiness probes on `/healthz` and `/readyz` (readiness gated on the database and on the schema version the service requires). The Deployment SHALL carry `AGGREGATE_REFRESH_SECONDS`, `RETENTION_SENSITIVE_DAYS`, `RETENTION_INTERNAL_DAYS` and `ANALYST_SCHEDULE`, with `TRUSTED_PROXY_CIDRS`, `ADMIN_EMAILS` and `PUBLIC_BASE_URL` set per environment overlay, and SHALL reference `ANTHROPIC_API_KEY` and the GitHub App settings (`GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`) from optional Kubernetes Secrets so the pod starts without them.

#### Scenario: Reporting comes up with the sync
- **WHEN** Argo CD syncs the ouroboros application
- **THEN** the reporting Deployment reaches Ready and the dashboard is reachable through the Gateway host

#### Scenario: Reporting readiness gated on database
- **WHEN** the reporting pod starts while Postgres is unreachable
- **THEN** the pod reports NotReady until `/readyz` succeeds

#### Scenario: Reporting readiness gated on schema
- **WHEN** the reporting pod starts before the API applied the latest migration
- **THEN** it reports NotReady and receives no traffic until the version is present

#### Scenario: Starts without the improvement-loop secrets
- **WHEN** the Anthropic and GitHub Secrets do not exist
- **THEN** the pod still starts, with the analyst and PR creation disabled

### Requirement: Dev-mode token tooling
The repository SHALL provide `api/scripts/gen_jwks.py` to generate a dev-mode RS256 keypair (public `jwks.json` plus a private key that is never committed or deployed) and `api/scripts/dev_token.py` to mint a bearer token from it, so that a batch can be posted to a deployment running `AUTH_MODE=dev` whose JWKS ConfigMap holds the public half.

#### Scenario: Curl round-trip lands in Postgres
- **WHEN** a token minted with `dev_token.py` is used to POST a sample batch to `/v1/telemetry/events` on a deployment that trusts the matching JWKS
- **THEN** the request receives `202 Accepted` and a matching row with the token's `oid`/`upn` is present in the events table
