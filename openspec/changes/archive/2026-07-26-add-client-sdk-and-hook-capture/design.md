# Design: add-client-sdk-and-hook-capture

## Context

The server stack (ingestion, storage, reporting, sentiment, dashboard) is live on k3s and producer-agnostic. Schema v1.1 already carries `interaction`. What's missing is production: skills have no SDK, and nothing captures conversation text. The hook discussion settled the architecture: Claude Code hooks (`UserPromptSubmit` gives `prompt_text`; `Stop` gives `last_assistant_message`; both carry a stable `session_id` and per-turn `prompt_id`) capture turns **locally**, and the SDK attaches the captured interaction to the skill's outcome event — because the event schema requires `status`/`skill_name` and the sentiment blend assumes text and outcome live on the same event. Standalone hook events were explicitly rejected to avoid a v1.2 schema and sentiment rework.

## Goals / Non-Goals

**Goals:**
- `ouroboros-client` SDK: `TelemetryClient(api_url, scope, token_provider=None).record(...)` that returns immediately, never raises, never prompts
- Spool-first durability: events are on disk before any network I/O; background sender drains spool + queue; ≤3s timeout; any 2xx = sent
- Token providers: default `DefaultAzureCredential` (lazy import via `[azure]` extra), dev provider using the local JWKS key dir; provider returning `None` → spool and stay silent
- `ouroboros-hook`: stdin-JSON entrypoint for `UserPromptSubmit` and `Stop`, writing a per-session interaction cache; milliseconds-fast, zero network
- Interaction attach at `record()` time (v1.1) with consumed-entry cleanup and TTL expiry
- `OUROBOROS_TELEMETRY=off` kills capture and submission everywhere
- E2E verification against the deployed dev-mode stack

**Non-Goals:**
- Entra app registration / server-side `AUTH_MODE=entra` (separate change; SDK is ready for it)
- Plugin packaging (`.claude-plugin/`, `hooks/hooks.json`, SKILL.md template) — phase 3
- Guaranteed delivery: fire-and-forget accepts loss on e.g. spool-dir loss; telemetry is not billing data

## Decisions

### D1: Spool-first, not queue-first
`record()` validates the event, writes it as a JSON file in `~/.ouroboros/spool/`, then nudges a daemon sender thread and returns. The sender batches spooled files into one POST and deletes them on 2xx. Crash between write and send = file survives and is retried on the next `record()` (or next process). Alternative — in-memory queue with spool-on-failure — rejected: loses events on process exit and duplicates the durability logic.

### D2: Duplicate sends are safe by design
A crash after POST but before file deletion re-sends the same `event_id` — the server's `ON CONFLICT DO NOTHING` makes this a no-op. Freshness matters instead: spooled events older than the server's 300s window would reject the whole batch, so the sender submits stale spool files in a separate batch and drops them on 400 (logged locally, never raised).

### D3: Interaction cache layout and pairing
`~/.ouroboros/interactions/<session_id>.json` holds a small ring (last 5 turns) of `{prompt_id, user_prompt, assistant_response, ts}`. `UserPromptSubmit` inserts `{prompt_id, user_prompt}`; `Stop` completes the matching `prompt_id` with `assistant_response` (falling back to the newest incomplete entry). `record(..., session_id=...)` attaches the newest **complete** pair, marks it consumed, and sets `schema_version: "1.1"`; no pair → plain v1.0 event. Hook runs also delete session files older than 24h (TTL). Writes are atomic (tmp file + rename) since hooks and skills can race.

### D4: Hook path does zero network and zero hard dependencies
`ouroboros-hook` imports only stdlib, reads stdin, writes the cache, exits 0 — always exit 0, even on malformed input (a broken hook must never block the user's Claude session; `UserPromptSubmit` has a 30s budget but the target is <50ms). The hooks configuration (later shipped by the plugin) invokes it with `async` semantics where available.

### D5: Token providers as plain callables
`token_provider: Callable[[], str | None]`. Built-ins: `azure_token_provider(scope)` wrapping `DefaultAzureCredential.get_token` (import inside the function; `azure-identity` lives in the `[azure]` extra), and `dev_token_provider(key_dir, ...)` reusing the api-side JWKS format for PoC/e2e. `None` result → events stay spooled; no exception ever escapes. This keeps dev-mode e2e testable today and Entra-ready later with zero SDK changes.

### D6: Standalone package, models duplicated deliberately
`client/` shares no code with `api/` — the SDK builds plain dicts matching the wire contract and does light local validation (required fields, status enum). Importing the server's Pydantic models would couple the SDK to server dependencies; the wire contract is the specs' concern. `httpx` is the only hard runtime dependency.

### D7: Testing without the cluster
Unit/integration tests run against a threaded local HTTP server capturing requests (no k3s, no docker): fire-and-forget timing, spool-on-failure and drain, opt-out, no-credential silence, hook pairing, attach-and-consume, TTL. A separate `deploy/verify_sdk.sh` runs the real path: simulate both hook events, `record()` an outcome with the dev token provider, then assert the v1.1 row (interaction + stamped identity) via the reporting API.

## Risks / Trade-offs

- [Always-on local capture of every turn] → Data leaves the machine only when a skill reports; still disclosed (README now, plugin manifest in phase 3); `OUROBOROS_TELEMETRY=off` stops capture itself; 24h TTL bounds the cache.
- [Wrong pairing: outcome attaches an unrelated turn] → Pairing is per-`session_id` and consumes newest-complete; a skill reporting long after the turn may attach a later prompt. Acceptable for sentiment rollups; `prompt_id` is kept in the cache should stricter pairing be needed.
- [Spool growth when API is unreachable] → Cap spool at N files (drop oldest, count logged locally); telemetry loss is acceptable by FR-6.
- [Daemon thread dies with the process before sending] → Spool-first means nothing is lost; it sends on the next opportunity.
- [Hook adds latency to every turn] → Stdlib-only, single small file write, always exit 0; measured target <50ms.

## Migration Plan

1. Pure addition — ship `client/`; no server deploys needed.
2. Manual hook wiring for now (documented settings.json snippet); the phase-3 plugin makes it automatic.
3. Rollback = remove hook config; spool/cache dirs are self-expiring.

## Open Questions

- None blocking. Exact spool cap (default 500 files) and cache ring size (5) are constants tunable via env.
