# Proposal: add-client-sdk-and-hook-capture

## Why

Nothing on a user's machine produces telemetry today — the only producers are test seeds and curl. Milestone phase 2 delivers the client half of the loop: a Python SDK skills call to report outcomes, and (per the hook/plugin decision) a Claude Code hook entrypoint that captures user prompts and assistant responses locally so the SDK can attach them to outcome events. This makes real `interaction` data flow into the sentiment pipeline built in the dashboard change, with zero server-side changes.

## What Changes

- **New Python SDK** (`client/`, package `ouroboros-client`): fire-and-forget event reporting — spool-first durability (`~/.ouroboros/spool/`), background sender with ≤3s timeout accepting any 2xx, batch submission of v1.0/v1.1 events, never raises into the caller, never interactive
- **Pluggable auth**: constructor takes `api_url` + `scope` + optional `token_provider` callable; default provider wraps `DefaultAzureCredential` (cached `az login`), dev provider mints local JWKS tokens for the PoC; no credential → silent no-op + spool
- **Hook capture entrypoint** (`ouroboros-hook` console script): consumes Claude Code `UserPromptSubmit` and `Stop` hook JSON on stdin and writes prompt/response pairs to a local per-session interaction cache (`~/.ouroboros/interactions/`) — local-only, no network in the hook path, fast exit
- **Interaction handoff**: when a skill reports an outcome, the SDK attaches the session's captured interaction to that event (schema v1.1) — outcome and interaction stay on the same event, exactly as the sentiment blend assumes; the server contract is untouched
- **Opt-out and disclosure**: `OUROBOROS_TELEMETRY=off` disables both capture and submission; cache entries expire locally (TTL cleanup)
- **End-to-end verification**: simulated hook events + an SDK outcome report land a v1.1 row (with stamped identity and interaction) in the deployed dev-mode stack

Out of scope (later changes): Entra ID app registration and real server-side JWT validation (`AUTH_MODE=entra` stays reserved; the SDK's default Azure provider is ready for it), plugin packaging of hooks + SKILL.md template (phase 3), standalone hook-emitted events (rejected — would require schema v1.2 and sentiment rework).

## Capabilities

### New Capabilities

- `client-sdk`: the reporting client — fire-and-forget semantics, spool-first durability and retry, batching, pluggable token providers, opt-out, and interaction attachment at report time
- `hook-capture`: local capture of conversation turns from Claude Code hook events — cache format and pairing (`session_id`/`prompt_id`), local-only guarantee, TTL expiry, opt-out

### Modified Capabilities

_None — the ingestion API, storage, reporting, sentiment, and dashboard contracts are unchanged; this change only adds producers._

## Impact

- New top-level dir: `client/` (package `ouroboros-client` with `ouroboros-hook` console script + tests)
- New dependencies: `httpx` (SDK transport); `azure-identity` as an optional extra (`[azure]`) so dev-mode use stays dependency-light; test-only local HTTP server
- No changes to `api/`, `reporting/`, `dashboard/`, or `deploy/` manifests; `deploy/verify_dashboard.sh` gains a sibling verify script for the SDK path
- Privacy (PR-1/PR-2): captured prompts/responses are sensitive verbatim content; capture is local-only until a skill reports, is disclosed (README + future plugin manifest), honors `OUROBOROS_TELEMETRY=off`, and cache entries expire on TTL. Hook scripts must silently no-op when Python/SDK are absent (FR-6 applied to hooks)
