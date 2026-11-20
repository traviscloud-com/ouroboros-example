-- Session model (design D9): the replayable hook-event store and the
-- normalized session / turn / lifecycle tables it is projected into.
-- Idempotent DDL; identity (auth_oid/auth_upn) is stamped server-side from the
-- validated bearer token, `received_at` is server time, and event time lives in
-- `client_ts` / `started_at` / `at` (design D17).
--
-- Natural ids from Claude Code (`prompt_id`, `agent_id`, `tool_use_id`) are TEXT:
-- only `session_id` is guaranteed to be a UUID. Lifecycle tables reference
-- `sessions`; writers upsert a stub `sessions` row first so hook and OTel data
-- can arrive in any order. `artifact_id` columns are plain here and gain their
-- foreign key in 0004 once `artifacts` exists.

CREATE TABLE IF NOT EXISTS hook_events_raw (
    hook_event_id   UUID        PRIMARY KEY,
    session_id      UUID        NOT NULL,
    prompt_id       TEXT,
    agent_id        TEXT,
    hook_event_name TEXT        NOT NULL,
    client_ts       TIMESTAMPTZ NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    client_version  TEXT,
    auth_oid        TEXT        NOT NULL,
    auth_upn        TEXT        NOT NULL,
    -- Whitelisted projection of the hook stdin (design D6); may carry sensitive
    -- text when the client's capture toggles are on, so it follows the
    -- sensitive retention clock.
    payload         JSONB       NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hook_events_raw_session ON hook_events_raw (session_id);
CREATE INDEX IF NOT EXISTS idx_hook_events_raw_prompt ON hook_events_raw (prompt_id);
CREATE INDEX IF NOT EXISTS idx_hook_events_raw_client_ts ON hook_events_raw (client_ts);
CREATE INDEX IF NOT EXISTS idx_hook_events_raw_name_ts ON hook_events_raw (hook_event_name, client_ts);
CREATE INDEX IF NOT EXISTS idx_hook_events_raw_payload ON hook_events_raw USING GIN (payload);

CREATE TABLE IF NOT EXISTS sessions (
    session_id              UUID        PRIMARY KEY,
    started_at              TIMESTAMPTZ,
    ended_at                TIMESTAMPTZ,
    source                  TEXT,
    model_initial           TEXT,
    agent_type              TEXT,
    cwd_hash                TEXT,
    project_key             TEXT,
    permission_mode         TEXT,
    effort                  TEXT,
    title_redacted          TEXT,       -- sensitive class
    end_reason              TEXT,
    context_tokens_at_start INTEGER,
    client_version          TEXT,
    auth_oid                TEXT        NOT NULL,
    auth_upn                TEXT        NOT NULL,
    received_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw                     JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions (started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_project_started ON sessions (project_key, started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_auth_oid ON sessions (auth_oid);

CREATE TABLE IF NOT EXISTS turns (
    turn_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID        NOT NULL REFERENCES sessions (session_id),
    prompt_id       TEXT        NOT NULL UNIQUE,
    seq             INTEGER,
    submitted_at    TIMESTAMPTZ,
    stopped_at      TIMESTAMPTZ,
    prompt_length   INTEGER,
    prompt_text     TEXT,       -- sensitive class
    response_length INTEGER,
    response_text   TEXT,       -- sensitive class
    model           TEXT,
    expansion_type  TEXT,
    command_name    TEXT,
    command_source  TEXT,
    auth_oid        TEXT        NOT NULL,
    auth_upn        TEXT        NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw             JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_turns_session_seq ON turns (session_id, seq);
CREATE INDEX IF NOT EXISTS idx_turns_submitted_at ON turns (submitted_at);
CREATE INDEX IF NOT EXISTS idx_turns_command_submitted ON turns (command_name, submitted_at);

CREATE TABLE IF NOT EXISTS tool_calls (
    tool_use_id    TEXT        PRIMARY KEY,
    session_id     UUID        NOT NULL REFERENCES sessions (session_id),
    prompt_id      TEXT,
    agent_id       TEXT,
    tool_name      TEXT,
    mcp_server     TEXT,
    started_at     TIMESTAMPTZ,
    duration_ms    INTEGER,
    success        BOOLEAN,
    is_interrupt   BOOLEAN,
    input_bytes    INTEGER,
    output_bytes   INTEGER,
    error_class    TEXT,
    decision       TEXT,
    input_content  TEXT,       -- sensitive class, only with OUROBOROS_CAPTURE_TOOL_CONTENT=on
    output_content TEXT,       -- sensitive class, same toggle
    auth_oid       TEXT        NOT NULL,
    auth_upn       TEXT        NOT NULL,
    received_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw            JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls (session_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_prompt ON tool_calls (prompt_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_started_at ON tool_calls (started_at);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool_started ON tool_calls (tool_name, started_at);

CREATE TABLE IF NOT EXISTS subagent_runs (
    agent_id            TEXT        PRIMARY KEY,
    session_id          UUID        NOT NULL REFERENCES sessions (session_id),
    prompt_id           TEXT,
    agent_type          TEXT,
    started_at          TIMESTAMPTZ,
    stopped_at          TIMESTAMPTZ,
    total_tokens        BIGINT,
    total_tool_uses     INTEGER,
    transcript_hash     TEXT,
    last_message_length INTEGER,
    auth_oid            TEXT        NOT NULL,
    auth_upn            TEXT        NOT NULL,
    received_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw                 JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_subagent_runs_session ON subagent_runs (session_id);
CREATE INDEX IF NOT EXISTS idx_subagent_runs_prompt ON subagent_runs (prompt_id);
CREATE INDEX IF NOT EXISTS idx_subagent_runs_started_at ON subagent_runs (started_at);
CREATE INDEX IF NOT EXISTS idx_subagent_runs_type_started ON subagent_runs (agent_type, started_at);

-- Rows below have no natural id of their own; writers derive a deterministic
-- UUID (uuid5 of the source event id) so replays upsert instead of duplicating.
CREATE TABLE IF NOT EXISTS compactions (
    id                         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id                 UUID        NOT NULL REFERENCES sessions (session_id),
    prompt_id                  TEXT,
    trigger                    TEXT,
    pre_tokens                 INTEGER,
    post_tokens                INTEGER,
    at                         TIMESTAMPTZ NOT NULL,
    custom_instructions_length INTEGER,
    summary_length             INTEGER,
    summary_text               TEXT,       -- sensitive class, only with OUROBOROS_CAPTURE_PROMPTS=on
    auth_oid                   TEXT        NOT NULL,
    auth_upn                   TEXT        NOT NULL,
    received_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw                        JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_compactions_session ON compactions (session_id);
CREATE INDEX IF NOT EXISTS idx_compactions_prompt ON compactions (prompt_id);
CREATE INDEX IF NOT EXISTS idx_compactions_at ON compactions (at);

CREATE TABLE IF NOT EXISTS instructions_loaded (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id        UUID        NOT NULL REFERENCES sessions (session_id),
    prompt_id         TEXT,
    artifact_id       UUID,       -- FK added in 0004
    file_path         TEXT        NOT NULL,
    content_hash      TEXT,
    memory_type       TEXT,
    load_reason       TEXT,
    trigger_file_path TEXT,
    parent_file_path  TEXT,
    at                TIMESTAMPTZ NOT NULL,
    auth_oid          TEXT        NOT NULL,
    auth_upn          TEXT        NOT NULL,
    received_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw               JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_instructions_loaded_session ON instructions_loaded (session_id);
CREATE INDEX IF NOT EXISTS idx_instructions_loaded_prompt ON instructions_loaded (prompt_id);
CREATE INDEX IF NOT EXISTS idx_instructions_loaded_at ON instructions_loaded (at);
CREATE INDEX IF NOT EXISTS idx_instructions_loaded_artifact ON instructions_loaded (artifact_id);
CREATE INDEX IF NOT EXISTS idx_instructions_loaded_hash ON instructions_loaded (content_hash);

CREATE TABLE IF NOT EXISTS model_switches (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id     UUID        NOT NULL REFERENCES sessions (session_id),
    prompt_id      TEXT,
    phase          TEXT        NOT NULL CHECK (phase IN ('pre', 'post')),
    from_model     TEXT,
    to_model       TEXT,
    context_tokens INTEGER,
    at             TIMESTAMPTZ NOT NULL,
    auth_oid       TEXT        NOT NULL,
    auth_upn       TEXT        NOT NULL,
    received_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw            JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_model_switches_session ON model_switches (session_id);
CREATE INDEX IF NOT EXISTS idx_model_switches_prompt ON model_switches (prompt_id);
CREATE INDEX IF NOT EXISTS idx_model_switches_at ON model_switches (at);

-- api_error / api_refusal OTel events and tool failures that are not tied to a
-- tool_use_id.
CREATE TABLE IF NOT EXISTS turn_errors (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  UUID        NOT NULL REFERENCES sessions (session_id),
    prompt_id   TEXT,
    kind        TEXT        NOT NULL CHECK (kind IN ('api_error', 'api_refusal', 'tool_error', 'other')),
    model       TEXT,
    error_code  TEXT,
    status_code INTEGER,
    error_class TEXT,
    message     TEXT,       -- sensitive class
    duration_ms INTEGER,
    at          TIMESTAMPTZ NOT NULL,
    auth_oid    TEXT        NOT NULL,
    auth_upn    TEXT        NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw         JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_turn_errors_session ON turn_errors (session_id);
CREATE INDEX IF NOT EXISTS idx_turn_errors_prompt ON turn_errors (prompt_id);
CREATE INDEX IF NOT EXISTS idx_turn_errors_at ON turn_errors (at);
CREATE INDEX IF NOT EXISTS idx_turn_errors_kind_at ON turn_errors (kind, at);

-- One row per hook_execution_start / hook_execution_complete OTel event (spike
-- 0.2): lets us measure the latency of our own hooks.
CREATE TABLE IF NOT EXISTS hook_runs (
    id                     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id             UUID        NOT NULL REFERENCES sessions (session_id),
    prompt_id              TEXT,
    phase                  TEXT        NOT NULL CHECK (phase IN ('start', 'complete')),
    hook_event             TEXT        NOT NULL,   -- e.g. UserPromptSubmit, Stop
    hook_name              TEXT,
    hook_source            TEXT,                   -- plugin | user | project | managed | merged
    plugin_name            TEXT,
    num_hooks              INTEGER,
    num_success            INTEGER,
    num_blocking           INTEGER,
    num_non_blocking_error INTEGER,
    num_cancelled          INTEGER,
    duration_ms            INTEGER,
    success                BOOLEAN,
    stdout_chars           INTEGER,
    additional_context_chars INTEGER,
    system_message_chars   INTEGER,
    initial_user_message_chars INTEGER,
    num_outputs_persisted  INTEGER,
    at                     TIMESTAMPTZ NOT NULL,
    auth_oid               TEXT        NOT NULL,
    auth_upn               TEXT        NOT NULL,
    received_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw                    JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_hook_runs_session ON hook_runs (session_id);
CREATE INDEX IF NOT EXISTS idx_hook_runs_prompt ON hook_runs (prompt_id);
CREATE INDEX IF NOT EXISTS idx_hook_runs_at ON hook_runs (at);
CREATE INDEX IF NOT EXISTS idx_hook_runs_event_at ON hook_runs (hook_event, at);
