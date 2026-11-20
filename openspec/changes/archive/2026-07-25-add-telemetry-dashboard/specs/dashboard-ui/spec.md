# dashboard-ui (delta)

## ADDED Requirements

### Requirement: Search and browse view
The dashboard SHALL provide a search view listing telemetry events with controls for the reporting API's filters (skill, status, session, user, time range, free text) and pagination, and SHALL let the user open an event to see its full detail including feedback and interaction text.

#### Scenario: Filter and inspect an event
- **WHEN** the user filters by a skill and status and opens a result row
- **THEN** the event detail shows all stored fields including notes, corrections, and any prompt/response text

#### Scenario: Free-text search from the UI
- **WHEN** the user types a query into the search box
- **THEN** the list updates to events whose feedback or interaction text matches

### Requirement: Reporting view
The dashboard SHALL provide a reporting view with graphs of the most-used skills, per-skill error and success rates, and a global overview (event totals, distinct sessions/users/skills, status breakdown, activity over time), driven by the reporting API aggregates.

#### Scenario: Most-used skills graphed with outcome rates
- **WHEN** the user opens the reporting view
- **THEN** a chart shows skills ordered by usage with their success/failure/user-corrected rates visible per skill

#### Scenario: Global overview visible
- **WHEN** the user opens the reporting view
- **THEN** overview totals and an activity-over-time chart are displayed for the selected time range

### Requirement: Sentiment view
The dashboard SHALL provide a sentiment view showing overall user sentiment and its trend, per-skill sentiment alongside correction and failure counts, and a per-session breakdown, driven by the sentiment rollups endpoint.

#### Scenario: Per-skill sentiment with correction burden
- **WHEN** the user opens the sentiment view
- **THEN** each skill shows its blended sentiment score together with its user-corrected and failure counts

#### Scenario: Session drill-down
- **WHEN** the user selects a session in the sentiment view
- **THEN** the session's mean sentiment and its correction/failure counts are shown

### Requirement: Served by the reporting service
The dashboard SHALL be a static single-page application built at image-build time, served by the reporting service at `/` on the same origin as the reporting API, reachable through the cluster ingress.

#### Scenario: SPA loads through the ingress
- **WHEN** a browser requests the dashboard host through the cluster ingress
- **THEN** the SPA loads and its data requests hit the same-origin reporting API
