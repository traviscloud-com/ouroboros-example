# hook-capture (delta)

## MODIFIED Requirements

### Requirement: Hook entrypoint captures turns locally
The package SHALL provide an `ouroboros-hook` entrypoint that reads a Claude Code hook event as JSON on stdin and dispatches on `hook_event_name`. For `UserPromptSubmit` and `Stop` it SHALL maintain the per-session interaction cache under `~/.ouroboros/interactions/`: `UserPromptSubmit` records the turn's `prompt_id` and prompt text; `Stop` completes the matching turn with the assistant's message. For every supported event (`SessionStart`, `SessionEnd`, `UserPromptSubmit`, `UserPromptExpansion`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch`, `SubagentStart`, `SubagentStop`, `PreCompact`, `PostCompact`, `InstructionsLoaded`, `PreModelSwitch`, `PostModelSwitch`, `Stop`) it SHALL additionally write one structured hook-event record to `~/.ouroboros/spool/hooks/` containing a whitelist projection of the payload. The entrypoint SHALL perform no network I/O, depend only on the standard library, and always exit 0 — including on malformed or unrecognized input — so a capture failure can never disrupt the user's session.

#### Scenario: Prompt and response pair up
- **WHEN** a UserPromptSubmit event and then a Stop event for the same session and prompt_id are piped to the entrypoint
- **THEN** the session's cache contains one complete pair with both texts and two hook-event records are spooled

#### Scenario: Malformed input exits cleanly
- **WHEN** invalid JSON or an unsupported hook_event_name is piped in
- **THEN** the entrypoint writes nothing, makes no network call, and exits 0

#### Scenario: No network ever
- **WHEN** the entrypoint processes any event
- **THEN** it performs no network I/O regardless of configuration

## ADDED Requirements

### Requirement: Metadata-first projections with content toggles
Hook-event records SHALL carry metadata by default (tool name, durations, sizes, success flags, model names, compaction trigger and token counts, instruction file paths with content hashes, expansion type and command name). Verbatim content SHALL be included only under named toggles: `OUROBOROS_CAPTURE_PROMPTS` (default `on`; prompt text, last assistant message, compaction summary, session title) and `OUROBOROS_CAPTURE_TOOL_CONTENT` (default `off`; `tool_input`, `tool_response`, command args). Raw stdin SHALL never be stored.

#### Scenario: Tool content off by default
- **WHEN** a `PostToolUse` event with a large `tool_response` is processed with default settings
- **THEN** the spooled record has `tool_name`, `duration_ms`, `success`, `output_bytes` and no `tool_response` text

#### Scenario: Prompts can be disabled while keeping structure
- **WHEN** `OUROBOROS_CAPTURE_PROMPTS=off` and a `UserPromptSubmit` is processed
- **THEN** the record has `prompt_length` but no `prompt`, and the interaction cache is not written

### Requirement: Instruction loads carry content identity
For `InstructionsLoaded` the entrypoint SHALL record `file_path` relative to `cwd`, `memory_type`, `load_reason`, `globs`, `trigger_file_path`, `parent_file_path` and a SHA-256 of the file's content when the file is readable, and SHALL never include the file's text.

#### Scenario: Hash without content
- **WHEN** an `InstructionsLoaded` event for `.claude/rules/terraform.md` is processed
- **THEN** the record contains the path and hash and no file contents
