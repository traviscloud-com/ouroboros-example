# deployment (delta)

## MODIFIED Requirements

### Requirement: Reporting service deployment
The system SHALL provide a container image for the reporting service (multi-stage build from the repository root: the Vite/Tailwind SPA is compiled, then served by the FastAPI app from `STATIC_DIR`) and a single-replica Deployment (the scheduler runs in-process) in `deploy/apps/ouroboros/base` with liveness/readiness probes on `/healthz` and `/readyz` (readiness gated on the database and on the schema version the service requires) and resource limits sized for view refreshes. The Deployment SHALL carry `AGGREGATE_REFRESH_SECONDS`, `RETENTION_SENSITIVE_DAYS`, `RETENTION_INTERNAL_DAYS` and `ANALYST_SCHEDULE`, with `TRUSTED_PROXY_CIDRS`, `ADMIN_EMAILS` and `PUBLIC_BASE_URL` set per environment overlay, and SHALL reference `ANTHROPIC_API_KEY` and the GitHub App settings (`GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`) from optional Kubernetes Secrets so the pod starts without them.

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

#### Scenario: Secrets not in Git
- **WHEN** the manifests under `deploy/apps/ouroboros` are inspected
- **THEN** they reference Secret names only; no API key or token value is present

## ADDED Requirements

### Requirement: Gateway routing for unauthenticated ingestion paths
The `ouroboros-api` HTTPRoute SHALL send `/v1/telemetry/*` and `/v1/otel/*` to the API service bypassing oauth2-proxy, with a 30-second request timeout and an 8 MiB body limit, and SHALL not forward `X-Auth-Request-Email` on those paths. `/v1/proposals/github-webhook` SHALL reach the reporting service through its own route without the Gateway login, because the service verifies the webhook signature itself. Every other path SHALL continue through oauth2-proxy to the reporting service.

#### Scenario: OTel export reaches the API
- **WHEN** Claude Code posts to `https://ouroboros.rancherlocal.azure.example.com/v1/otel/v1/metrics` with a bearer token
- **THEN** the API receives it without a Google login redirect

#### Scenario: Dashboard still behind login
- **WHEN** an anonymous browser opens `/overview`
- **THEN** oauth2-proxy redirects to Google login

### Requirement: Database capacity and backups
The `ouroboros-db` CNPG Cluster SHALL have storage sized for hook and usage data (20 Gi with in-use resize enabled) and a `ScheduledBackup` to the cluster's object store, because proposals, approvals and outcomes are not reproducible from clients; the restore procedure SHALL be documented.

#### Scenario: Backup present
- **WHEN** the cluster is inspected after rollout
- **THEN** a completed CNPG backup newer than 24 hours exists

### Requirement: Rollout verification on rancherlocal
The rollout SHALL be verified by: both pods Ready, one OTel metrics batch and one hook-event batch accepted and visible in `usage_facts`/`sessions`, one analytics refresh completed, one redacted and one verbatim export, and one analyst dry run producing a proposal without opening a PR; results recorded under the change directory.

#### Scenario: End-to-end check recorded
- **WHEN** the rollout checklist is executed
- **THEN** `openspec/changes/analytics-platform/rollout.md` records each step with timestamps and outcomes
