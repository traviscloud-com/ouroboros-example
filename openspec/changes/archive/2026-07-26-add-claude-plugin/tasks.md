# Tasks: add-claude-plugin

## 1. Plugin scaffold

- [x] 1.1 Confirm current plugin hooks.json schema and marketplace.json schema against the Claude Code docs (guide agent / docs); note exact field names for tasks 1.2–2.1
- [x] 1.2 Create `plugin/.claude-plugin/plugin.json` (name `ouroboros`, version, author, homepage, disclosure-bearing description) and `plugin/README.md` (install steps, `pip install ouroboros-client` prerequisite, disclosure, opt-out, managed-rollout note)

## 2. Hook wiring and launcher

- [x] 2.1 Create `plugin/hooks/hooks.json` registering UserPromptSubmit and Stop → `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ouroboros_hook_launcher.py"` (async per schema support)
- [x] 2.2 Implement `plugin/scripts/ouroboros_hook_launcher.py` (stdlib-only): delegate to `ouroboros_client.hook.main()`, exit 0 silently on ImportError or any exception
- [x] 2.3 Launcher tests (subprocess): parity with `ouroboros-hook` when SDK importable; inert exit-0 with SDK stripped from sys.path; malformed input exit 0

## 3. Skill template

- [x] 3.1 Write `plugin/skills/telemetry-reporting/SKILL.md`: disclosure + opt-out first, then the reporting protocol (client construction, status selection guidance, feedback fields incl. verbatim user_corrections, session_id sourcing, latency, fire-and-forget guarantee), structured to be copyable as the authoring template

## 4. Marketplace

- [x] 4.1 Create repo-root `.claude-plugin/marketplace.json` listing `ouroboros` with source `./plugin`; document `/plugin marketplace add traviscloud-com/ouroboros-example` + `/plugin install` in plugin README and top-level README pointer

## 5. Verification

- [x] 5.1 `deploy/verify_plugin.sh` part 1 — structure + launcher: parse manifests, run the launcher exactly as hooks.json invokes it (CLAUDE_PLUGIN_ROOT set) for both events, assert cache parity, assert inert no-SDK behavior
- [x] 5.2 Attempt local CLI install (`claude` non-interactive plugin/marketplace commands) against the working tree; if not scriptable, document manual install verification steps in plugin README and record the outcome
- [x] 5.3 `deploy/verify_plugin.sh` part 2 — milestone trace: simulate 3 turns + 2 SDK outcome reports in one session against the live stack; assert via the reporting API that all rows share the session_id with correct statuses and interaction pairings (multi-turn session trace = PLAN phase 3 exit criterion)
