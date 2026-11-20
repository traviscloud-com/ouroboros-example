# deployment (delta)

## ADDED Requirements

### Requirement: Reporting service deployment
The system SHALL provide a container image for the reporting service (multi-stage build: SPA assets compiled then served by the FastAPI app) and Kubernetes manifests deploying it to the k3s cluster with liveness/readiness probes wired to `/healthz` and `/readyz` and an ingress host distinct from the ingestion API, such that `kubectl apply -f deploy/` brings up ingestion, Postgres, and reporting together.

#### Scenario: Reporting stack comes up with single apply
- **WHEN** `kubectl apply -f deploy/` runs against the rancher-desktop context
- **THEN** the reporting Deployment reaches Ready and the dashboard is reachable through its ingress host

#### Scenario: Reporting readiness gated on database
- **WHEN** the reporting pod starts while Postgres is unreachable
- **THEN** the pod reports NotReady until `/readyz` succeeds
