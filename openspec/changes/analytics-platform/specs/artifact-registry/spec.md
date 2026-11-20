# artifact-registry (delta)

## ADDED Requirements

### Requirement: Versioned artifact registry
The system SHALL maintain `artifacts` (kind `skill|hook|agent|command|instructions|rule|plugin`, name, owner repo, path, source) and `artifact_versions` (content hash, content text, frontmatter, estimated always-on tokens, estimated body tokens, seen-from/seen-until, optional commit SHA), and SHALL treat artifact text as `internal` class, not `sensitive`.

#### Scenario: New version on content change
- **WHEN** a skill whose SKILL.md hash differs from the latest registered version is synced or observed
- **THEN** a new `artifact_versions` row is created, the previous row's `seen_until` is set, and the artifact's `last_seen` is updated

#### Scenario: Same content is one version
- **WHEN** the same hash is observed again from another session
- **THEN** no new version row is created and `seen_until` advances

### Requirement: Registry population from hooks, OTel and explicit sync
Artifacts SHALL be registered automatically from `InstructionsLoaded` hook events (instructions/rules, with client-computed hash), from `skill_activated`/`UserPromptExpansion`/`events.skill_name` (skills and commands, name only until synced), and in full by `python3 -m ouroboros_client.sync_artifacts <path>` which uploads frontmatter, body, content hash and token estimates for every skill, agent, hook and instructions file under a plugin or repository to `POST /v1/telemetry/artifacts`.

#### Scenario: Skill observed before sync
- **WHEN** a `skill_activated` event names a skill that has never been synced
- **THEN** an `artifacts` row exists with the name and kind and no version text, flagged as unsynced in the registry UI

#### Scenario: Sync fills token estimates
- **WHEN** `sync_artifacts` runs over the `plugin/` directory
- **THEN** every skill has a version with `estimated_tokens_always_on` derived from `description` + `when_to_use` (listing cap 1,536 chars) and `estimated_tokens_body` from the body

### Requirement: Per-artifact outcome and usage attribution
The registry SHALL expose, per artifact and date range, invocations, outcome counts and correction rate (from `events` and `skill_activations`), cost and tokens per invocation (from `usage_facts` by `skill.name`/`agent.name`), sessions in which it was loaded, and a never-invoked flag when it was loaded but never activated in the range.

#### Scenario: Never-invoked skill surfaced
- **WHEN** a skill's description is loaded in 40 sessions in the range and `skill_activations` has no row for it
- **THEN** the registry lists it as never-invoked with its always-on token cost multiplied by session count
