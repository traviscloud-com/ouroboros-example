# plugin-packaging

## Purpose

The Ouroboros Claude Code plugin: the installable unit bundling capture-hook wiring, the launcher, and the feedback-protocol skill — with disclosure at install time, inert degradation without the SDK, and repo-as-marketplace distribution.

## Requirements

### Requirement: Plugin manifest with disclosure
The repository SHALL provide a Claude Code plugin at `plugin/` with a `.claude-plugin/plugin.json` manifest (version 0.2.0) naming the plugin `ouroboros` and carrying a description that discloses every class of data collected — turn text, tool metadata, instruction-file paths and hashes, subagent and compaction lifecycle, model switches, and (when the user runs `setup_otel`) token and cost telemetry — and names every toggle: `OUROBOROS_TELEMETRY=off`, `OUROBOROS_CAPTURE_PROMPTS`, `OUROBOROS_CAPTURE_TOOL_CONTENT`, and the `OTEL_LOG_*` flags.

#### Scenario: Manifest is valid and disclosing
- **WHEN** the plugin manifest is parsed
- **THEN** it declares name `ouroboros`, version `0.2.0`, and a description mentioning telemetry capture and the opt-out variable

#### Scenario: Manifest lists classes and toggles
- **WHEN** a user reads the plugin description or `plugin/README.md`
- **THEN** every collected class is named with its default and the variable that disables it

### Requirement: Hooks activate on install
The plugin SHALL ship hook configuration registering handlers for every event the entrypoint supports (`SessionStart`, `SessionEnd`, `UserPromptSubmit`, `UserPromptExpansion`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch`, `SubagentStart`, `SubagentStop`, `PreCompact`, `PostCompact`, `InstructionsLoaded`, `PreModelSwitch`, `PostModelSwitch`, `Stop`) that invoke the plugin's launcher script via `${CLAUDE_PLUGIN_ROOT}` with `async: true` and a 10-second timeout, so installing (or force-enabling) the plugin activates capture without any manual settings edit.

#### Scenario: Hook config wires every event to the launcher
- **WHEN** the plugin's hook configuration is inspected
- **THEN** UserPromptSubmit, Stop and every other supported event invoke the launcher script through a `${CLAUDE_PLUGIN_ROOT}` path

#### Scenario: Launcher behaves as the hook entrypoint
- **WHEN** a hook event JSON is piped to the launcher exactly as the hook configuration invokes it
- **THEN** the interaction cache and hook spool are updated identically to invoking `ouroboros-hook` directly

#### Scenario: Install enables all captures
- **WHEN** the plugin is installed and a session uses a tool, a subagent and a compaction
- **THEN** hook-event records for each are spooled without any manual settings change

#### Scenario: Hooks never block the session
- **WHEN** the launcher is slow or Python is missing
- **THEN** Claude Code continues normally because hooks are asynchronous and the launcher exits 0

### Requirement: Launcher degrades to inert without the SDK
The launcher SHALL be standard-library-only and SHALL exit 0 producing no output and writing no files when `ouroboros-client` is not importable — an installed plugin without the SDK must be completely inert and can never disrupt a session.

#### Scenario: Missing SDK is a silent no-op
- **WHEN** the launcher runs under a Python environment without ouroboros-client and receives a valid hook event
- **THEN** it exits 0, writes nothing, and emits nothing

#### Scenario: Malformed input still exits zero
- **WHEN** the launcher receives invalid JSON
- **THEN** it exits 0

### Requirement: Repo serves as the plugin marketplace
The repository SHALL provide a root `.claude-plugin/marketplace.json` listing the `ouroboros` plugin with its in-repo source path, such that adding this repository as a marketplace and installing `ouroboros` from it succeeds; `plugin/README.md` SHALL document the install steps including the SDK pip prerequisite and the managed-rollout (`enabledPlugins`) path for organizations.

#### Scenario: Marketplace lists the plugin
- **WHEN** the marketplace manifest is parsed
- **THEN** it references the plugin source under `plugin/` by the name `ouroboros`
