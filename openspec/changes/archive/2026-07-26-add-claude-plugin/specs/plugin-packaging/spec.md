# plugin-packaging (delta)

## ADDED Requirements

### Requirement: Plugin manifest with disclosure
The repository SHALL provide a Claude Code plugin at `plugin/` with a `.claude-plugin/plugin.json` manifest naming the plugin `ouroboros` and carrying a description that discloses telemetry capture (prompts/responses and skill outcomes) and names the `OUROBOROS_TELEMETRY=off` opt-out.

#### Scenario: Manifest is valid and disclosing
- **WHEN** the plugin manifest is parsed
- **THEN** it declares name `ouroboros`, a version, and a description mentioning telemetry capture and the opt-out variable

### Requirement: Hooks activate on install
The plugin SHALL ship hook configuration registering `UserPromptSubmit` and `Stop` handlers that invoke the plugin's launcher script via `${CLAUDE_PLUGIN_ROOT}`, so installing (or force-enabling) the plugin activates conversation capture without any manual settings edit.

#### Scenario: Hook config wires both events to the launcher
- **WHEN** the plugin's hook configuration is inspected
- **THEN** both UserPromptSubmit and Stop invoke the launcher script through a `${CLAUDE_PLUGIN_ROOT}` path

#### Scenario: Launcher behaves as the hook entrypoint
- **WHEN** a hook event JSON is piped to the launcher exactly as the hook configuration invokes it
- **THEN** the interaction cache is updated identically to invoking `ouroboros-hook` directly

### Requirement: Launcher degrades to inert without the SDK
The launcher SHALL be standard-library-only and SHALL exit 0 producing no output and writing no files when `ouroboros-client` is not importable — an installed plugin without the SDK must be completely inert and can never disrupt a session.

#### Scenario: Missing SDK is a silent no-op
- **WHEN** the launcher runs under a Python environment without ouroboros-client and receives a valid hook event
- **THEN** it exits 0, writes nothing, and emits nothing

#### Scenario: Malformed input still exits zero
- **WHEN** the launcher receives invalid JSON
- **THEN** it exits 0

### Requirement: Repo serves as the plugin marketplace
The repository SHALL provide a root `.claude-plugin/marketplace.json` listing the `ouroboros` plugin with its in-repo source path, such that adding this repository as a marketplace and installing `ouroboros` from it succeeds; the README SHALL document the install steps including the SDK pip prerequisite and the managed-rollout (`enabledPlugins`) path for organizations.

#### Scenario: Marketplace lists the plugin
- **WHEN** the marketplace manifest is parsed
- **THEN** it references the plugin source under `plugin/` by the name `ouroboros`

### Requirement: Multi-turn session trace verified end-to-end
The change SHALL include a scripted verification that a single session with multiple captured turns and multiple skill outcome reports produces correlated rows in the deployed stack: all rows share the `session_id`, each outcome that had a captured turn carries the correct interaction pair, and the trace is retrievable through the reporting API session view.

#### Scenario: Session trace lands correlated
- **WHEN** the verification script simulates several turn captures and records several outcomes in one session against the live stack
- **THEN** the reporting API returns all events for that session_id with the expected statuses and interaction pairings
