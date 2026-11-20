# Ouroboros plugin for Claude Code

Installs the Ouroboros skill-telemetry client experience as one unit:

- **Capture hooks** on every session event (`SessionStart`, `SessionEnd`,
  `UserPromptSubmit`, `UserPromptExpansion`, `PostToolUse`,
  `PostToolUseFailure`, `PostToolBatch`, `SubagentStart`, `SubagentStop`,
  `PreCompact`, `PostCompact`, `InstructionsLoaded`, `PreModelSwitch`,
  `PostModelSwitch`, `Stop`) that write a metadata-first record to a
  **local** spool (`~/.ouroboros/spool/hooks/`) and keep the prompt/response
  turn cache (`~/.ouroboros/interactions/`, 24h expiry). The hooks are
  async, stdlib-only and do no network I/O; a separate sender process
  uploads the spool in batches.
- **`telemetry-reporting` skill** — the feedback-protocol instructions Claude
  follows to report skill outcomes through the SDK, which attaches the
  captured turn to the outcome event. Also the copyable template for
  instrumenting other skills.

## Disclosure

With this plugin installed and the `ouroboros-client` SDK present, the
following reaches your team's Ouroboros ingestion API, stamped with your
verified identity (full table with retention classes in
`skills/telemetry-reporting/SKILL.md`):

| class | default | disable with |
|---|---|---|
| skill outcomes, feedback, verbatim corrections (sensitive) | on when a skill reports | `OUROBOROS_TELEMETRY=off` |
| prompt / response / compaction-summary text (sensitive) | on | `OUROBOROS_CAPTURE_PROMPTS=off` |
| session lifecycle, tool metadata (names, durations, sizes, error class), subagents, compaction, model switches, instruction-file paths + SHA-256 | on | `OUROBOROS_TELEMETRY=off` |
| tool input/output text, slash-command args (sensitive) | off | `OUROBOROS_CAPTURE_TOOL_CONTENT=on` to enable |
| token and cost metrics via Claude Code OTel | off until `setup_otel` | `python3 -m ouroboros_client.setup_otel --remove` |

**Opt out of everything at any time**: `export OUROBOROS_TELEMETRY=off`.
Disabling the plugin also removes its hooks.

## Setup commands

| command | sends | undo |
|---|---|---|
| `python3 -m ouroboros_client.setup_otel` | enables Claude Code's built-in OpenTelemetry export (tokens, USD cost, model, skill/plugin/hook names) to `$OUROBOROS_API_URL/v1/otel` with a 90-day dev token; writes only the `env` block of `~/.claude/settings.json` (user scope), backs the file up first; `--dry-run` / `--print` to inspect. Content flags `OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_ASSISTANT_RESPONSES`, `OTEL_LOG_TOOL_CONTENT`, `OTEL_LOG_RAW_API_BODIES` are written as `0`, `OTEL_LOG_TOOL_DETAILS=1`. | `python3 -m ouroboros_client.setup_otel --remove`, or delete `CLAUDE_CODE_ENABLE_TELEMETRY`, `OTEL_METRICS_EXPORTER`, `OTEL_LOGS_EXPORTER`, `OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS` and the `OTEL_LOG_*` keys from `env` |
| `python3 -m ouroboros_client.sync_artifacts` | skill/agent/hook/CLAUDE.md inventory with hashes and token estimates | stop running it |
| `python3 -m ouroboros_client.transcript` (opt-in) | transcript metadata; message text only with `--content` and a consent file | stop running it; delete `~/.ouroboros/consent.json` |

## Install

```
/plugin marketplace add traviscloud-com/ouroboros-example
/plugin install ouroboros@ouroboros
pip install "git+https://github.com/traviscloud-com/ouroboros-example.git#subdirectory=client"   # the hooks are inert until the SDK is installed
```

The install dialog lists everything the plugin adds (15 hooks, 1 skill).
Without `ouroboros-client` on your Python, the hook launcher exits silently —
the plugin can never break a session.

## Organization rollout (future)

Once the Entra ID phase lands, orgs can force-enable via managed settings:

```json
{
  "extraKnownMarketplaces": { "ouroboros": { "source": { "source": "github", "repo": "traviscloud-com/ouroboros-example" } } },
  "enabledPlugins": ["ouroboros@ouroboros"]
}
```

Note: plugin hooks are all-or-nothing — users can disable the whole plugin
(unless managed) but not individual hooks; `OUROBOROS_TELEMETRY=off` is the
granular control and always works.
