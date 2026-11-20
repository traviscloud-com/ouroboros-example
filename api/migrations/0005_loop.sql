-- Improvement loop v1 (design D14): analyst proposals, human decisions,
-- measured outcomes, and the audit log shared with reporting auth (D12).

CREATE TABLE IF NOT EXISTS improvement_proposals (
    proposal_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    target_artifact_id UUID        NOT NULL REFERENCES artifacts (artifact_id),
    base_version_id    UUID        REFERENCES artifact_versions (version_id),
    rationale          TEXT        NOT NULL,
    expected_effect    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    evidence_event_ids UUID[]      NOT NULL DEFAULT '{}',
    diff               TEXT        NOT NULL,
    risk_class         TEXT        NOT NULL CHECK (risk_class IN ('low', 'medium', 'high')),
    status             TEXT        NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'approved', 'applied', 'rejected', 'superseded')),
    created_by         TEXT        NOT NULL CHECK (created_by IN ('human', 'loop')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_by         TEXT,
    decided_at         TIMESTAMPTZ,
    pr_url             TEXT,
    merge_sha          TEXT,
    applied_at         TIMESTAMPTZ,
    applied_version_id UUID        REFERENCES artifact_versions (version_id)
);
CREATE INDEX IF NOT EXISTS idx_improvement_proposals_status_created
    ON improvement_proposals (status, created_at);
CREATE INDEX IF NOT EXISTS idx_improvement_proposals_target
    ON improvement_proposals (target_artifact_id);

CREATE TABLE IF NOT EXISTS improvement_outcomes (
    proposal_id    UUID        PRIMARY KEY REFERENCES improvement_proposals (proposal_id) ON DELETE CASCADE,
    before_window  TSTZRANGE   NOT NULL,
    after_window   TSTZRANGE   NOT NULL,
    metrics_before JSONB       NOT NULL DEFAULT '{}'::jsonb,
    metrics_after  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    verdict        TEXT        NOT NULL
        CHECK (verdict IN ('improved', 'neutral', 'worse', 'insufficient_data')),
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every export, principal change and proposal decision lands here.
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL   PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_email TEXT        NOT NULL,
    actor_role  TEXT,
    action      TEXT        NOT NULL,
    target_kind TEXT,
    target_id   TEXT,
    details     JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_audit_log_at ON audit_log (at);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor_at ON audit_log (actor_email, at);
CREATE INDEX IF NOT EXISTS idx_audit_log_action_at ON audit_log (action, at);
