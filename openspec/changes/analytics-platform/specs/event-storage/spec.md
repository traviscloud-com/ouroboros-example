# event-storage (delta)

## MODIFIED Requirements

### Requirement: Idempotent schema application
The API SHALL apply versioned SQL migrations from `api/migrations/NNNN_<name>.sql` in order on startup, recording each in `schema_migrations(version, name, applied_at, checksum)` and running each file in its own transaction. Migration `0001` SHALL be the baseline schema so existing databases converge; every migration SHALL be safe to re-run, and a recorded migration whose file checksum has changed SHALL be an error. The reporting service SHALL NOT apply migrations and SHALL report not-ready until `schema_migrations` reaches the version it requires.

#### Scenario: Re-applying schema preserves data
- **WHEN** the migrations run against a database that already has the events table and rows
- **THEN** they succeed without error and existing rows are unchanged

#### Scenario: Fresh database
- **WHEN** the API starts against an empty database
- **THEN** all migrations apply in order and `schema_migrations` lists each with its checksum

#### Scenario: Existing database converges
- **WHEN** the API starts against a database that holds only the original `events` table
- **THEN** migration 0001 is a no-op, later migrations apply, and existing `events` rows are untouched

#### Scenario: Reporting waits for schema
- **WHEN** the reporting pod starts before the API has applied the latest migration
- **THEN** `/readyz` fails until the required version is present

## ADDED Requirements

### Requirement: Normalized tables and registry
Storage SHALL include `hook_events_raw`, `sessions`, `turns`, `tool_calls`, `subagent_runs`, `compactions`, `instructions_loaded`, `skill_activations`, `usage_facts`, `model_switches`, `turn_errors`, `hook_runs`, `transcript_lines`, `artifacts`, `artifact_versions`, `improvement_proposals`, `improvement_outcomes`, `analyst_runs`, `principals` and `audit_log`, each stamped with identity and `received_at` where ingested. `events` remains the skill-outcome fact table with new nullable `prompt_id`, `turn_id`, `artifact_id` links.

#### Scenario: Events contract preserved
- **WHEN** a v1.0 or v1.1 outcome event is ingested by the existing endpoint
- **THEN** it is stored exactly as before with the new link columns null

### Requirement: Indexes for analytics and search
Storage SHALL provide B-tree indexes on `(session_id)`, `(prompt_id)` and event-time columns of every fact table, GIN indexes on JSONB payload/attribute columns, and generated `tsvector` columns with GIN indexes over `turns.prompt_text`, `turns.response_text` and the textual parts of `events.feedback`/`events.interaction` so that `q` search uses full-text matching.

#### Scenario: Search uses FTS
- **WHEN** `/v1/reports/events?q="terraform plan"` is requested
- **THEN** the query plan uses the GIN tsvector index and returns ranked matches

### Requirement: Retention by sensitivity class
Every column SHALL have a sensitivity class (`public`, `internal`, `sensitive`); a nightly job SHALL null `sensitive` text older than `RETENTION_SENSITIVE_DAYS` (default 30), including the text keys of `hook_events_raw.payload` and `transcript_lines.content`. After `RETENTION_INTERNAL_DAYS` (default 365) it SHALL delete the replayable and lifecycle stores (`hook_events_raw`, `transcript_lines`, `hook_runs`, `turn_errors`, `model_switches`) and null the text of superseded `artifact_versions`. The fact tables the aggregates are recomputed from SHALL be kept, so `public` aggregates survive refreshes. Each run SHALL be recorded in `audit_log`.

#### Scenario: Prompt text expires, structure remains
- **WHEN** a turn is older than the sensitive window
- **THEN** its `prompt_text`/`response_text` are null while `prompt_length`, timestamps, tool calls and usage remain
