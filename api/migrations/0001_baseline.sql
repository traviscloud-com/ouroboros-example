-- Ouroboros telemetry events — idempotent DDL, safe to re-apply.

CREATE TABLE IF NOT EXISTS events (
    event_id      UUID PRIMARY KEY,
    session_id    UUID        NOT NULL,
    client_ts     TIMESTAMPTZ NOT NULL,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    skill_name    TEXT        NOT NULL,
    skill_version TEXT        NOT NULL,
    status        TEXT        NOT NULL
        CHECK (status IN ('success', 'failure', 'user_corrected')),
    model         TEXT,
    latency_ms    INTEGER,
    error_code    TEXT,
    -- Identity stamped server-side from validated JWT claims (SR-2)
    auth_oid      TEXT        NOT NULL,
    auth_upn      TEXT        NOT NULL,
    -- feedback.user_corrections is sensitive verbatim user content (PR-1)
    feedback      JSONB       NOT NULL
);

-- v1.1: optional verbatim conversation content — same sensitivity class as
-- feedback.user_corrections (PR-1)
ALTER TABLE events ADD COLUMN IF NOT EXISTS interaction JSONB;

CREATE INDEX IF NOT EXISTS idx_events_session ON events (session_id);
CREATE INDEX IF NOT EXISTS idx_events_skill_status ON events (skill_name, status);
CREATE INDEX IF NOT EXISTS idx_events_received_at ON events (received_at);
