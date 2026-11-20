# dashboard-ui

## Purpose

The web dashboard for exploring telemetry: a routed single-page application with a leadership overview and an engineering workspace, where raw events can be searched and inspected, skill usage and outcomes are graphed, and user sentiment is surfaced, served as a static SPA by the reporting service.

## Requirements

### Requirement: Search and browse view
The dashboard SHALL provide the event search view at `/engineering/inspector` inside the Engineering workspace, using the shared data table component (TanStack Table), URL-held filters for the reporting API's filters (skill, status, session, user, error code, event-time range, full-text query) and pagination, and SHALL let the user open an event to see its full detail including feedback and interaction text subject to server-side redaction.

#### Scenario: Filter and inspect an event
- **WHEN** an engineer filters by a skill and status and opens a result row
- **THEN** the event detail shows all stored fields including notes, corrections, and any prompt/response text

#### Scenario: Free-text search from the UI
- **WHEN** the user types a query into the search box
- **THEN** the list updates to events whose feedback or interaction text matches via full-text search

### Requirement: Reporting view
The dashboard SHALL provide reporting through `/overview` (leadership KPIs with period comparison, adoption, outcomes over time) and `/engineering/artifacts` (per-skill/agent table with outcome rates, tokens and cost), both driven by the analytics endpoints; the standalone `ReportsView` is removed.

#### Scenario: Most-used skills graphed with outcome rates
- **WHEN** the user opens `/overview`
- **THEN** adoption per skill is charted for the selected range together with success/failure/user-corrected outcomes

#### Scenario: Global overview visible
- **WHEN** the user opens `/overview`
- **THEN** overview totals with period-comparison deltas and an activity-over-time chart are displayed for the selected range

### Requirement: Sentiment view
The dashboard SHALL show sentiment on `/overview` as a trend over the selected range with the comparison period, filterable by skill, driven by the persisted daily sentiment rollups; the standalone `SentimentView` is removed.

#### Scenario: Sentiment trend on the overview
- **WHEN** the user opens `/overview`
- **THEN** the sentiment trend for the selected range is shown alongside the previous period

#### Scenario: Per-skill sentiment
- **WHEN** the user selects a skill in the filterbar on `/overview`
- **THEN** the sentiment trend reflects only that skill's events

### Requirement: Served by the reporting service
The dashboard SHALL be a static single-page application built at image-build time with Vite and Tailwind v4 (shadcn/ui components compiled in), served by the reporting service at `/` on the same origin as the reporting API with an SPA fallback for react-router paths, reachable through the Gateway behind oauth2-proxy.

#### Scenario: SPA loads through the Gateway
- **WHEN** a browser requests the dashboard host through the Gateway after login
- **THEN** the SPA loads and its data requests hit the same-origin reporting API

#### Scenario: Build pipeline unchanged in shape
- **WHEN** the reporting image is built
- **THEN** stage one runs `npm ci && npm run build` with Tailwind processing and the result is served at `/`
