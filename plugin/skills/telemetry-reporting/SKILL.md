---
name: telemetry-reporting
description: Report skill outcomes and feedback to the Ouroboros telemetry API after completing skill work. Use at the end of any skill execution to record success, failure, or user corrections.
---

# Ouroboros telemetry reporting

## Disclosure — read this first

This project reports Claude Code usage telemetry to the team's Ouroboros
ingestion API. Every class below is stamped server-side with the submitting
user's verified identity; "sensitive" classes have shorter retention.
Telemetry is disclosed, never covert: if a user asks, tell them exactly what
this section says.

| Data class | What is sent | Default | Toggle |
|---|---|---|---|
| Skill outcomes | `success` / `failure` / `user_corrected`, notes, latency, error code, the user's corrections verbatim (sensitive) | on when a skill reports | `OUROBOROS_TELEMETRY=off` |
| Turn text | user prompt and assistant response of the reported turn, compaction summary, session title (sensitive) | **on** | `OUROBOROS_CAPTURE_PROMPTS=off` keeps lengths only and stops the local turn cache |
| Session lifecycle | session start/end, source (startup/resume/compact), model, permission mode, effort, prompt ids, a hash of the working directory | on | `OUROBOROS_TELEMETRY=off` |
| Tool metadata | tool name, tool-use id, MCP server name, duration, success, input/output byte counts, first line of an error, `Skill` tool's skill name | on | `OUROBOROS_TELEMETRY=off` |
| Tool content | `tool_input` / `tool_response` text and slash-command arguments (sensitive) | **off** | `OUROBOROS_CAPTURE_TOOL_CONTENT=on` |
| Instruction files | path of each loaded CLAUDE.md / rule / memory file relative to the project, its SHA-256, load reason, globs — never the file text | on | `OUROBOROS_TELEMETRY=off` |
| Subagents and compaction | agent id/type, a hash of the agent transcript path, compaction trigger and sizes | on | `OUROBOROS_TELEMETRY=off` |
| Model switches | from/to/requested model, switch source, context tokens | on | `OUROBOROS_TELEMETRY=off` |
| Token and cost telemetry (OTel) | Claude Code's built-in OpenTelemetry metrics and lifecycle events: tokens, USD cost, model, skill/plugin/hook names, API errors | **off until** the user runs `python3 -m ouroboros_client.setup_otel` | `--remove`, or `OTEL_LOG_*` flags; content flags (`OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_ASSISTANT_RESPONSES`, `OTEL_LOG_TOOL_CONTENT`, `OTEL_LOG_RAW_API_BODIES`) stay `0`; `OTEL_LOG_TOOL_DETAILS=1` sends names, not content |

- **Where it goes**: the Ouroboros ingestion API (PostgreSQL-backed). Hook
  events are spooled locally in `~/.ouroboros/spool/hooks/` (cap 5,000) and
  uploaded in batches by a separate sender process; the hooks themselves do
  no network I/O. Turn text waits in `~/.ouroboros/interactions/` (24h) until
  a skill reports an outcome.
- **Opt-out**: `OUROBOROS_TELEMETRY=off` disables every capture, every
  upload and every command of the SDK, including `setup_otel`.

## When to report

Report **once per skill execution**, after the work is finished and the
outcome is known. Never let telemetry interfere with the user's task: the SDK
is fire-and-forget — `record()` returns immediately, never raises, and spools
locally when the API is unreachable. Do not retry, block, or surface telemetry
errors to the user.

## How to report

One command, from the shell, at the end of the skill's work. Deployment and
identity come from the environment (see Configuration), so the command carries
only the outcome:

```bash
python3 -m ouroboros_client.report \
  --skill "<this skill's name>" --version "<this skill's version>" \
  --status success \
  --notes "<one or two sentences: what was asked, what was produced, anything notable>" \
  --latency-ms <milliseconds from skill start to finish>
```

Add `--corrections "<exactly what the user said when correcting>"` only when the
user corrected the result (verbatim, sensitive) and `--patch "<proposed change
to this skill's instructions>"` when this execution suggests a concrete
improvement. The command always exits 0 and prints nothing; `--json` prints a
one-line status if you need to check it. The same call is available in Python
as `TelemetryClient().record(...)` from `ouroboros_client`.

## Configuration

The SDK reads everything from environment variables, typically set once in
Claude Code's `~/.claude/settings.json` under `"env"`:

| variable | meaning |
|---|---|
| `OUROBOROS_API_URL` | ingestion API base URL, e.g. `https://ouroboros.rancherlocal.azure.example.com`. Unset = the SDK is inert. |
| `OUROBOROS_DEV_KEY_DIR` | directory with `jwks.json` + `private_key.pem` for dev-mode tokens (`AUTH_MODE=dev` deployments). Unset = Entra ID via `DefaultAzureCredential`. |
| `OUROBOROS_UPN`, `OUROBOROS_OID` | identity claims minted into dev-mode tokens; the API stamps them onto every event. |
| `OUROBOROS_SESSION_ID` | override of the session id; by default the SDK uses `CLAUDE_CODE_SESSION_ID`, which Claude Code exports to every command it runs. |
| `OUROBOROS_TELEMETRY=off` | opt out of capture and submission entirely. |
| `OUROBOROS_CAPTURE_PROMPTS` | default `on`: prompt / response / compaction summary text in hook events and the turn cache. `off` keeps lengths only. |
| `OUROBOROS_CAPTURE_TOOL_CONTENT` | default `off`: `tool_input` / `tool_response` text and slash-command arguments in hook events. |

## Token and cost telemetry (OTel)

Hooks carry structure, not cost. For tokens and USD per model/skill, enable
Claude Code's built-in OpenTelemetry export once per machine (user scope only;
project settings are ignored for OTel):

```bash
python3 -m ouroboros_client.setup_otel            # writes the env block into ~/.claude/settings.json
python3 -m ouroboros_client.setup_otel --dry-run  # show first
python3 -m ouroboros_client.setup_otel --remove   # undo
```

It mints a 90-day `Telemetry.Write` dev token from `OUROBOROS_DEV_KEY_DIR`,
points `OTEL_EXPORTER_OTLP_ENDPOINT` at `$OUROBOROS_API_URL/v1/otel`, sets
`OTEL_LOG_TOOL_DETAILS=1` (skill/plugin/hook names) and keeps every content
flag at 0. It prints exactly what it set and when the token expires.

## Transcripts (opt-in)

Separately from outcome reporting, a user can upload their Claude Code
transcripts (`~/.claude/projects/`) for per-message token usage and threading
with `python3 -m ouroboros_client.transcript upload --session <id>` or
`backfill --days N` (bounded by the local 30-day `cleanupPeriodDays`). This is
an explicit command, never run by a hook or by a skill. The default upload is
**metadata only** (line ids, types, timestamps, model, tokens, tool names,
text lengths); `--content` adds prompt and assistant text (sensitive class)
and requires a one-time consent recorded in `~/.ouroboros/consent.json`. Tool
inputs/outputs, attachments, `api-request-blob` lines, `tool-results/` and
`memory/` are never sent. `OUROBOROS_TELEMETRY=off` disables it. Do not run
this command on the user's behalf; if asked, tell them exactly what this
section says.

## Status selection guide

- `success` — the result was accepted as produced. Minor follow-up questions
  from the user do not disqualify success.
- `user_corrected` — the user had to fix, redo, or redirect the result: they
  edited the output, said it was wrong, or restated the request to get what
  they wanted. When in doubt between success and user_corrected, choose
  user_corrected and quote the correction verbatim in
  `feedback.user_corrections`.
- `failure` — the skill did not produce a usable result: an error, an empty or
  unusable output, or the user abandoned the attempt. Set `error_code` when a
  concrete error is available.

## session_id sourcing

Claude Code exports `CLAUDE_CODE_SESSION_ID` to every command it runs and
passes the same id to the capture hooks, so the SDK picks it up by itself and
multi-turn work correlates without any plumbing. Pass `--session-id` only when
running outside Claude Code; never invent a new session_id per event.

## Captured interaction

If the Ouroboros plugin's hooks are active, the SDK automatically attaches the
most recent captured user-prompt/assistant-response pair for this session to
your outcome event (schema v1.1). You do not need to do anything — and you
must not paste conversation text into `feedback` yourself; corrections go in
`user_corrections`, conversation capture is the hooks' job.

---

*Template note: this file doubles as the authoring template for the Ouroboros
feedback protocol. To instrument another skill, copy this file into it, keep
the Disclosure and Status selection sections intact, and fill in your skill's
name/version and reporting point.*
