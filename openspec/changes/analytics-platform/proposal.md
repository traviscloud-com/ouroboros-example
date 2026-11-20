# Proposal: analytics-platform

## Why

Ouroboros exists to **collect Claude Code usage data, prompts and context so that an autonomous feedback loop can incrementally improve our agents, skills, context (CLAUDE.md/rules) and hooks — while minimizing context bloat**. Today it collects only what a skill chooses to self-report: one denormalized `events` row per skill outcome, with the last prompt/response pair attached. That leaves the loop blind to the things it is supposed to improve:

- **No usage or cost data** — no tokens, per-turn model, USD cost, tool calls or compaction counts, so "is this skill worth its always-on tokens?" cannot be answered.
- **Hooks capture only turn text** — `transcript_path`, `cwd`, tool, subagent, compaction, session and instruction-load events are ignored, and hooks/agents/CLAUDE.md produce no rows at all. There is no entity model for the artifacts we want to improve.
- **Nothing records the loop itself** — no improvement proposals, applied changes, or before/after outcomes, so the loop cannot measure whether it helped.
- **The reporting surface is unauthenticated** inside the Gateway and shows verbatim prompts to anyone who logs in; there are no roles or redaction.
- **The dashboard cannot serve leadership** — three recharts views, no routing, date range, period comparison, adoption, latency percentiles or cost, and no path to a data export engineers can model on.

This change turns Ouroboros from a skill-outcome logger into an analytics platform with two audiences — technical leadership evaluating adoption, outcomes, cost and trends; engineers planning improvements and modeling on raw structured data — and a first, approval-gated version of the improvement loop.

Privacy posture is unchanged and restated here: prompts, corrections and any captured text are **sensitive**; telemetry is **disclosed, never covert**; `OUROBOROS_TELEMETRY=off` disables every client-side path, and all new content-bearing collection (tool input/output, transcripts, OTel prompt/response logging) is **off by default and opt-in**.

## What Changes

- **Two complementary collection pipelines.** (1) The plugin's hooks expand from `UserPromptSubmit`+`Stop` to `SessionStart/End`, `UserPromptExpansion`, `PostToolUse`/`PostToolUseFailure` (metadata only by default), `SubagentStart/Stop`, `PreCompact/PostCompact`, `InstructionsLoaded`, `PreModelSwitch/PostModelSwitch` and `Stop`, spooled locally and uploaded in batches as structured hook events. (2) Claude Code's built-in OpenTelemetry export is pointed at a new OTLP/HTTP receiver in the API (`/v1/otel/v1/metrics`, `/v1/otel/v1/logs`) for tokens, cost, model, `skill_activated`, `compaction`, `subagent_completed` and `api_request` facts. Both join on `session_id` and `prompt.id`.
- **Optional transcript ingestion** — explicit per-user opt-in upload of transcript JSONL usage/metadata lines (not content by default), gated behind a schema spike because the per-line format is undocumented.
- **Normalized data model** — `sessions`, `turns`, `tool_calls`, `subagent_runs`, `compactions`, `instructions_loaded`, `skill_activations`, `usage_facts`, `artifacts` + `artifact_versions` (skills, hooks, agents, CLAUDE.md/rules with content hash and estimated always-on tokens), `improvement_proposals`, `improvement_outcomes`, `principals`. `events` stays the skill-outcome fact table. Versioned, idempotent SQL migrations applied by the API on start; GIN/FTS indexes; retention per sensitivity class.
- **Reporting auth** — the reporting service trusts `X-Auth-Request-Email` only from the Gateway (pod-CIDR check), maps it to a role (`leadership` | `engineer` | `admin`), redacts verbatim text for `leadership`, and exposes a modeling export (NDJSON/Parquet) to `engineer`+.
- **Analytics aggregations** — scheduled materialized views for adoption (DAU/WAU per skill/agent), outcome rates, p50/p95 latency, tokens and cost per session/skill/model, a context-bloat index, error_code breakdowns and correction themes (VADER + keyword clustering now; LLM scoring later, optional).
- **shadcn/ui dashboard** — shadcn/ui components, chart components (Recharts) and dashboard blocks installed into the existing Vite SPA (Tailwind v4, react-router, date-range + period-comparison filterbar, dark mode), with the Tremor Dashboard template as a layout reference only and individual Tremor Raw files borrowed where shadcn/ui has no equivalent; with a Leadership overview and an Engineering workspace (per-artifact detail, failure analysis, prompt/context inspector, session timeline, export).
- **Improvement loop v1** — a scheduled analyst job (Claude via the API) reads aggregated evidence + artifact text and writes `improvement_proposals` with concrete diffs; a human approves in the UI, which opens a PR against the owning repo; outcomes are measured as before/after metric windows. Nothing is applied automatically in v1.
- **Platform** — stays two images + CloudNativePG; manifests gain OTel-receiver routing, longer ingestion timeouts, storage growth headroom, a scheduler (in-process in reporting, with CronJob as fallback) and the analyst job's secrets via the existing secret path. Backups become a hard dependency once the loop writes state.

Out of scope (later changes): `AUTH_MODE=entra`; autonomous apply for low-risk proposal classes; LLM-based theme scoring; embeddings search; multi-tenant org support.

## Capabilities

### New Capabilities

- `usage-telemetry`: OTLP/HTTP receiver for Claude Code's OpenTelemetry metrics and logs — accepted payloads, mapping to `usage_facts`/`skill_activations`/`compactions`, auth, required client env and privacy toggles
- `session-model`: structured hook-event ingestion and the normalized session/turn/tool/subagent/compaction/instruction tables that join hook data to OTel data
- `transcript-ingestion`: explicit opt-in upload of transcript JSONL excerpts (usage/metadata lines by default), the spike that precedes it, and its retention
- `artifact-registry`: versioned registry of the things the loop improves — skills, hooks, agents, CLAUDE.md/rules — with content hashes and always-on token estimates
- `analytics-aggregations`: scheduled materialized aggregates (adoption, outcomes, latency percentiles, cost/tokens, context-bloat index, correction themes) and the modeling export
- `reporting-auth`: Gateway-trusted identity, roles, redaction for leadership, and admin-managed principals
- `analytics-ui`: the shadcn/ui-based Leadership and Engineering experiences — pages, filterbar, personas, export
- `improvement-loop`: analyst job, proposals, approval → PR, outcome measurement

### Modified Capabilities

- `hook-capture`: captures the expanded hook set as structured events (metadata-first), keeps the local-only turn cache, adds spooled upload of hook events
- `client-sdk`: hook-event batching to a new ingestion endpoint, `transcript` opt-in command, no new hard deps on the hook path
- `plugin-packaging`: hooks.json covers the new events; plugin version bump and disclosure text updated; `python3 -m ouroboros_client.setup_otel` helper
- `telemetry-ingestion`: new `POST /v1/telemetry/hook-events`, `POST /v1/telemetry/transcripts` and `POST /v1/telemetry/artifacts` endpoints; freshness window relaxed for spooled batches with `client_ts` preserved
- `event-storage`: versioned migrations replace single-file schema application; new tables; retention classes; GIN/FTS indexes
- `reporting-api`: authenticated, role-aware, redacting; new aggregate and export endpoints; `/v1/reports/*` filters on `client_ts`
- `dashboard-ui`: becomes a routed Tailwind v4 + shadcn/ui SPA shell; existing three views are folded into the Engineering workspace
- `sentiment-analysis`: adds keyword clustering of correction themes and persists daily rollups instead of recomputing in Python per request
- `deployment`: manifest changes in `deploy/apps/ouroboros` in this repo for the receiver path, scheduler, timeouts, storage and backups

## Impact

- **Code**: `api/` (migrations dir, OTel receiver, hook-event + transcript endpoints), `reporting/` (auth middleware, aggregates scheduler, export, analyst job), `dashboard/` (rewritten on Tailwind v4 + shadcn/ui + react-router), `client/` (hook dispatcher, OTel setup helper, transcript uploader, artifact sync), `plugin/` (hooks.json, version 0.2.0, disclosure).
- **Dependencies**: api adds `opentelemetry-proto` (protobuf parsing) and `protobuf`; reporting adds `anthropic` (analyst job), `httpx` and `pyjwt` (GitHub API and App tokens) and `pyarrow` (Parquet export, optional extra), and schedules with an asyncio ticker (no scheduler dependency); dashboard adds `tailwindcss`, `@tailwindcss/vite`, `tw-animate-css`, `react-router-dom` 7, `@tanstack/react-table`, `class-variance-authority`, `cn`, `lucide-react`, `@base-ui/react` (the headless primitives the current shadcn CLI installs; spike 0.3), `date-fns`, `react-day-picker` (shadcn `calendar`); React moves to 19.x and Recharts to 3.x (design D3). Hook path stays stdlib-only.
- **Database**: ~12 new tables; storage grows with hook events and usage facts (estimate: a few MB/day per active user); retention jobs required.
- **Platform**: manifests (`deploy/apps/ouroboros`) — HTTPRoute for `/v1/otel/*` and `/v1/telemetry/*` to the API with longer timeouts, a route for the GitHub webhook, reporting env, Postgres PVC growth, CNPG scheduled backups, analyst and GitHub secrets, optional CronJob.
- **Client machines**: `~/.claude/settings.json` gains the `CLAUDE_CODE_ENABLE_TELEMETRY` / `OTEL_*` env (user scope; project settings are ignored for OTel); plugin update.
- **Privacy**: new sensitivity classes (`public` metadata, `internal` identifiers, `sensitive` verbatim text); every content-bearing collection path defaults off; disclosure in the plugin manifest, SKILL template and README updated; opt-out unchanged.
- **Risks**: shadcn/ui is copied source, so component upgrades are ours to apply (design D1), and any borrowed Tremor Raw file has no active upstream (D3); Claude Code hook/OTel schemas are versioned by Anthropic and may shift (D4 keeps raw payloads for replay); transcript line format undocumented (spike first).
