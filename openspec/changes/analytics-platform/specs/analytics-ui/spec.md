# analytics-ui (delta)

## ADDED Requirements

### Requirement: SPA shell with routing and filterbar
The dashboard SHALL be a Vite + React SPA built on Tailwind v4 and a copy-into-source component system (shadcn/ui, charts on Recharts), with `react-router` routes `/overview`, `/engineering/*`, `/proposals`, `/proposals/:id`, `/settings/*`, a sidebar/topbar shell, class-based dark mode, and a filterbar (date range, comparison period, skill/agent/user selectors) whose state lives in URL search params.

#### Scenario: Filters are shareable
- **WHEN** a user sets a date range and comparison and copies the URL
- **THEN** opening that URL reproduces the same filters and data on every page

#### Scenario: Role-based landing
- **WHEN** the SPA loads and `/v1/me` returns `landing`
- **THEN** the user is routed there and navigation shows only pages their role can use

### Requirement: Leadership overview
`/overview` SHALL show, for the selected range with period-comparison deltas: active users, sessions, turns, success and correction rates, cost (USD) and tokens, sentiment trend, the context-bloat index trend, adoption per skill/agent, outcomes over time, cost by model and skill, and top correction themes — using only redaction-safe fields.

#### Scenario: KPI deltas
- **WHEN** the range is the last 7 days with `compare_to=previous`
- **THEN** each KPI card shows the current value and the percentage change versus the previous 7 days

#### Scenario: No verbatim text on the overview
- **WHEN** any principal views `/overview`
- **THEN** no prompt, response or correction text is rendered

### Requirement: Engineering workspace
`/engineering` SHALL provide: an artifact registry table and detail (versions, always-on tokens, invocations, outcomes, cost per invocation, never-invoked flag, proposals); failure analysis (error codes, tool failures, latency percentiles) with drill-down to events; a session list and session timeline (turns, tool calls, subagents, compactions, instructions loaded, model switches, outcomes) with a prompt/context inspector that honors server redaction; and an export page for the modeling datasets.

#### Scenario: Drill from aggregate to evidence
- **WHEN** an engineer clicks a failure bar for `error_code=TIMEOUT`
- **THEN** the events inspector opens pre-filtered to that code and range

#### Scenario: Session timeline is complete
- **WHEN** an engineer opens a session that had a subagent run and an auto compaction
- **THEN** the timeline shows the subagent span and the compaction with pre/post tokens in chronological order among the turns

### Requirement: Proposals and settings pages
`/proposals` SHALL list improvement proposals with status, target artifact, expected effect and risk class, and show a diff viewer with evidence links and approve/reject actions permitted by role; `/settings/users` SHALL let admins manage principals; `/settings/general` SHALL hold theme and default-range preferences.

#### Scenario: Approve opens a PR
- **WHEN** an admin approves a proposal
- **THEN** the UI shows the resulting PR URL and the proposal status changes to `pr_opened`

#### Scenario: Engineer limited by risk class
- **WHEN** an engineer views a `high` risk proposal
- **THEN** approve is disabled with an explanation and reject remains available
