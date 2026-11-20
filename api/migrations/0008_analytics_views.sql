-- Analytics aggregations (design D13): materialized views in the `analytics`
-- schema, refreshed CONCURRENTLY by the reporting scheduler, so every view has
-- a unique index over its grain. Buckets use event time (`client_ts` / `at` /
-- `started_at`) in UTC, never `received_at` (design D17). Nullable text keys are
-- coalesced to '' so the unique indexes hold.
-- These are first-cut definitions over an empty database; expect them to be
-- replaced by later migrations once real data arrives.

CREATE SCHEMA IF NOT EXISTS analytics;

-- One row per skill/agent touch: skill activations (OTel), skill outcomes
-- (events) and subagent runs (hooks + OTel). Plain view shared by the
-- daily/weekly adoption rollups.
CREATE OR REPLACE VIEW analytics.activity AS
    SELECT (at AT TIME ZONE 'UTC')::date AS day,
           date_trunc('week', at AT TIME ZONE 'UTC')::date AS week,
           skill_name AS skill_or_agent, 'skill'::text AS kind,
           auth_oid, session_id, prompt_id
      FROM skill_activations
    UNION ALL
    SELECT (client_ts AT TIME ZONE 'UTC')::date,
           date_trunc('week', client_ts AT TIME ZONE 'UTC')::date,
           skill_name, 'skill', auth_oid, session_id, prompt_id
      FROM events
    UNION ALL
    SELECT (started_at AT TIME ZONE 'UTC')::date,
           date_trunc('week', started_at AT TIME ZONE 'UTC')::date,
           agent_type, 'agent', auth_oid, session_id, prompt_id
      FROM subagent_runs
     WHERE agent_type IS NOT NULL AND started_at IS NOT NULL;

CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.adoption_daily AS
    SELECT day, skill_or_agent, kind,
           count(DISTINCT auth_oid)   AS dau,
           count(DISTINCT session_id) AS sessions,
           count(DISTINCT prompt_id)  AS turns,
           count(*)                   AS invocations
      FROM analytics.activity
     GROUP BY day, skill_or_agent, kind
WITH DATA;
CREATE UNIQUE INDEX IF NOT EXISTS uq_adoption_daily
    ON analytics.adoption_daily (day, skill_or_agent, kind);

-- Retention: users active for the same skill/agent in week N and week N+1.
CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.adoption_weekly AS
    WITH users AS (
        SELECT DISTINCT week, skill_or_agent, kind, auth_oid FROM analytics.activity
    ),
    retained AS (
        SELECT u.week, u.skill_or_agent, u.kind, count(*) AS retained_users
          FROM users u
          JOIN users n
            ON n.week = u.week + 7
           AND n.skill_or_agent = u.skill_or_agent
           AND n.kind = u.kind
           AND n.auth_oid = u.auth_oid
         GROUP BY u.week, u.skill_or_agent, u.kind
    )
    SELECT a.week, a.skill_or_agent, a.kind,
           count(DISTINCT a.auth_oid)      AS wau,
           count(DISTINCT a.session_id)    AS sessions,
           count(DISTINCT a.prompt_id)     AS turns,
           count(*)                        AS invocations,
           coalesce(min(r.retained_users), 0) AS retained_users
      FROM analytics.activity a
      LEFT JOIN retained r
        ON r.week = a.week AND r.skill_or_agent = a.skill_or_agent AND r.kind = a.kind
     GROUP BY a.week, a.skill_or_agent, a.kind
WITH DATA;
CREATE UNIQUE INDEX IF NOT EXISTS uq_adoption_weekly
    ON analytics.adoption_weekly (week, skill_or_agent, kind);

CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.outcomes_daily AS
    SELECT (client_ts AT TIME ZONE 'UTC')::date AS day,
           skill_name,
           count(*) FILTER (WHERE status = 'success')        AS success,
           count(*) FILTER (WHERE status = 'failure')        AS failure,
           count(*) FILTER (WHERE status = 'user_corrected') AS user_corrected,
           count(*)                                          AS total,
           (count(*) FILTER (WHERE status = 'user_corrected'))::numeric / count(*) AS correction_rate
      FROM events
     GROUP BY 1, 2
WITH DATA;
CREATE UNIQUE INDEX IF NOT EXISTS uq_outcomes_daily
    ON analytics.outcomes_daily (day, skill_name);

CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.error_codes_daily AS
    SELECT (client_ts AT TIME ZONE 'UTC')::date AS day,
           skill_name,
           error_code,
           count(*) AS errors
      FROM events
     WHERE error_code IS NOT NULL
     GROUP BY 1, 2, 3
WITH DATA;
CREATE UNIQUE INDEX IF NOT EXISTS uq_error_codes_daily
    ON analytics.error_codes_daily (day, skill_name, error_code);

-- kind = 'skill' (events.latency_ms) or 'tool' (tool_calls.duration_ms).
CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.latency_daily AS
    WITH samples AS (
        SELECT (client_ts AT TIME ZONE 'UTC')::date AS day, 'skill'::text AS kind,
               skill_name AS name, latency_ms::double precision AS ms
          FROM events
         WHERE latency_ms IS NOT NULL
        UNION ALL
        SELECT (started_at AT TIME ZONE 'UTC')::date, 'tool',
               coalesce(tool_name, ''), duration_ms::double precision
          FROM tool_calls
         WHERE duration_ms IS NOT NULL AND started_at IS NOT NULL
    )
    SELECT day, kind, name,
           count(*) AS samples,
           percentile_cont(0.5)  WITHIN GROUP (ORDER BY ms) AS p50,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY ms) AS p95,
           percentile_cont(0.99) WITHIN GROUP (ORDER BY ms) AS p99
      FROM samples
     GROUP BY day, kind, name
WITH DATA;
CREATE UNIQUE INDEX IF NOT EXISTS uq_latency_daily
    ON analytics.latency_daily (day, kind, name);

-- Cost and tokens from `api_request` rows, the primary usage fact (spike 0.2);
-- the `token` / `cost` delta counters are reconciliation-only and excluded so
-- no series double counts.
CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.cost_daily AS
    SELECT (at AT TIME ZONE 'UTC')::date AS day,
           coalesce(model, '')      AS model,
           coalesce(skill_name, '') AS skill_name,
           coalesce(agent_name, '') AS agent_name,
           coalesce(sum((attrs->>'input_tokens')::numeric), 0)          AS input_tokens,
           coalesce(sum((attrs->>'output_tokens')::numeric), 0)         AS output_tokens,
           coalesce(sum((attrs->>'cache_read_tokens')::numeric), 0)     AS cache_read,
           coalesce(sum((attrs->>'cache_creation_tokens')::numeric), 0) AS cache_create,
           coalesce(sum(cost_usd), 0)                                    AS cost_usd,
           count(*)                   AS api_requests,
           count(DISTINCT session_id) AS sessions
      FROM usage_facts
     WHERE kind = 'api_request'
     GROUP BY 1, 2, 3, 4
WITH DATA;
CREATE UNIQUE INDEX IF NOT EXISTS uq_cost_daily
    ON analytics.cost_daily (day, model, skill_name, agent_name);

CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.cost_per_session AS
    SELECT session_id,
           min(at) AS first_at,
           max(at) AS last_at,
           coalesce(sum((attrs->>'input_tokens')::numeric), 0)          AS input_tokens,
           coalesce(sum((attrs->>'output_tokens')::numeric), 0)         AS output_tokens,
           coalesce(sum((attrs->>'cache_read_tokens')::numeric), 0)     AS cache_read,
           coalesce(sum((attrs->>'cache_creation_tokens')::numeric), 0) AS cache_create,
           coalesce(sum(cost_usd), 0)                                    AS cost_usd,
           count(*)                                                      AS api_requests
      FROM usage_facts
     WHERE kind = 'api_request'
     GROUP BY session_id
WITH DATA;
CREATE UNIQUE INDEX IF NOT EXISTS uq_cost_per_session
    ON analytics.cost_per_session (session_id);

-- Context-bloat heuristic (design D13), to be recalibrated with data:
--   bloat_index = always_on_tokens / AUTO_COMPACT_WINDOW (160000)
--               + compactions per 100 turns * 0.5
--               + mean_pre_tokens / CONTEXT_WINDOW (200000) * 0.5
-- always_on_tokens and loaded_artifacts are per-session means over the day;
-- the always-on estimate comes from the artifact version whose hash the
-- InstructionsLoaded event reported.
CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.context_bloat_daily AS
    WITH s AS (
        SELECT session_id,
               (started_at AT TIME ZONE 'UTC')::date AS day,
               coalesce(project_key, '') AS project_key
          FROM sessions
         WHERE started_at IS NOT NULL
    ),
    loads AS (
        SELECT il.session_id,
               count(DISTINCT il.artifact_id) AS loaded_artifacts,
               coalesce(sum(av.estimated_tokens_always_on), 0) AS always_on_tokens
          FROM (SELECT DISTINCT session_id, artifact_id, content_hash
                  FROM instructions_loaded
                 WHERE artifact_id IS NOT NULL) il
          LEFT JOIN artifact_versions av
            ON av.artifact_id = il.artifact_id AND av.content_hash = il.content_hash
         GROUP BY il.session_id
    ),
    t AS (SELECT session_id, count(*) AS turns FROM turns GROUP BY session_id),
    c AS (SELECT session_id, count(*) AS compactions, avg(pre_tokens) AS mean_pre_tokens
            FROM compactions GROUP BY session_id)
    SELECT s.day, s.project_key,
           count(*)                                   AS sessions,
           coalesce(sum(t.turns), 0)                  AS turns,
           avg(coalesce(l.always_on_tokens, 0))       AS always_on_tokens,
           avg(coalesce(l.loaded_artifacts, 0))       AS loaded_artifacts,
           coalesce(sum(c.compactions), 0)            AS compactions,
           avg(c.mean_pre_tokens)                     AS mean_pre_tokens,
           avg(coalesce(l.always_on_tokens, 0)) / 160000.0
             + CASE WHEN coalesce(sum(t.turns), 0) > 0
                    THEN coalesce(sum(c.compactions), 0) * 100.0 / sum(t.turns) * 0.5
                    ELSE 0 END
             + coalesce(avg(c.mean_pre_tokens), 0) / 200000.0 * 0.5 AS bloat_index
      FROM s
      LEFT JOIN loads l USING (session_id)
      LEFT JOIN t USING (session_id)
      LEFT JOIN c USING (session_id)
     GROUP BY s.day, s.project_key
WITH DATA;
CREATE UNIQUE INDEX IF NOT EXISTS uq_context_bloat_daily
    ON analytics.context_bloat_daily (day, project_key);

-- Behavioural half of the sentiment blend (weights mirror the reporting
-- defaults: success 0.3, failure -1.0, user_corrected -0.6). The VADER text
-- score is computed in Python by the scheduler and is not part of this view.
CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.sentiment_daily AS
    SELECT (client_ts AT TIME ZONE 'UTC')::date AS day,
           skill_name,
           count(*) AS event_count,
           (count(*) FILTER (WHERE feedback->>'user_corrections' IS NOT NULL
                                OR length(coalesce(feedback->>'notes', '')) > 0))::numeric
             / count(*) AS text_coverage,
           avg(CASE status WHEN 'success' THEN 0.3
                           WHEN 'failure' THEN -1.0
                           WHEN 'user_corrected' THEN -0.6 END) AS behavioral_sentiment
      FROM events
     GROUP BY 1, 2
WITH DATA;
CREATE UNIQUE INDEX IF NOT EXISTS uq_sentiment_daily
    ON analytics.sentiment_daily (day, skill_name);

-- Weekly correction themes: the 50 most frequent word bigrams in verbatim
-- user corrections (stop words removed), with up to five example events.
CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.correction_themes AS
    WITH corrections AS (
        SELECT event_id,
               date_trunc('week', client_ts AT TIME ZONE 'UTC')::date AS week,
               lower(feedback->>'user_corrections') AS txt
          FROM events
         WHERE feedback->>'user_corrections' IS NOT NULL
    ),
    words AS (
        SELECT c.event_id, c.week, w.word, w.ord
          FROM corrections c,
               regexp_split_to_table(c.txt, '[^a-z0-9]+') WITH ORDINALITY AS w(word, ord)
         WHERE length(w.word) >= 3
           AND w.word NOT IN ('the', 'and', 'that', 'this', 'with', 'for', 'you', 'not',
                              'but', 'are', 'was', 'have', 'has', 'had', 'from', 'your',
                              'should', 'would', 'could', 'please', 'instead', 'than',
                              'then', 'into', 'its', 'our', 'use', 'used', 'just', 'also',
                              'about', 'when', 'what', 'which', 'there', 'their', 'they',
                              'them', 'been', 'were', 'will', 'can', 'did', 'does', 'don',
                              'didn', 'doesn', 'all', 'any', 'one', 'out', 'more', 'only')
    ),
    bigrams AS (
        SELECT event_id, week,
               word || ' ' || lead(word) OVER (PARTITION BY event_id ORDER BY ord) AS theme
          FROM words
    ),
    counted AS (
        SELECT week, theme, count(*) AS count,
               (array_agg(DISTINCT event_id))[1:5] AS example_event_ids
          FROM bigrams
         WHERE theme IS NOT NULL
         GROUP BY week, theme
    ),
    ranked AS (
        SELECT week, theme, count, example_event_ids,
               row_number() OVER (PARTITION BY week ORDER BY count DESC, theme) AS rn
          FROM counted
    )
    SELECT week, theme, count, example_event_ids
      FROM ranked
     WHERE rn <= 50
WITH DATA;
CREATE UNIQUE INDEX IF NOT EXISTS uq_correction_themes
    ON analytics.correction_themes (week, theme);
