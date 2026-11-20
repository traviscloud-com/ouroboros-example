# session-model (delta)

## ADDED Requirements

### Requirement: Structured hook-event ingestion endpoint
The ingestion API SHALL expose `POST /v1/telemetry/hook-events` accepting a batch of hook event records `{hook_event_id, session_id, prompt_id?, agent_id?, hook_event_name, client_ts, client_version, payload}` under the same bearer auth as `/v1/telemetry/events`, SHALL accept `client_ts` up to `HOOK_EVENTS_MAX_AGE_SECONDS` (default 7 days) old, SHALL store every accepted record in `hook_events_raw` idempotently by `hook_event_id`, and SHALL return 202 with per-record accounting (`accepted`, `duplicates`, `rejected` with a reason), so one invalid or stale record does not fail the batch; only a malformed envelope fails the whole request.

#### Scenario: Offline batch accepted
- **WHEN** a batch spooled 3 days ago is uploaded
- **THEN** the API returns 202 and the rows carry the original `client_ts` as event time with `received_at` set to now

#### Scenario: Replayed batch is a no-op
- **WHEN** the same batch is uploaded twice
- **THEN** no duplicate rows exist in `hook_events_raw` or the normalized tables

### Requirement: Normalization into session, turn and lifecycle tables
The API SHALL project hook events into `sessions`, `turns`, `tool_calls`, `subagent_runs`, `compactions`, `instructions_loaded`, `model_switches` and `hook_runs` using order-tolerant upserts keyed on natural ids (`session_id`, `prompt_id`, `tool_use_id`, `agent_id`), filling only null columns on conflict so hook and OTel arrivals in any order converge.

#### Scenario: Session assembled from lifecycle events
- **WHEN** `SessionStart`, two `UserPromptSubmit`/`Stop` pairs, one `PostToolUse` and `SessionEnd` arrive for one session
- **THEN** one `sessions` row with start/end/reason, two `turns` rows ordered by `seq`, and one `tool_calls` row linked to the right `prompt_id` exist

#### Scenario: Stop before UserPromptSubmit
- **WHEN** a `Stop` event is ingested before its `UserPromptSubmit` (out-of-order upload)
- **THEN** the turn row is created from `Stop` and later completed by `UserPromptSubmit` without error or duplication

### Requirement: Skill outcomes link to turns and artifacts
The `events` table SHALL gain nullable `prompt_id`, `turn_id` and `artifact_id` link columns, exposed by the reporting API and the export. An outcome event MAY carry a `prompt_id`; the client SHALL send it when the outcome has a captured interaction pair recorded under a prompt id. When the `prompt_id` matches an ingested turn the API SHALL populate `turn_id`, and when the turn is ingested after the outcome the API SHALL fill `turn_id` at that point. When `skill_name` matches a registered skill artifact the API SHALL populate `artifact_id` (the most recently seen artifact of that name). Outcomes without a `prompt_id` SHALL keep a null `turn_id` and join to their session by `session_id`. Hook events SHALL never create `events` rows.

#### Scenario: Outcome joined to its turn
- **WHEN** a skill reports an outcome with `prompt_id` equal to an existing turn
- **THEN** the stored event references that turn

#### Scenario: Outcome arrives before its turn
- **WHEN** an outcome with a `prompt_id` is ingested before the hook events or transcript lines that create that turn
- **THEN** the event is stored with a null `turn_id` and references the turn once it is ingested

#### Scenario: Outcome joined to its skill artifact
- **WHEN** an outcome is reported for a `skill_name` that matches a registered skill artifact
- **THEN** the stored event references that artifact, and an outcome for an unregistered skill keeps a null `artifact_id`

#### Scenario: No synthetic outcomes
- **WHEN** a session produces hook events but no skill reports
- **THEN** the `events` table gains no rows for that session

### Requirement: Instruction loads recorded with content identity
`InstructionsLoaded` hook events SHALL be stored with `file_path` relative to the session's working directory, `content_hash`, `memory_type`, `load_reason`, `trigger_file_path` and `parent_file_path`, and SHALL be linked to an `artifacts` row by `(kind, path, content_hash)` when one exists.

#### Scenario: CLAUDE.md load attributed to a version
- **WHEN** an `InstructionsLoaded` event arrives for `.claude/CLAUDE.md` with a hash matching a registered `artifact_versions` row
- **THEN** the `instructions_loaded` row references that artifact and the artifact's always-on tokens count toward the session's context-bloat inputs
