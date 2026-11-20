-- Improvement loop v1, Phase 5 (design D14): the full proposal state machine,
-- PR bookkeeping, and a per-run log of the analyst job.
--
-- States: proposed -> approved | rejected; approved -> pr_opened | failed;
-- pr_opened -> applied | failed. `superseded` stays reserved for a proposal
-- replaced by a newer one on the same artifact version. Every transition is
-- written to audit_log by the reporting service.

ALTER TABLE improvement_proposals DROP CONSTRAINT IF EXISTS improvement_proposals_status_check;
ALTER TABLE improvement_proposals ADD CONSTRAINT improvement_proposals_status_check
    CHECK (status IN ('proposed', 'approved', 'rejected', 'pr_opened', 'applied', 'failed', 'superseded'));

ALTER TABLE improvement_proposals ADD COLUMN IF NOT EXISTS target_path     TEXT;
ALTER TABLE improvement_proposals ADD COLUMN IF NOT EXISTS decision_reason TEXT;
ALTER TABLE improvement_proposals ADD COLUMN IF NOT EXISTS pr_repo         TEXT;
ALTER TABLE improvement_proposals ADD COLUMN IF NOT EXISTS pr_number       INTEGER;
ALTER TABLE improvement_proposals ADD COLUMN IF NOT EXISTS pr_branch       TEXT;
ALTER TABLE improvement_proposals ADD COLUMN IF NOT EXISTS failure_reason  TEXT;
ALTER TABLE improvement_proposals ADD COLUMN IF NOT EXISTS analyst_run_id  UUID;
ALTER TABLE improvement_proposals ADD COLUMN IF NOT EXISTS evidence        JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE improvement_proposals ADD COLUMN IF NOT EXISTS updated_at      TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_improvement_proposals_pr
    ON improvement_proposals (pr_repo, pr_number) WHERE pr_number IS NOT NULL;

-- One row per analyst run (scheduled or admin-triggered): what was looked at,
-- what was kept or discarded, and the Claude API token usage.
CREATE TABLE IF NOT EXISTS analyst_runs (
    run_id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at          TIMESTAMPTZ,
    trigger              TEXT        NOT NULL CHECK (trigger IN ('schedule', 'manual')),
    actor_email          TEXT,
    model                TEXT        NOT NULL,
    status               TEXT        NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed')),
    artifacts_considered INTEGER     NOT NULL DEFAULT 0,
    proposals_created    INTEGER     NOT NULL DEFAULT 0,
    outputs_discarded    INTEGER     NOT NULL DEFAULT 0,
    input_tokens         BIGINT      NOT NULL DEFAULT 0,
    output_tokens        BIGINT      NOT NULL DEFAULT 0,
    cache_read_tokens    BIGINT      NOT NULL DEFAULT 0,
    cache_creation_tokens BIGINT     NOT NULL DEFAULT 0,
    error                TEXT,
    details              JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_analyst_runs_started ON analyst_runs (started_at);
