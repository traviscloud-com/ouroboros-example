# hook-capture (delta)

## ADDED Requirements

### Requirement: Hook entrypoint captures turns locally
The package SHALL provide an `ouroboros-hook` entrypoint that reads a Claude Code hook event as JSON on stdin and maintains a per-session interaction cache under `~/.ouroboros/interactions/`: `UserPromptSubmit` records the turn's `prompt_id` and prompt text; `Stop` completes the matching turn with the assistant's message. The entrypoint SHALL perform no network I/O, depend only on the standard library, and always exit 0 — including on malformed or unrecognized input — so a capture failure can never disrupt the user's session.

#### Scenario: Prompt and response pair up
- **WHEN** a UserPromptSubmit event and then a Stop event for the same session and prompt_id are piped to the entrypoint
- **THEN** the session's cache contains one complete pair with both texts

#### Scenario: Malformed input exits cleanly
- **WHEN** invalid JSON or an unknown hook_event_name is piped in
- **THEN** the entrypoint writes nothing, makes no network call, and exits 0

#### Scenario: No network ever
- **WHEN** the entrypoint processes any event
- **THEN** it performs no network I/O regardless of configuration

### Requirement: Bounded, expiring, atomic cache
The interaction cache SHALL keep at most a small fixed number of recent turns per session (oldest evicted), SHALL expire session files older than a TTL (default 24 hours) during hook runs, and SHALL use atomic writes so concurrent hook and SDK access cannot corrupt entries. Captured text never leaves the machine through this capability — only the SDK's attach step transmits it.

#### Scenario: Ring eviction
- **WHEN** more turns than the ring size are captured for one session
- **THEN** only the most recent turns remain in the cache

#### Scenario: TTL cleanup
- **WHEN** a hook event is processed while another session's cache file is older than the TTL
- **THEN** the expired file is removed

### Requirement: Capture opt-out
The entrypoint SHALL honor `OUROBOROS_TELEMETRY=off` by capturing nothing — no cache reads or writes — while still exiting 0.

#### Scenario: Opt-out disables capture
- **WHEN** `OUROBOROS_TELEMETRY=off` is set and hook events are piped in
- **THEN** no cache file is created or modified
