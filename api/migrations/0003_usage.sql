-- Usage and cost facts from Claude Code's built-in OpenTelemetry export
-- (design D5/D7/D9), plus model-invoked skill activations.
-- `kind` names the source. `api_request` log events are the primary usage
-- fact (spike 0.2): one row per request with `prompt_id`, `request_id`,
-- `cost_usd` and the token breakdown in attrs (`input_tokens`,
-- `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, plus the
-- 5m/1h cache split, `duration_ms`, `ttft_ms`). The `token` / `cost` delta
-- counters lack `prompt_id` and are kept only to reconcile totals; the
-- counters session / loc / commit / pr / active_time are their own kinds.

CREATE TABLE IF NOT EXISTS usage_facts (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID        NOT NULL REFERENCES sessions (session_id),
    prompt_id    TEXT,
    at           TIMESTAMPTZ NOT NULL,
    kind         TEXT        NOT NULL
        CHECK (kind IN ('token', 'cost', 'api_request', 'session', 'loc', 'commit', 'pr', 'active_time')),
    model        TEXT,
    token_type   TEXT,                 -- input | output | cacheRead | cacheCreation
    value        NUMERIC,
    cost_usd     NUMERIC,
    skill_name   TEXT,
    plugin_name  TEXT,
    agent_name   TEXT,
    query_source TEXT,
    request_id   TEXT,                 -- api_request only; natural id for upserts
    attrs        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    auth_oid     TEXT        NOT NULL,
    auth_upn     TEXT        NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_usage_facts_session ON usage_facts (session_id);
CREATE INDEX IF NOT EXISTS idx_usage_facts_prompt ON usage_facts (prompt_id);
CREATE INDEX IF NOT EXISTS idx_usage_facts_at ON usage_facts (at);
CREATE INDEX IF NOT EXISTS idx_usage_facts_skill_at ON usage_facts (skill_name, at);
CREATE INDEX IF NOT EXISTS idx_usage_facts_kind_at ON usage_facts (kind, at);
CREATE INDEX IF NOT EXISTS idx_usage_facts_attrs ON usage_facts USING GIN (attrs);
CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_facts_api_request
    ON usage_facts (request_id) WHERE kind = 'api_request' AND request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS skill_activations (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id         UUID        NOT NULL REFERENCES sessions (session_id),
    prompt_id          TEXT,
    artifact_id        UUID,       -- FK added in 0004
    skill_name         TEXT        NOT NULL,
    skill_source       TEXT,
    invocation_trigger TEXT,
    at                 TIMESTAMPTZ NOT NULL,
    auth_oid           TEXT        NOT NULL,
    auth_upn           TEXT        NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw                JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_skill_activations_session ON skill_activations (session_id);
CREATE INDEX IF NOT EXISTS idx_skill_activations_prompt ON skill_activations (prompt_id);
CREATE INDEX IF NOT EXISTS idx_skill_activations_at ON skill_activations (at);
CREATE INDEX IF NOT EXISTS idx_skill_activations_skill_at ON skill_activations (skill_name, at);
CREATE INDEX IF NOT EXISTS idx_skill_activations_artifact ON skill_activations (artifact_id);
