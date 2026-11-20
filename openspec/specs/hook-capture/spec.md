# hook-capture

## Purpose

Local capture from Claude Code hook events: the `ouroboros-hook` entrypoint, the per-session interaction cache and the hook-event spool it writes, its no-network guarantee, expiry, and opt-out. The entrypoint transmits nothing itself; upload is the client-sdk's job.

## Requirements

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

### Requirement: Bounded, expiring, atomic cache
The interaction cache SHALL keep at most a small fixed number of recent turns per session (oldest evicted), SHALL expire session files older than a TTL (default 24 hours) during hook runs, and SHALL use atomic writes so concurrent hook and SDK access cannot corrupt entries. The entrypoint itself transmits nothing — captured text leaves the machine only through the SDK (the attach step and the hook-event upload).

#### Scenario: Ring eviction
- **WHEN** more turns than the ring size are captured for one session
- **THEN** only the most recent turns remain in the cache

#### Scenario: TTL cleanup
- **WHEN** a hook event is processed while another session's cache file is older than the TTL
- **THEN** the expired file is removed

### Requirement: Capture opt-out
The entrypoint SHALL honor `OUROBOROS_TELEMETRY=off` by capturing nothing — no cache reads or writes and no spool writes — while still exiting 0.

#### Scenario: Opt-out disables capture
- **WHEN** `OUROBOROS_TELEMETRY=off` is set and hook events are piped in
- **THEN** no cache or spool file is created or modified
