# ouroboros-client

Fire-and-forget telemetry SDK for Claude Skills, plus the `ouroboros-hook`
entrypoint that turns Claude Code hook events into metadata-first records
(and keeps the prompt/response turn cache for interaction telemetry).

## Disclosure

This tooling records skill outcomes and, when the hooks are wired, one record
per Claude Code hook event: session lifecycle, prompt text (default on) or
prompt length, tool metadata (name, duration, success, sizes, error class;
tool content only with `OUROBOROS_CAPTURE_TOOL_CONTENT=on`), subagent and
compaction lifecycle, instruction-file paths with SHA-256 (never contents),
model switches. Text classes are **sensitive**. Hook records are spooled to
`~/.ouroboros/spool/hooks/` (cap 5,000) and uploaded in batches of 200 to
`POST /v1/telemetry/hook-events` by a separate sender process; the hook
itself never touches the network. Opt out at any time:
`export OUROBOROS_TELEMETRY=off` (disables capture, upload and every
command). Local turn capture expires after 24h. The full class table with
toggles is in `plugin/skills/telemetry-reporting/SKILL.md`.

## Configuration (environment)

| variable | meaning |
|---|---|
| `OUROBOROS_API_URL` | ingestion API base URL. Unset = the client is inert (records nothing). |
| `OUROBOROS_DEV_KEY_DIR` | directory holding `jwks.json` + `private_key.pem` (from `api/scripts/gen_jwks.py`) for dev-mode tokens. Unset = Entra via `DefaultAzureCredential` (`pip install "ouroboros-client[azure]"`). |
| `OUROBOROS_UPN`, `OUROBOROS_OID` | identity claims minted into dev-mode tokens. |
| `OUROBOROS_SESSION_ID` | session override; default `CLAUDE_CODE_SESSION_ID` (set by Claude Code for every command it runs). |
| `OUROBOROS_TELEMETRY=off` | disable capture and submission. |
| `OUROBOROS_CAPTURE_PROMPTS` | default `on`: prompt / response / compaction-summary text in hook events and the turn cache; `off` = lengths only. |
| `OUROBOROS_CAPTURE_TOOL_CONTENT` | default `off`: `tool_input` / `tool_response` text (bounded to 64 KiB) and slash-command arguments. |
| `OUROBOROS_HOME` | spool + interaction cache dir (default `~/.ouroboros`). |
| `OUROBOROS_HOOK_SPOOL_CAP` | hook-event spool cap (default 5000, oldest dropped). |

## Enabling token and cost telemetry (OTel)

```bash
python3 -m ouroboros_client.setup_otel --dry-run   # inspect
python3 -m ouroboros_client.setup_otel             # write ~/.claude/settings.json "env"
python3 -m ouroboros_client.setup_otel --remove    # undo
```

Mints a 90-day `Telemetry.Write` dev token from `OUROBOROS_DEV_KEY_DIR` (or
takes `--token`), sets `OTEL_EXPORTER_OTLP_ENDPOINT=$OUROBOROS_API_URL/v1/otel`
with `http/protobuf`, `OTEL_LOG_TOOL_DETAILS=1` and every `OTEL_LOG_*` content
flag at `0`, and adds `ouroboros.client.version` to `OTEL_RESOURCE_ATTRIBUTES`.
Idempotent; only the `env` block is touched and the file is backed up first.
`--settings <path>` targets another file, `--print` emits the block as JSON.

## Reporting an outcome

From the shell (what the Claude Code skill does):

```bash
python3 -m ouroboros_client.report --skill SQL_Generator --version 2.1.0 \
  --status user_corrected --notes "user replaced the join" \
  --corrections "use LEFT JOIN" --latency-ms 1234 [--json]
```

From Python:

```python
from ouroboros_client import TelemetryClient

TelemetryClient().record(                 # api_url / token / session from the environment
    skill_name="SQL_Generator",
    skill_version="2.1.0",
    status="user_corrected",              # success | failure | user_corrected
    feedback={"notes": "...", "user_corrections": "..."},
    latency_ms=1234,
)
# returns immediately; spools to ~/.ouroboros/spool/ when offline
```

`record()` never raises, never blocks on the network, and never prompts for
credentials. If a captured interaction exists for the session it is attached
automatically (schema v1.1). Explicit `api_url=` / `token_provider=` arguments
override the environment.

## Artifact sync

`sync_artifacts` registers the Claude Code artifacts of a plugin or repository
in the Ouroboros artifact registry (skills, agents, commands, hooks, the plugin
manifest, `CLAUDE.md` / `.claude/rules/*.md`, and `~/.claude` memory files)
with a sha256 per artifact, its frontmatter, and two token estimates: the
**always-on** cost (a skill's listing line, `name` + `description` +
`when_to_use`, capped at 1,536 characters; a hook's `hooks.json` entry; an
agent's `description`; the whole body of instructions and rules) and the
**body** cost paid on invocation. Artifact text is `internal` class (our own
source, already in Git), so bodies are uploaded verbatim; files over 256 KB,
symlinks leaving the scanned root, `.git`/`node_modules`/`.venv`, and anything
matching `*.pem`, `*.key`, `.env*` or `.dev-keys` are never read.

```bash
python3 -m ouroboros_client.sync_artifacts ./plugin --dry-run          # table, uploads nothing
python3 -m ouroboros_client.sync_artifacts ./plugin --dry-run --json   # full records minus text
python3 -m ouroboros_client.sync_artifacts ./plugin --repo acme/tools  # upload (needs OUROBOROS_API_URL + token)
```

`--repo` defaults to the `origin` remote, `--source` to `plugin` when a
`.claude-plugin/plugin.json` exists, `user` for `~/.claude`, else `project`.
Uploads go in batches of 100 to `POST /v1/telemetry/artifacts` with the same
bearer as outcome events; the server answers `202 {"accepted", "unchanged",
"versions_created"}` and opens a new `artifact_versions` row only when a hash
changed. Unlike `report`, this is an explicit command and fails loudly (exit 1)
when the API is unreachable or rejects a batch, so CI notices.

An owning repo can keep the registry current from CI (example, not wired here;
the identity is an OIDC-federated Entra app or dev keys, never a stored secret):

```yaml
# .github/workflows/ouroboros-artifacts.yml (example)
# name: ouroboros-artifacts
# on:
#   push:
#     branches: [main]
#     paths: ["plugin/**", ".claude/**", "CLAUDE.md"]
# permissions:
#   contents: read
#   id-token: write
# jobs:
#   sync:
#     runs-on: ubuntu-latest
#     steps:
#       - uses: actions/checkout@v5
#       - uses: actions/setup-python@v5
#         with: { python-version: "3.12" }
#       - run: pip install "ouroboros-client[azure] @ git+https://github.com/traviscloud-com/ouroboros-example#subdirectory=client"
#       - uses: azure/login@v2
#         with:
#           client-id: ${{ vars.OUROBOROS_CLIENT_ID }}
#           tenant-id: ${{ vars.AZURE_TENANT_ID }}
#           subscription-id: ${{ vars.AZURE_SUBSCRIPTION_ID }}
#       - run: python3 -m ouroboros_client.sync_artifacts ./plugin --repo "$GITHUB_REPOSITORY" --json
#         env:
#           OUROBOROS_API_URL: ${{ vars.OUROBOROS_API_URL }}
```

## Transcripts (opt-in)

Claude Code keeps session transcripts under `~/.claude/projects/` for 30 days
(`cleanupPeriodDays`). They are the only source of per-message token `usage`
and `parentUuid` threading, so the SDK can upload them - explicitly, never
from a hook:

```bash
python3 -m ouroboros_client.transcript upload --session <session-id>   # one session + its subagents
python3 -m ouroboros_client.transcript upload ~/.claude/projects/<project>/<session>.jsonl
python3 -m ouroboros_client.transcript backfill --days 30             # bounded by cleanupPeriodDays
python3 -m ouroboros_client.transcript consent                        # one-time, needed for --content
```

By default only **metadata** leaves the machine, one record per chain line:
`line_uuid`, `parent_uuid`, `type`/`subtype`, `prompt_id` (equal to the hook
`prompt_id` and OTel `prompt.id`), `model`, `message_id`, `timestamp`, token
`usage` (deduplicated per API message), tool-use ids and names, text lengths,
`agent_id` for subagent transcripts. `--content` adds the text of your prompts
and of the assistant's text/thinking blocks (sensitive class, 30-day
retention) and requires the consent record `~/.ouroboros/consent.json`. Tool
inputs and outputs, attachments, thinking signatures, images,
`api-request-blob` lines, `tool-results/` and `memory/` are never read or sent.
`OUROBOROS_TELEMETRY=off` disables the command. Uploads go to
`POST /v1/telemetry/transcripts` in batches of 500 with the same bearer token
as outcome events and are idempotent, so re-running is safe.

Read point: the final assistant message of a turn is written to the transcript
only after that turn's `Stop` hooks, so upload finished sessions; a `watch`
mode (read at the next `UserPromptSubmit` / `SessionEnd`) is a stub for now.

## Installing for Claude Code

The plugin's hooks run `python3`, so the package must be importable by the
`python3` on your PATH. With no pip on a system Python:

```bash
uv pip install --python "$(command -v python3)" --target "$(python3 -m site --user-site)" \
  ./client "PyJWT[crypto]"
```

## Hook wiring

The Ouroboros plugin (`plugin/hooks/hooks.json`) registers its launcher
(`plugin/scripts/ouroboros_hook_launcher.py`, which calls the same entrypoint
as `ouroboros-hook`) for all 15 supported events with `async: true` and a
10 s timeout; installing the plugin is the supported wiring. Manually, add
`ouroboros-hook` for each event to `~/.claude/settings.json`:

```json
{ "hooks": { "PostToolUse": [ { "hooks": [ { "type": "command", "command": "ouroboros-hook", "async": true, "timeout": 10 } ] } ] } }
```

`ouroboros-hook` is stdlib-only, does zero network I/O, and always exits 0.
It writes the projected record to `~/.ouroboros/spool/hooks/` and, when
`OUROBOROS_API_URL` is set, spawns `python3 -m ouroboros_client.sender` as a
detached process (at most once per 5 s) to drain both spools within a 3 s
budget. `python3 -m ouroboros_client.report` drains them too.
