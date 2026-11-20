# Proposal: add-claude-plugin

## Why

Phase 2 shipped the SDK and hook entrypoint, but activation is manual: users must hand-edit `settings.json` and skill authors have no canonical protocol to follow. Milestone phase 3 packages the whole client experience as a Claude Code plugin — installing it wires the capture hooks automatically and delivers the SKILL.md feedback-protocol template with the telemetry disclosure — completing the disclosed, installable loop and closing the last PoC milestone (multi-turn session trace verified in Postgres).

## What Changes

- **New Claude Code plugin** (`plugin/`): `.claude-plugin/plugin.json` manifest (name `ouroboros`, disclosure in the description) so hooks, skills, and scripts install as one reviewed unit
- **Automatic hook wiring**: `plugin/hooks/hooks.json` registers `UserPromptSubmit` and `Stop` handlers invoking a stdlib-only launcher at `${CLAUDE_PLUGIN_ROOT}/scripts/` — no settings.json editing
- **Resilient launcher**: the launcher delegates to `ouroboros_client.hook` when the SDK is installed and silently no-ops (exit 0) when it isn't — a plugin install without the SDK can never break a session (FR-6 applied to hooks)
- **SKILL.md feedback-protocol template**: an installable `telemetry-reporting` skill that doubles as the copyable template — the reporting protocol (statuses, feedback fields, session_id sourcing), the required disclosure text, and opt-out instructions
- **Marketplace distribution**: a repo-root `.claude-plugin/marketplace.json` so this GitHub repo serves as the marketplace (`/plugin marketplace add traviscloud-com/ouroboros-example` → `/plugin install ouroboros@...`); managed-rollout path (`enabledPlugins`) documented for the future Entra phase
- **Milestone verification**: scripted multi-turn session trace — several captured turns plus multiple skill reports in one session land as correlated rows (shared `session_id`) verified through the reporting API

Out of scope (later): Entra ID server-side validation, publishing to any public marketplace beyond this repo, org managed-settings rollout (documented, not exercised), automated SDK installation by the plugin (README instruction instead).

## Capabilities

### New Capabilities

- `plugin-packaging`: the plugin artifact — manifest and disclosure, auto-activated hook wiring, launcher no-op guarantee, marketplace layout and install path
- `skill-template`: the feedback-protocol SKILL.md — what a telemetry-reporting skill must contain: protocol, disclosure, opt-out

### Modified Capabilities

_None — hook-capture, client-sdk, and all server capabilities are unchanged; the plugin only packages and wires what exists._

## Impact

- New top-level `plugin/` directory and repo-root `.claude-plugin/marketplace.json`; no changes to `api/`, `reporting/`, `dashboard/`, `client/` code, or `deploy/` manifests
- New verify script `deploy/verify_plugin.sh` (launcher behavior with/without SDK + multi-turn session trace)
- Privacy (PR-2): the plugin's install dialog lists its hooks; the manifest description, README, and skill template all carry the disclosure; `OUROBOROS_TELEMETRY=off` remains the universal opt-out. Hook consent is all-or-nothing at the plugin level (documented limitation from the hooks research)
- Dependency note: capture only functions where `ouroboros-client` is pip-installed; the plugin degrades to inert without it
