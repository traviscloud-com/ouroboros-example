# Design: add-claude-plugin

## Context

Everything the plugin wires already exists and is tested: `ouroboros-hook` (stdlib-only capture), the SDK attach path, and the server stack on k3s. The hooks/plugin research established the facts this design builds on: plugins bundle skills + hooks and activate hooks on install with the component inventory shown in the install dialog; `${CLAUDE_PLUGIN_ROOT}` resolves inside hook commands; a GitHub repo can serve as a marketplace via `.claude-plugin/marketplace.json`; org rollout is possible later via managed `enabledPlugins`. Phase 3's PLAN milestone also requires demonstrating the multi-turn session trace in Postgres.

## Goals / Non-Goals

**Goals:**
- One installable unit: manifest + hooks + launcher + skill template, with disclosure visible at install time
- Hook wiring that activates on install and can never damage a session (fast, silent, exit-0 under every failure mode, SDK absent included)
- The SKILL.md feedback-protocol template as both an installed skill and a copyable authoring template
- Repo-as-marketplace install path; local-path install used for verification
- Scripted multi-turn session-trace verification through the live stack

**Non-Goals:**
- Bundling or auto-installing the Python SDK (plugins cannot ship a Python runtime; README covers `pip install`)
- Entra ID, managed-settings rollout exercises, public marketplace listing
- Per-hook user toggles (platform limitation — plugin-level enable/disable only; opt-out env var is our granular control)

## Decisions

### D1: Launcher delegates, never vendors
`plugin/scripts/ouroboros_hook_launcher.py` (stdlib-only) tries `from ouroboros_client.hook import main` and falls back to `sys.exit(0)` on any ImportError/exception. Alternative — vendoring the capture code into the plugin — rejected: two implementations of cache/pairing logic would drift; the launcher keeps `ouroboros-client` the single source of truth, at the cost of capture being inert until the SDK is installed (acceptable and documented).

### D2: hooks.json invokes python3 via ${CLAUDE_PLUGIN_ROOT}
Command shape: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ouroboros_hook_launcher.py"` for both `UserPromptSubmit` and `Stop`, marked async where the schema supports it. `python3` from PATH rather than a fixed path — matches the SDK's own install assumption. The exact hooks.json schema is confirmed against current docs at implementation time (a task), since plugin hook config format may differ in details from settings.json hooks.

### D3: The template is an installed skill
`plugin/skills/telemetry-reporting/SKILL.md` instructs Claude how to report skill outcomes through `ouroboros-client` (statuses, feedback fields, session_id sourcing from hook payloads, latency, when to mark `user_corrected`), and leads with the disclosure + opt-out text. Installed, it teaches Claude the protocol in any project with the plugin; as a file, it is the copyable template PLAN phase 3 promised. One artifact, both jobs.

### D4: Repo doubles as the marketplace
Root `.claude-plugin/marketplace.json` lists the `ouroboros` plugin with source `./plugin`. Users: `/plugin marketplace add traviscloud-com/ouroboros-example` then `/plugin install ouroboros@ouroboros`. Verification uses the local-path form of `marketplace add` against the working tree. Alternative — separate marketplace repo — needless for one plugin.

### D5: Verification = structure + launcher + trace
Three layers, scripted in `deploy/verify_plugin.sh`:
1. **Structure**: manifest and hooks.json parse; launcher runs under both hook events exactly as hooks.json invokes it (env `CLAUDE_PLUGIN_ROOT` set manually).
2. **Degradation**: launcher with a bare `python3` (no SDK importable) exits 0 and writes nothing.
3. **Milestone trace**: three simulated turns + two SDK outcome reports in one `session_id` → reporting API shows the correlated multi-turn trace (row count, shared session, interaction on the right events). CLI-driven `/plugin` install is attempted when the `claude` CLI supports it non-interactively; otherwise structural verification stands and manual install steps are documented in the README.

## Risks / Trade-offs

- [hooks.json schema drift between docs and shipped CLI] → Confirmed at implementation; the launcher contract (stdin JSON in, exit 0) is stable regardless.
- [Capture inert when SDK missing] → By design (D1); README + skill template state the pip install prerequisite plainly.
- [Install-dialog-only consent] → Disclosure duplicated in manifest description, README, and the skill template; opt-out env var always works (platform limitation documented in proposal).
- [python3 not on PATH on some machines] → Launcher never runs → hooks exit non-zero? Hook command failure is surfaced by Claude Code as a non-blocking error; command uses `python3 ... || true` shell form if needed — settled at implementation with the schema check.

## Migration Plan

1. Pure addition (`plugin/`, root `.claude-plugin/`, verify script); nothing server-side.
2. Users: add marketplace, install plugin, `pip install ouroboros-client` — capture live.
3. Rollback = `/plugin uninstall`; local caches expire on TTL.

## Open Questions

- None blocking. Whether `claude` CLI can drive plugin install non-interactively is resolved during verification (D5 fallback covers both outcomes).
