-- Link skill outcomes to turns and artifacts (design D9) and add full-text
-- search columns (design D11) replacing ILIKE scans.
-- `events.prompt_id` is the client-supplied join key; `turn_id` / `artifact_id`
-- are filled server-side when a matching turn / artifact exists.

ALTER TABLE events ADD COLUMN IF NOT EXISTS prompt_id   TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS turn_id     UUID;
ALTER TABLE events ADD COLUMN IF NOT EXISTS artifact_id UUID;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_events_turn') THEN
        ALTER TABLE events
            ADD CONSTRAINT fk_events_turn
            FOREIGN KEY (turn_id) REFERENCES turns (turn_id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_events_artifact') THEN
        ALTER TABLE events
            ADD CONSTRAINT fk_events_artifact
            FOREIGN KEY (artifact_id) REFERENCES artifacts (artifact_id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_events_prompt ON events (prompt_id);
CREATE INDEX IF NOT EXISTS idx_events_turn ON events (turn_id);
CREATE INDEX IF NOT EXISTS idx_events_artifact ON events (artifact_id);
CREATE INDEX IF NOT EXISTS idx_events_client_ts ON events (client_ts);
CREATE INDEX IF NOT EXISTS idx_events_skill_client_ts ON events (skill_name, client_ts);

-- Generated tsvector columns: `to_tsvector(regconfig, text)` and
-- `jsonb_to_tsvector(regconfig, jsonb, jsonb)` are IMMUTABLE with an explicit
-- configuration, which STORED generated columns require.
ALTER TABLE turns ADD COLUMN IF NOT EXISTS prompt_tsv TSVECTOR
    GENERATED ALWAYS AS (to_tsvector('english', coalesce(prompt_text, ''))) STORED;
ALTER TABLE turns ADD COLUMN IF NOT EXISTS response_tsv TSVECTOR
    GENERATED ALWAYS AS (to_tsvector('english', coalesce(response_text, ''))) STORED;
CREATE INDEX IF NOT EXISTS idx_turns_prompt_tsv ON turns USING GIN (prompt_tsv);
CREATE INDEX IF NOT EXISTS idx_turns_response_tsv ON turns USING GIN (response_tsv);

-- Indexes every string value in feedback (notes, user_corrections, suggested_patch).
ALTER TABLE events ADD COLUMN IF NOT EXISTS feedback_tsv TSVECTOR
    GENERATED ALWAYS AS (jsonb_to_tsvector('english', feedback, '["string"]'::jsonb)) STORED;
CREATE INDEX IF NOT EXISTS idx_events_feedback_tsv ON events USING GIN (feedback_tsv);
