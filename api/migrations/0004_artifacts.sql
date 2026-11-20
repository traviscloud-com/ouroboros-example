-- Artifact registry (design D10): the skills, hooks, agents, commands,
-- instructions, rules and plugins we observe or sync, with content-hashed
-- versions and token estimates. Artifact text is `internal` class (our own
-- source, already in Git), not `sensitive`.
-- `owner_repo` and `path` default to '' rather than NULL so the natural key
-- (kind, name, owner_repo, path) can back ON CONFLICT upserts. OTel
-- `plugin_loaded` / `hook_registered` records (spike 0.2) register plugins and
-- hooks by `plugin_id_hash` when names are redacted (`third-party`) until a
-- named record arrives.

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    kind        TEXT        NOT NULL
        CHECK (kind IN ('skill', 'hook', 'agent', 'command', 'instructions', 'rule', 'plugin')),
    name        TEXT        NOT NULL,
    owner_repo  TEXT        NOT NULL DEFAULT '',
    path        TEXT        NOT NULL DEFAULT '',
    source      TEXT        CHECK (source IN ('plugin', 'user', 'project', 'managed')),
    plugin_id_hash TEXT,
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    auth_oid    TEXT        NOT NULL,
    auth_upn    TEXT        NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (kind, name, owner_repo, path)
);
CREATE INDEX IF NOT EXISTS idx_artifacts_kind_name ON artifacts (kind, name);
CREATE INDEX IF NOT EXISTS idx_artifacts_last_seen ON artifacts (last_seen);
CREATE INDEX IF NOT EXISTS idx_artifacts_plugin_id_hash ON artifacts (plugin_id_hash);

CREATE TABLE IF NOT EXISTS artifact_versions (
    version_id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id               UUID        NOT NULL REFERENCES artifacts (artifact_id) ON DELETE CASCADE,
    content_hash              TEXT        NOT NULL,
    content_text              TEXT,       -- internal class
    frontmatter               JSONB,
    estimated_tokens_always_on INTEGER,
    estimated_tokens_body     INTEGER,
    seen_from                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    seen_until                TIMESTAMPTZ,
    commit_sha                TEXT,
    auth_oid                  TEXT        NOT NULL,
    auth_upn                  TEXT        NOT NULL,
    received_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw                       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (artifact_id, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_artifact_versions_hash ON artifact_versions (content_hash);
CREATE INDEX IF NOT EXISTS idx_artifact_versions_seen_from ON artifact_versions (seen_from);

-- Link the lifecycle tables created before the registry existed.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_instructions_loaded_artifact') THEN
        ALTER TABLE instructions_loaded
            ADD CONSTRAINT fk_instructions_loaded_artifact
            FOREIGN KEY (artifact_id) REFERENCES artifacts (artifact_id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_skill_activations_artifact') THEN
        ALTER TABLE skill_activations
            ADD CONSTRAINT fk_skill_activations_artifact
            FOREIGN KEY (artifact_id) REFERENCES artifacts (artifact_id) ON DELETE SET NULL;
    END IF;
END $$;
