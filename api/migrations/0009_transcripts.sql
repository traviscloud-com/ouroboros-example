-- Transcript ingestion (design D8, task 2.6): one row per chain line of a
-- Claude Code transcript (`user`, `assistant`, `system`, `attachment`),
-- uploaded explicitly by `python3 -m ouroboros_client.transcript`.
-- Idempotent DDL. Identity (auth_oid/auth_upn) is stamped server-side,
-- `received_at` is server time, `timestamp` is the line's own clock (D17).
--
-- Metadata columns are `internal` class. `content` is `sensitive` class: it
-- is null unless the batch was uploaded with `--content` under a recorded
-- consent, and the reporting retention job (task 3.3) nulls it after
-- RETENTION_SENSITIVE_DAYS while the usage metadata stays.
--
-- `line_uuid` is unique per session: subagent line uuids never appear in the
-- parent file, so (session_id, line_uuid) is the idempotency key for both.
-- `seq` is the line's position in parentUuid-chain order (not timestamp
-- order: the host clock can step back); `prompt_id` is inherited along the
-- chain for lines that do not carry `promptId` themselves.

CREATE TABLE IF NOT EXISTS transcript_lines (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id            UUID        NOT NULL REFERENCES sessions (session_id),
    agent_id              TEXT,                   -- subagent transcripts only
    line_uuid             TEXT        NOT NULL,
    parent_uuid           TEXT,
    seq                   INTEGER,
    type                  TEXT        NOT NULL,   -- user | assistant | system | attachment
    subtype               TEXT,                   -- system.subtype or attachment.type
    prompt_id             TEXT,
    role                  TEXT,
    model                 TEXT,
    message_id            TEXT,
    request_id            TEXT,
    stop_reason           TEXT,
    timestamp             TIMESTAMPTZ NOT NULL,
    input_tokens          BIGINT,
    output_tokens         BIGINT,
    cache_read_tokens     BIGINT,
    cache_creation_tokens BIGINT,
    thinking_tokens       BIGINT,
    tool_use_ids          TEXT[]      NOT NULL DEFAULT '{}',
    tool_names            TEXT[]      NOT NULL DEFAULT '{}',
    content_chars         INTEGER,
    is_sidechain          BOOLEAN     NOT NULL DEFAULT false,
    is_error              BOOLEAN,
    attribution_skill     TEXT,
    client_version        TEXT,                   -- Claude Code version on the line
    content               TEXT,                   -- sensitive class, only with --content
    auth_oid              TEXT        NOT NULL,
    auth_upn              TEXT        NOT NULL,
    received_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw                   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (session_id, line_uuid)
);
CREATE INDEX IF NOT EXISTS idx_transcript_lines_session   ON transcript_lines (session_id);
CREATE INDEX IF NOT EXISTS idx_transcript_lines_prompt    ON transcript_lines (prompt_id);
CREATE INDEX IF NOT EXISTS idx_transcript_lines_timestamp ON transcript_lines (timestamp);
CREATE INDEX IF NOT EXISTS idx_transcript_lines_agent     ON transcript_lines (agent_id);
CREATE INDEX IF NOT EXISTS idx_transcript_lines_message   ON transcript_lines (message_id);
