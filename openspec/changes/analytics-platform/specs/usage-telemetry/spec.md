# usage-telemetry (delta)

## ADDED Requirements

### Requirement: OTLP/HTTP receiver endpoints
The ingestion API SHALL expose `POST /v1/otel/v1/metrics` and `POST /v1/otel/v1/logs` accepting OTLP/HTTP payloads in both `application/x-protobuf` and `application/json`, SHALL require the same `Telemetry.Write` bearer token as `/v1/telemetry/events`, and SHALL respond with the OTLP success response on acceptance. Records whose metric or event name is not mapped SHALL be counted and dropped, never rejected.

#### Scenario: Protobuf metrics batch accepted
- **WHEN** Claude Code exports a `http/protobuf` metrics batch with a valid bearer token
- **THEN** the receiver returns 200 with an empty `ExportMetricsServiceResponse` and the mapped data points are stored

#### Scenario: Unknown metric tolerated
- **WHEN** a batch contains a metric name the receiver does not map
- **THEN** the batch is still accepted, the unmapped record increments a counter, and no error is returned

#### Scenario: Missing token rejected
- **WHEN** a request to `/v1/otel/v1/logs` carries no bearer token
- **THEN** the receiver returns 401 and stores nothing

### Requirement: Usage and cost facts mapped from Claude Code telemetry
The receiver SHALL map `claude_code.token.usage` and `claude_code.cost.usage` data points, and `api_request` log events, into `usage_facts` rows carrying `session_id`, `prompt_id` when present, event time, `model`, token type, value, `cost_usd`, `skill.name`, `plugin.name`, `agent.name` and `query_source`, stamping `auth_oid`/`auth_upn` from the token server-side.

#### Scenario: Token metric attributed to a skill
- **WHEN** a `claude_code.token.usage` data point arrives with `skill.name=telemetry-reporting`, `type=output`, `model=claude-sonnet-4-5`
- **THEN** a `usage_facts` row of kind `token` exists with those attributes, the data point's value, and the caller's identity

#### Scenario: api_request linked to its turn
- **WHEN** an `api_request` log event arrives with `prompt.id` matching a hook-ingested turn
- **THEN** the `usage_facts` row references that `prompt_id` and the turn's cost is visible on the session timeline

### Requirement: Lifecycle events mapped from logs
The receiver SHALL map `skill_activated` to `skill_activations`, `compaction` (with `pre_tokens`/`post_tokens`) to `compactions`, `subagent_completed` to `subagent_runs` (upsert by `agent_id`), `tool_result`/`tool_decision` into `tool_calls` enrichment, `api_error`/`api_refusal` to `turn_errors`, and `hook_execution_start`/`hook_execution_complete` to `hook_runs`.

#### Scenario: Compaction recorded with token delta
- **WHEN** a `compaction` event with `trigger=auto`, `pre_tokens=150000`, `post_tokens=40000` arrives
- **THEN** a `compactions` row exists for the session with those values and the context-bloat aggregate can count it

#### Scenario: Subagent facts converge from both pipelines
- **WHEN** a `SubagentStop` hook event and a `subagent_completed` OTel event arrive for the same `agent_id` in either order
- **THEN** exactly one `subagent_runs` row exists carrying both the hook's `agent_type`/timestamps and OTel's `total_tokens`/`total_tool_uses`

### Requirement: Client OTel setup with privacy-preserving defaults
The SDK SHALL provide `python3 -m ouroboros_client.setup_otel` that writes the OpenTelemetry environment block into the user-scope `~/.claude/settings.json` idempotently, mints a long-lived `Telemetry.Write` dev token for `OTEL_EXPORTER_OTLP_HEADERS`, and sets `OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_ASSISTANT_RESPONSES`, `OTEL_LOG_TOOL_CONTENT` and `OTEL_LOG_RAW_API_BODIES` to disabled while enabling `OTEL_LOG_TOOL_DETAILS`. The command SHALL print every variable it set and SHALL do nothing when `OUROBOROS_TELEMETRY=off`.

#### Scenario: Idempotent setup
- **WHEN** `setup_otel` runs twice
- **THEN** `~/.claude/settings.json` contains one OTel env block with the same keys and other settings are untouched

#### Scenario: Content logging stays off
- **WHEN** `setup_otel` runs with default arguments
- **THEN** the written env has `OTEL_LOG_USER_PROMPTS=0`, `OTEL_LOG_ASSISTANT_RESPONSES=0`, `OTEL_LOG_TOOL_CONTENT=0`, `OTEL_LOG_RAW_API_BODIES=0` and `OTEL_LOG_TOOL_DETAILS=1`
