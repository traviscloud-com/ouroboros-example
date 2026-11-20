-- Phase 3 (tasks 3.4, 3.6): the two analytics series the reporting scheduler
-- computes in Python become plain tables it writes, replacing the first-cut
-- matviews from 0008 (which could only express the behavioural half of the
-- sentiment blend and a SQL bigram count without the VADER negativity gate).
--   * analytics.sentiment_daily: per day x skill, VADER text score + the
--     behavioural score + the blended sentiment (same weights as
--     /v1/reports/sentiment), written by reporting/app/sentiment.py.
--   * analytics.correction_themes: per ISO week, the most frequent word
--     bigrams in blended-negative user corrections with example event ids,
--     written by reporting/app/themes.py.
-- Also adds a generated tsvector over events.interaction so the `q` filter on
-- /v1/reports/events can use websearch_to_tsquery on prompts as well as on
-- feedback (0007 covered feedback only).
-- Idempotent: the DROPs only fire while the 0008 matview is still there.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'analytics' AND c.relname = 'sentiment_daily' AND c.relkind = 'm') THEN
        DROP MATERIALIZED VIEW analytics.sentiment_daily;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'analytics' AND c.relname = 'correction_themes' AND c.relkind = 'm') THEN
        DROP MATERIALIZED VIEW analytics.correction_themes;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS analytics.sentiment_daily (
    day                  DATE        NOT NULL,
    skill_name           TEXT        NOT NULL,
    event_count          INTEGER     NOT NULL,
    text_count           INTEGER     NOT NULL,   -- events that carried user text
    text_coverage        NUMERIC     NOT NULL,
    text_sentiment       NUMERIC,                -- mean VADER compound over text_count events
    behavioral_sentiment NUMERIC     NOT NULL,   -- mean status weight
    sentiment            NUMERIC     NOT NULL,   -- mean blended score (the dashboard number)
    computed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (day, skill_name)
);

CREATE TABLE IF NOT EXISTS analytics.correction_themes (
    week              DATE        NOT NULL,      -- Monday of the ISO week (UTC)
    theme             TEXT        NOT NULL,      -- word bigram, lower-case
    count             INTEGER     NOT NULL,      -- distinct corrections containing the bigram
    example_event_ids UUID[]      NOT NULL DEFAULT '{}',
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (week, theme)
);
CREATE INDEX IF NOT EXISTS idx_correction_themes_week_count
    ON analytics.correction_themes (week, count DESC);

ALTER TABLE events ADD COLUMN IF NOT EXISTS interaction_tsv TSVECTOR
    GENERATED ALWAYS AS (jsonb_to_tsvector('english', coalesce(interaction, '{}'::jsonb), '["string"]'::jsonb)) STORED;
CREATE INDEX IF NOT EXISTS idx_events_interaction_tsv ON events USING GIN (interaction_tsv);
