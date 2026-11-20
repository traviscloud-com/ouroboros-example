# improvement-loop (delta)

## ADDED Requirements

### Requirement: Scheduled analyst produces evidence-backed proposals
The reporting service SHALL run an analyst job on `ANALYST_SCHEDULE` (and on admin request) that, per target artifact, assembles an evidence pack from analytics views, the latest artifact version and example corrections (redacted unless `ANALYST_SEES_SENSITIVE=on`), calls Claude via the Claude API with a fixed prompt, and writes `improvement_proposals` rows with `target_artifact_id`, `base_version_id`, `rationale`, `expected_effect`, `evidence_event_ids`, a unified `diff`, `risk_class` (`low|medium|high`) and `created_by=loop`. Proposals SHALL aim to reduce always-on tokens and correction rate; outputs failing the JSON schema SHALL be discarded and logged.

#### Scenario: Proposal targets bloat
- **WHEN** an artifact is never invoked in the window but loaded in most sessions
- **THEN** the analyst proposes a `low` risk diff trimming or gating its description, citing the sessions and token cost as evidence

#### Scenario: No sensitive text by default
- **WHEN** the analyst runs with default settings
- **THEN** the prompt sent to Claude contains no verbatim user text, only aggregates, artifact text and redaction markers

### Requirement: Human approval gate and PR creation
No proposal SHALL be applied automatically in v1. Approval SHALL require `admin` (any risk) or `engineer` (`low` only); approval SHALL set status `approved` and write an `audit_log` row, after which the service SHALL open a pull request against the owning repository using a GitHub App installation token (preferred) or a token supplied by Kubernetes secret, record `pr_url` and move the proposal to `pr_opened`, or to `failed` with the reason when the PR cannot be created. Without a GitHub credential the approval SHALL still be recorded and the proposal SHALL report "approved, PR creation not configured". Rejection SHALL record `decided_by`/`decided_at`. Status becomes `applied` when the PR is merged (HMAC-verified webhook or poll) with the merge SHA stored, or `failed` when it is closed unmerged; every transition SHALL be written to `audit_log`.

#### Scenario: Approval creates PR
- **WHEN** an admin approves a proposal for `plugin/skills/telemetry-reporting/SKILL.md`
- **THEN** a PR exists on the owning repo containing exactly the stored diff and a link to the proposal, and the proposal shows the PR URL with status `pr_opened`

#### Scenario: Approval without GitHub configured
- **WHEN** an admin approves a proposal and neither a GitHub App nor a token is configured
- **THEN** the proposal is `approved`, the approval is in `audit_log`, and the proposal shows "approved, PR creation not configured"

#### Scenario: Engineer cannot approve high risk
- **WHEN** an engineer calls `POST /v1/proposals/{id}/approve` on a `high` risk proposal
- **THEN** the service returns 403 and the proposal remains `proposed`

### Requirement: Outcome measurement
For each `applied` proposal the scheduler SHALL, once an after-window of `OUTCOME_WINDOW_DAYS` (default 14) has elapsed, store `improvement_outcomes` with the same metric set used as evidence for equal-length before and after windows and a verdict `improved|neutral|worse|insufficient_data`, surfaced on the proposal and on `/overview`.

#### Scenario: Verdict after window
- **WHEN** 14 days have passed since a proposal was applied and the artifact's correction rate fell while invocations held
- **THEN** an outcome row exists with verdict `improved` and both metric snapshots

#### Scenario: Too little data
- **WHEN** the artifact had fewer invocations than the minimum sample in either window
- **THEN** the verdict is `insufficient_data` and no improvement is claimed

### Requirement: Autonomy is deferred and gated
Automatic approval SHALL be absent in v1; the design SHALL reserve a setting that could later enable auto-approval only for `low` risk proposals once a configured number of prior `low` proposals have verdict `improved` and none `worse`.

#### Scenario: No auto-apply path exists
- **WHEN** the analyst creates proposals
- **THEN** none transitions past `proposed` without a human action recorded in `audit_log`
