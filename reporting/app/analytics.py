"""Analytics series over the ``analytics.*`` materialized views and the
scheduler-written tables (design D13). Every function takes a
:class:`~app.queries.Window` and returns ``{"series": [...], "totals": {...}}``
so the routes can wrap the current and the comparison window in one envelope.

Buckets are ``day`` (the views' grain) or ``week``; weekly percentiles and
bloat components are sample/session-weighted means of the daily values and
are flagged ``approximate``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .queries import ANALYTICS_BUCKETS, Window, bucket_expr, plain, rows_to_dicts

BLOAT_FORMULA = (
    "bloat_index = always_on_tokens / 160000 (autoCompactWindow)"
    " + compactions per 100 turns * 0.5"
    " + mean_pre_tokens / 200000 (context window) * 0.5"
    " - heuristic, to be recalibrated once real data arrives (design D13)"
)


def _check_bucket(bucket: str) -> None:
    if bucket not in ANALYTICS_BUCKETS:
        raise ValueError(f"bucket must be one of {ANALYTICS_BUCKETS}")


def _sum(rows: list[dict], key: str) -> float | int:
    return sum((r.get(key) or 0) for r in rows)


# --- adoption ----------------------------------------------------------------------


def adoption(engine: Engine, window: Window, *, bucket: str = "day",
             kind: str | None = None, name: str | None = None) -> dict:
    _check_bucket(bucket)
    params = {**window.params(), "kind": kind, "name": name}
    filters = ""
    if kind is not None:
        filters += " AND kind = :kind"
    if name is not None:
        filters += " AND skill_or_agent = :name"
    if bucket == "day":
        series_sql = f"""
            SELECT day AS bucket, skill_or_agent AS name, kind,
                   dau AS users, sessions, turns, invocations
              FROM analytics.adoption_daily
             WHERE day BETWEEN :start AND :end {filters}
             ORDER BY bucket, invocations DESC, name"""
    else:
        series_sql = f"""
            SELECT week AS bucket, skill_or_agent AS name, kind,
                   wau AS users, sessions, turns, invocations, retained_users
              FROM analytics.adoption_weekly
             WHERE week BETWEEN date_trunc('week', CAST(:start AS date))::date AND :end {filters}
             ORDER BY bucket, invocations DESC, name"""
    top_sql = f"""
        SELECT skill_or_agent AS name, kind,
               sum(invocations) AS invocations, sum(turns) AS turns, max(dau) AS peak_dau
          FROM analytics.adoption_daily
         WHERE day BETWEEN :start AND :end {filters}
         GROUP BY 1, 2 ORDER BY invocations DESC, name LIMIT 50"""
    # Distinct users / sessions over the window cannot be summed from the daily
    # grain; the plain activity view (same event-time buckets) answers them.
    totals_sql = f"""
        SELECT count(DISTINCT auth_oid)   AS users,
               count(DISTINCT session_id) AS sessions,
               count(DISTINCT prompt_id)  AS turns,
               count(*)                   AS invocations,
               count(DISTINCT skill_or_agent) AS names
          FROM analytics.activity
         WHERE day BETWEEN :start AND :end {filters}"""
    with engine.connect() as conn:
        series = rows_to_dicts(conn.execute(text(series_sql), params))
        top = rows_to_dicts(conn.execute(text(top_sql), params))
        totals = rows_to_dicts(conn.execute(text(totals_sql), params))[0]
    return {"series": series, "top": top, "totals": totals}


# --- outcomes -------------------------------------------------------------------------


def outcomes(engine: Engine, window: Window, *, bucket: str = "day",
             skill_name: str | None = None) -> dict:
    _check_bucket(bucket)
    params = {**window.params(), "skill_name": skill_name}
    filters = " AND skill_name = :skill_name" if skill_name is not None else ""
    b = bucket_expr("day", bucket)
    series_sql = f"""
        SELECT {b} AS bucket, skill_name,
               sum(success) AS success, sum(failure) AS failure,
               sum(user_corrected) AS user_corrected, sum(total) AS total,
               sum(user_corrected)::numeric / NULLIF(sum(total), 0) AS correction_rate
          FROM analytics.outcomes_daily
         WHERE day BETWEEN :start AND :end {filters}
         GROUP BY 1, 2 ORDER BY 1, total DESC, 2"""
    errors_sql = f"""
        SELECT skill_name, error_code, sum(errors) AS errors
          FROM analytics.error_codes_daily
         WHERE day BETWEEN :start AND :end {filters}
         GROUP BY 1, 2 ORDER BY errors DESC, 1, 2 LIMIT 50"""
    # Tool call success / failure counts (Phase 4 follow-up). Tools are not
    # attributed to skills, so the skill filter does not apply here.
    tools_sql = """
        SELECT coalesce(tool_name, 'unknown') AS tool_name, mcp_server,
               count(*) AS calls,
               count(*) FILTER (WHERE success IS TRUE)  AS success,
               count(*) FILTER (WHERE success IS FALSE) AS failure,
               count(*) FILTER (WHERE is_interrupt IS TRUE) AS interrupted,
               count(*) FILTER (WHERE success IS NULL)  AS unknown,
               count(*) FILTER (WHERE success IS FALSE)::numeric
                   / NULLIF(count(*) FILTER (WHERE success IS NOT NULL), 0) AS failure_rate
          FROM tool_calls
         WHERE coalesce(started_at, received_at) >= :start_ts AND coalesce(started_at, received_at) < :end_ts
         GROUP BY 1, 2 ORDER BY failure DESC, calls DESC, 1 LIMIT 100"""
    with engine.connect() as conn:
        series = rows_to_dicts(conn.execute(text(series_sql), params))
        errors = rows_to_dicts(conn.execute(text(errors_sql), params))
        tools = rows_to_dicts(conn.execute(text(tools_sql), params))
    total = _sum(series, "total")
    totals = {
        "success": _sum(series, "success"),
        "failure": _sum(series, "failure"),
        "user_corrected": _sum(series, "user_corrected"),
        "total": total,
        "correction_rate": (_sum(series, "user_corrected") / total) if total else None,
        "success_rate": (_sum(series, "success") / total) if total else None,
        "errors": _sum(errors, "errors"),
        "tool_calls": _sum(tools, "calls"),
        "tool_failures": _sum(tools, "failure"),
    }
    return {"series": series, "error_codes": errors, "tools": tools, "totals": totals}


# --- latency ----------------------------------------------------------------------------


def latency(engine: Engine, window: Window, *, bucket: str = "day",
            kind: str | None = None, name: str | None = None) -> dict:
    _check_bucket(bucket)
    params = {**window.params(), "kind": kind, "name": name}
    filters = ""
    if kind is not None:
        filters += " AND kind = :kind"
    if name is not None:
        filters += " AND name = :name"
    if bucket == "day":
        series_sql = f"""
            SELECT day AS bucket, kind, name, samples, p50, p95, p99
              FROM analytics.latency_daily
             WHERE day BETWEEN :start AND :end {filters}
             ORDER BY bucket, kind, samples DESC, name"""
    else:
        series_sql = f"""
            SELECT date_trunc('week', day)::date AS bucket, kind, name,
                   sum(samples) AS samples,
                   sum(p50 * samples) / sum(samples) AS p50,
                   sum(p95 * samples) / sum(samples) AS p95,
                   sum(p99 * samples) / sum(samples) AS p99
              FROM analytics.latency_daily
             WHERE day BETWEEN :start AND :end {filters}
             GROUP BY 1, 2, 3 ORDER BY 1, 2, samples DESC, 3"""
    totals_sql = f"""
        SELECT kind, sum(samples) AS samples,
               sum(p50 * samples) / sum(samples) AS p50,
               sum(p95 * samples) / sum(samples) AS p95,
               sum(p99 * samples) / sum(samples) AS p99
          FROM analytics.latency_daily
         WHERE day BETWEEN :start AND :end {filters}
         GROUP BY kind ORDER BY kind"""
    with engine.connect() as conn:
        series = rows_to_dicts(conn.execute(text(series_sql), params))
        by_kind = rows_to_dicts(conn.execute(text(totals_sql), params))
    totals = {"samples": _sum(by_kind, "samples")}
    for row in by_kind:
        for pct in ("p50", "p95", "p99"):
            totals[f"{row['kind']}_{pct}"] = row[pct]
    return {"series": series, "by_kind": by_kind, "totals": totals,
            "approximate": bucket != "day"}


# --- cost -----------------------------------------------------------------------------------

COST_GROUPS = {"none": None, "model": "model", "skill": "skill_name", "agent": "agent_name"}


def cost(engine: Engine, window: Window, *, bucket: str = "day", group_by: str = "none",
         model: str | None = None, skill_name: str | None = None,
         agent_name: str | None = None) -> dict:
    _check_bucket(bucket)
    if group_by not in COST_GROUPS:
        raise ValueError(f"group_by must be one of {sorted(COST_GROUPS)}")
    dim = COST_GROUPS[group_by]
    params = {**window.params(), "model": model, "skill_name": skill_name, "agent_name": agent_name}
    filters = ""
    for column, value in (("model", model), ("skill_name", skill_name), ("agent_name", agent_name)):
        if value is not None:
            filters += f" AND {column} = :{column}"
    b = bucket_expr("day", bucket)
    group_col = f"{dim} AS \"group\"," if dim else "NULL::text AS \"group\","
    group_by_sql = "GROUP BY 1, 2" if dim else "GROUP BY 1"
    series_sql = f"""
        SELECT {b} AS bucket, {group_col}
               sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
               sum(cache_read) AS cache_read, sum(cache_create) AS cache_create,
               sum(cost_usd) AS cost_usd, sum(api_requests) AS api_requests,
               sum(sessions) AS session_rows
          FROM analytics.cost_daily
         WHERE day BETWEEN :start AND :end {filters}
         {group_by_sql} ORDER BY 1, cost_usd DESC"""
    sessions_sql = """
        SELECT count(*) AS sessions, coalesce(avg(cost_usd), 0) AS mean_session_cost_usd
          FROM analytics.cost_per_session
         WHERE first_at >= :start_ts AND first_at < :end_ts"""
    top_sessions_sql = """
        SELECT session_id, first_at, last_at, cost_usd, api_requests,
               input_tokens, output_tokens, cache_read, cache_create
          FROM analytics.cost_per_session
         WHERE first_at >= :start_ts AND first_at < :end_ts
         ORDER BY cost_usd DESC LIMIT 10"""
    with engine.connect() as conn:
        series = rows_to_dicts(conn.execute(text(series_sql), params))
        sessions = rows_to_dicts(conn.execute(text(sessions_sql), params))[0]
        top_sessions = rows_to_dicts(conn.execute(text(top_sessions_sql), params))
    totals = {
        "input_tokens": _sum(series, "input_tokens"),
        "output_tokens": _sum(series, "output_tokens"),
        "cache_read": _sum(series, "cache_read"),
        "cache_create": _sum(series, "cache_create"),
        "cost_usd": _sum(series, "cost_usd"),
        "api_requests": _sum(series, "api_requests"),
        "sessions": sessions["sessions"],
        "mean_session_cost_usd": sessions["mean_session_cost_usd"],
    }
    return {"series": series, "top_sessions": top_sessions, "totals": totals, "group_by": group_by}


# --- context bloat --------------------------------------------------------------------------


def context_bloat(engine: Engine, window: Window, *, bucket: str = "day",
                  project_key: str | None = None) -> dict:
    _check_bucket(bucket)
    params = {**window.params(), "project_key": project_key}
    filters = " AND project_key = :project_key" if project_key is not None else ""
    if bucket == "day":
        series_sql = f"""
            SELECT day AS bucket, project_key, sessions, turns, always_on_tokens,
                   loaded_artifacts, compactions, mean_pre_tokens, bloat_index
              FROM analytics.context_bloat_daily
             WHERE day BETWEEN :start AND :end {filters}
             ORDER BY bucket, project_key"""
    else:
        series_sql = f"""
            SELECT date_trunc('week', day)::date AS bucket, project_key,
                   sum(sessions) AS sessions, sum(turns) AS turns,
                   sum(always_on_tokens * sessions) / sum(sessions) AS always_on_tokens,
                   sum(loaded_artifacts * sessions) / sum(sessions) AS loaded_artifacts,
                   sum(compactions) AS compactions,
                   sum(mean_pre_tokens * sessions) FILTER (WHERE mean_pre_tokens IS NOT NULL)
                     / NULLIF(sum(sessions) FILTER (WHERE mean_pre_tokens IS NOT NULL), 0) AS mean_pre_tokens,
                   sum(bloat_index * sessions) / sum(sessions) AS bloat_index
              FROM analytics.context_bloat_daily
             WHERE day BETWEEN :start AND :end {filters}
             GROUP BY 1, 2 ORDER BY 1, 2"""
    totals_sql = f"""
        SELECT coalesce(sum(sessions), 0) AS sessions, coalesce(sum(turns), 0) AS turns,
               sum(always_on_tokens * sessions) / NULLIF(sum(sessions), 0) AS always_on_tokens,
               sum(loaded_artifacts * sessions) / NULLIF(sum(sessions), 0) AS loaded_artifacts,
               coalesce(sum(compactions), 0) AS compactions,
               sum(mean_pre_tokens * sessions) FILTER (WHERE mean_pre_tokens IS NOT NULL)
                 / NULLIF(sum(sessions) FILTER (WHERE mean_pre_tokens IS NOT NULL), 0) AS mean_pre_tokens,
               sum(bloat_index * sessions) / NULLIF(sum(sessions), 0) AS bloat_index,
               count(DISTINCT project_key) AS projects
          FROM analytics.context_bloat_daily
         WHERE day BETWEEN :start AND :end {filters}"""
    with engine.connect() as conn:
        series = rows_to_dicts(conn.execute(text(series_sql), params))
        totals = rows_to_dicts(conn.execute(text(totals_sql), params))[0]
    return {"series": series, "totals": totals, "approximate": bucket != "day",
            "formula": BLOAT_FORMULA}


# --- sentiment (scheduler-written table) -----------------------------------------------------


def sentiment(engine: Engine, window: Window, *, bucket: str = "day",
              skill_name: str | None = None) -> dict:
    _check_bucket(bucket)
    params = {**window.params(), "skill_name": skill_name}
    filters = " AND skill_name = :skill_name" if skill_name is not None else ""
    b = bucket_expr("day", bucket)
    series_sql = f"""
        SELECT {b} AS bucket, skill_name,
               sum(event_count) AS event_count, sum(text_count) AS text_count,
               sum(text_count)::numeric / NULLIF(sum(event_count), 0) AS text_coverage,
               sum(text_sentiment * text_count) / NULLIF(sum(text_count), 0) AS text_sentiment,
               sum(behavioral_sentiment * event_count) / sum(event_count) AS behavioral_sentiment,
               sum(sentiment * event_count) / sum(event_count) AS sentiment,
               max(computed_at) AS computed_at
          FROM analytics.sentiment_daily
         WHERE day BETWEEN :start AND :end {filters}
         GROUP BY 1, 2 ORDER BY 1, sentiment, 2"""
    totals_sql = f"""
        SELECT coalesce(sum(event_count), 0) AS event_count,
               sum(text_count)::numeric / NULLIF(sum(event_count), 0) AS text_coverage,
               sum(sentiment * event_count) / NULLIF(sum(event_count), 0) AS sentiment,
               sum(text_sentiment * text_count) / NULLIF(sum(text_count), 0) AS text_sentiment
          FROM analytics.sentiment_daily
         WHERE day BETWEEN :start AND :end {filters}"""
    with engine.connect() as conn:
        series = rows_to_dicts(conn.execute(text(series_sql), params))
        totals = rows_to_dicts(conn.execute(text(totals_sql), params))[0]
    return {"series": series, "totals": totals}


# --- correction themes (scheduler-written table) ---------------------------------------------


def themes(engine: Engine, window: Window, *, top: int = 20) -> dict:
    params = {**window.params(), "top": top}
    weekly_sql = """
        SELECT week, theme, count, example_event_ids
          FROM (SELECT week, theme, count, example_event_ids,
                       row_number() OVER (PARTITION BY week ORDER BY count DESC, theme) AS rn
                  FROM analytics.correction_themes
                 WHERE week BETWEEN date_trunc('week', CAST(:start AS date))::date AND :end) t
         WHERE rn <= :top
         ORDER BY week, count DESC, theme"""
    overall_sql = """
        SELECT theme, sum(count) AS count,
               (array_agg(ex ORDER BY ex))[1:5] AS example_event_ids
          FROM analytics.correction_themes ct
          LEFT JOIN LATERAL unnest(ct.example_event_ids) AS ex ON true
         WHERE week BETWEEN date_trunc('week', CAST(:start AS date))::date AND :end
         GROUP BY theme ORDER BY count DESC, theme LIMIT :top"""
    with engine.connect() as conn:
        weekly = rows_to_dicts(conn.execute(text(weekly_sql), params))
        overall = rows_to_dicts(conn.execute(text(overall_sql), params))
    for row in weekly + overall:
        row["example_event_ids"] = [str(e) for e in (row.get("example_event_ids") or []) if e]
    return {"series": weekly, "top": overall,
            "totals": {"themes": len({r["theme"] for r in weekly}), "weeks": len({r["week"] for r in weekly})}}


# --- session timeline -----------------------------------------------------------------------

_TURN_CHILDREN = {
    "tool_calls": (
        "SELECT tool_use_id, prompt_id, agent_id, tool_name, mcp_server, started_at, duration_ms, "
        "success, is_interrupt, input_bytes, output_bytes, error_class, decision, input_content, "
        "output_content FROM tool_calls WHERE session_id = :sid ORDER BY started_at NULLS LAST, tool_use_id"
    ),
    "outcomes": (
        "SELECT event_id, prompt_id, turn_id, artifact_id, client_ts, skill_name, skill_version, status, "
        "model, latency_ms, error_code, feedback, interaction FROM events WHERE session_id = :sid "
        "ORDER BY client_ts, event_id"
    ),
    "compactions": (
        "SELECT id, prompt_id, trigger, pre_tokens, post_tokens, at, custom_instructions_length, "
        "summary_length, summary_text FROM compactions WHERE session_id = :sid ORDER BY at, id"
    ),
    "skill_activations": (
        "SELECT id, prompt_id, artifact_id, skill_name, skill_source, invocation_trigger, at "
        "FROM skill_activations WHERE session_id = :sid ORDER BY at, id"
    ),
    "subagent_runs": (
        "SELECT agent_id, prompt_id, agent_type, started_at, stopped_at, total_tokens, total_tool_uses, "
        "last_message_length FROM subagent_runs WHERE session_id = :sid ORDER BY started_at NULLS LAST, agent_id"
    ),
    "model_switches": (
        "SELECT id, prompt_id, phase, from_model, to_model, context_tokens, at FROM model_switches "
        "WHERE session_id = :sid ORDER BY at, id"
    ),
    "errors": (
        "SELECT id, prompt_id, kind, model, error_code, status_code, error_class, message, duration_ms, at "
        "FROM turn_errors WHERE session_id = :sid ORDER BY at, id"
    ),
    "instructions_loaded": (
        "SELECT id, prompt_id, artifact_id, file_path, content_hash, memory_type, load_reason, "
        "trigger_file_path, parent_file_path, at FROM instructions_loaded WHERE session_id = :sid "
        "ORDER BY at, id"
    ),
    "usage": (
        "SELECT prompt_id, count(*) AS api_requests, coalesce(sum(cost_usd), 0) AS cost_usd, "
        "coalesce(sum((attrs->>'input_tokens')::numeric), 0) AS input_tokens, "
        "coalesce(sum((attrs->>'output_tokens')::numeric), 0) AS output_tokens, "
        "coalesce(sum((attrs->>'cache_read_tokens')::numeric), 0) AS cache_read, "
        "coalesce(sum((attrs->>'cache_creation_tokens')::numeric), 0) AS cache_create, "
        "min(at) AS first_at, max(at) AS last_at FROM usage_facts "
        "WHERE session_id = :sid AND kind = 'api_request' GROUP BY prompt_id"
    ),
}


def session_timeline(engine: Engine, session_id: str) -> dict | None:
    try:
        sid = str(uuid.UUID(session_id))
    except ValueError:
        return None
    with engine.connect() as conn:
        session = conn.execute(
            text(
                "SELECT session_id, started_at, ended_at, source, model_initial, agent_type, cwd_hash, "
                "project_key, permission_mode, effort, title_redacted, end_reason, context_tokens_at_start, "
                "client_version, auth_upn, received_at FROM sessions WHERE session_id = :sid"
            ),
            {"sid": sid},
        ).mappings().one_or_none()
        if session is None:
            return None
        turns = rows_to_dicts(
            conn.execute(
                text(
                    "SELECT turn_id, prompt_id, seq, submitted_at, stopped_at, prompt_length, prompt_text, "
                    "response_length, response_text, model, expansion_type, command_name, command_source "
                    "FROM turns WHERE session_id = :sid ORDER BY seq NULLS LAST, submitted_at NULLS LAST, turn_id"
                ),
                {"sid": sid},
            )
        )
        children = {
            key: rows_to_dicts(conn.execute(text(sql), {"sid": sid})) for key, sql in _TURN_CHILDREN.items()
        }
        transcript = conn.execute(
            text(
                "SELECT count(*) AS lines, count(*) FILTER (WHERE content IS NOT NULL) AS lines_with_content "
                "FROM transcript_lines WHERE session_id = :sid"
            ),
            {"sid": sid},
        ).mappings().one()

    by_prompt = {t["prompt_id"]: t for t in turns}
    session_level: dict[str, list] = {}
    for key, rows in children.items():
        if key == "usage":
            continue
        for row in rows:
            turn = by_prompt.get(row.get("prompt_id"))
            if turn is not None:
                turn.setdefault(key, []).append(row)
            else:
                session_level.setdefault(key, []).append(row)
    for turn in turns:
        for key in _TURN_CHILDREN:
            if key != "usage":
                turn.setdefault(key, [])
        turn["usage"] = None
    for usage in children["usage"]:
        turn = by_prompt.get(usage["prompt_id"])
        if turn is not None:
            turn["usage"] = {k: v for k, v in usage.items() if k != "prompt_id"}
        else:
            session_level.setdefault("usage", []).append(usage)

    session_dict = {k: plain(v) for k, v in dict(session).items()}
    totals = {
        "turns": len(turns),
        "tool_calls": len(children["tool_calls"]),
        "outcomes": len(children["outcomes"]),
        "compactions": len(children["compactions"]),
        "subagent_runs": len(children["subagent_runs"]),
        "errors": len(children["errors"]),
        "cost_usd": sum((u["cost_usd"] or 0) for u in children["usage"]),
        "api_requests": sum((u["api_requests"] or 0) for u in children["usage"]),
        "transcript_lines": transcript["lines"],
        "transcript_lines_with_content": transcript["lines_with_content"],
    }
    return {"session": session_dict, "turns": turns, "unattributed": session_level, "totals": totals}


# --- artifact registry -----------------------------------------------------------------------

ARTIFACT_KINDS = ("skill", "hook", "agent", "command", "instructions", "rule", "plugin")

# Per-artifact attribution for the window (design D10 / artifact-registry spec):
# invocations = distinct (session, prompt) across skill_activations and events
# matched by artifact_id or, for skills and commands, by name; cost from
# usage_facts by skill.name (skills/commands) or agent.name (agents);
# sessions_loaded from instructions_loaded (instructions/rules only - skill
# descriptions have no per-session load signal yet).
_ARTIFACT_METRICS = """
    LEFT JOIN LATERAL (
        SELECT count(*) AS invocations FROM (
            SELECT DISTINCT sa.session_id, coalesce(sa.prompt_id, sa.id::text) AS k
              FROM skill_activations sa
             WHERE sa.at >= :start_ts AND sa.at < :end_ts
               AND (sa.artifact_id = a.artifact_id
                    OR (sa.artifact_id IS NULL AND a.kind IN ('skill', 'command') AND sa.skill_name = a.name))
            UNION
            SELECT DISTINCT e.session_id, coalesce(e.prompt_id, e.event_id::text)
              FROM events e
             WHERE e.client_ts >= :start_ts AND e.client_ts < :end_ts
               AND (e.artifact_id = a.artifact_id
                    OR (e.artifact_id IS NULL AND a.kind IN ('skill', 'command') AND e.skill_name = a.name))
        ) u
    ) inv ON true
    LEFT JOIN LATERAL (
        SELECT count(*) FILTER (WHERE e.status = 'success')        AS success,
               count(*) FILTER (WHERE e.status = 'failure')        AS failure,
               count(*) FILTER (WHERE e.status = 'user_corrected') AS user_corrected,
               count(*)                                            AS outcomes
          FROM events e
         WHERE e.client_ts >= :start_ts AND e.client_ts < :end_ts
           AND (e.artifact_id = a.artifact_id
                OR (e.artifact_id IS NULL AND a.kind IN ('skill', 'command') AND e.skill_name = a.name))
    ) oc ON true
    LEFT JOIN LATERAL (
        SELECT coalesce(sum(u.cost_usd), 0) AS cost_usd,
               coalesce(sum((u.attrs->>'input_tokens')::numeric + (u.attrs->>'output_tokens')::numeric
                            + coalesce((u.attrs->>'cache_read_tokens')::numeric, 0)
                            + coalesce((u.attrs->>'cache_creation_tokens')::numeric, 0)), 0) AS tokens,
               count(*) AS api_requests
          FROM usage_facts u
         WHERE u.kind = 'api_request' AND u.at >= :start_ts AND u.at < :end_ts
           AND ((a.kind IN ('skill', 'command') AND u.skill_name = a.name)
                OR (a.kind = 'agent' AND u.agent_name = a.name))
    ) us ON true
    LEFT JOIN LATERAL (
        SELECT count(DISTINCT il.session_id) AS sessions_loaded
          FROM instructions_loaded il
         WHERE il.artifact_id = a.artifact_id AND il.at >= :start_ts AND il.at < :end_ts
    ) ld ON true
    LEFT JOIN LATERAL (
        SELECT av.version_id, av.content_hash, av.estimated_tokens_always_on, av.estimated_tokens_body,
               av.seen_from, av.seen_until, av.commit_sha, (av.content_text IS NOT NULL) AS synced,
               (SELECT count(*) FROM artifact_versions v2 WHERE v2.artifact_id = a.artifact_id) AS version_count
          FROM artifact_versions av
         WHERE av.artifact_id = a.artifact_id
         ORDER BY av.seen_until IS NOT NULL, av.seen_from DESC
         LIMIT 1
    ) lv ON true
"""

_ARTIFACT_SELECT = """
    SELECT a.artifact_id, a.kind, a.name, a.owner_repo, a.path, a.source, a.plugin_id_hash,
           a.first_seen, a.last_seen,
           inv.invocations, oc.success, oc.failure, oc.user_corrected, oc.outcomes,
           CASE WHEN oc.outcomes > 0 THEN oc.user_corrected::numeric / oc.outcomes END AS correction_rate,
           us.cost_usd, us.tokens, us.api_requests,
           CASE WHEN inv.invocations > 0 THEN us.cost_usd / inv.invocations END AS cost_per_invocation,
           CASE WHEN inv.invocations > 0 THEN us.tokens / inv.invocations END AS tokens_per_invocation,
           ld.sessions_loaded,
           (ld.sessions_loaded > 0 AND inv.invocations = 0) AS never_invoked,
           coalesce(lv.estimated_tokens_always_on, 0) * ld.sessions_loaded AS always_on_tokens_total,
           lv.version_id AS latest_version_id, lv.content_hash AS latest_content_hash,
           lv.estimated_tokens_always_on, lv.estimated_tokens_body, lv.seen_from AS latest_seen_from,
           lv.seen_until AS latest_seen_until, lv.commit_sha AS latest_commit_sha,
           coalesce(lv.synced, false) AS synced, coalesce(lv.version_count, 0) AS version_count
      FROM artifacts a
"""


def artifacts(engine: Engine, window: Window, *, kind: str | None = None, q: str | None = None,
              never_invoked: bool | None = None, limit: int = 100, offset: int = 0,
              order: str = "invocations") -> dict:
    if kind is not None and kind not in ARTIFACT_KINDS:
        raise ValueError(f"kind must be one of {ARTIFACT_KINDS}")
    # Applied to the outer query over the metrics subquery, hence unqualified names.
    orders = {
        "invocations": "invocations DESC, name",
        "name": "name, kind",
        "cost": "cost_usd DESC, name",
        "always_on": "always_on_tokens_total DESC, name",
        "last_seen": "last_seen DESC, name",
    }
    if order not in orders:
        raise ValueError(f"order must be one of {sorted(orders)}")
    params = {**window.params(), "kind": kind, "q": f"%{q.strip()}%" if q else None,
              "limit": limit, "offset": offset}
    where = []
    if kind is not None:
        where.append("a.kind = :kind")
    if q:
        where.append("(a.name ILIKE :q OR a.path ILIKE :q OR a.owner_repo ILIKE :q)")
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    having = ""
    if never_invoked is True:
        having = "WHERE never_invoked"
    elif never_invoked is False:
        having = "WHERE NOT never_invoked"
    sql = f"""
        SELECT * FROM ({_ARTIFACT_SELECT} {_ARTIFACT_METRICS} {where_sql}) x
        {having}
        ORDER BY {orders[order]}
        LIMIT :limit OFFSET :offset"""
    count_sql = f"""
        SELECT count(*) FROM ({_ARTIFACT_SELECT} {_ARTIFACT_METRICS} {where_sql}) x {having}"""
    with engine.connect() as conn:
        rows = rows_to_dicts(conn.execute(text(sql), params))
        total = conn.execute(text(count_sql), params).scalar_one()
    return {"artifacts": rows, "total": total, "limit": limit, "offset": offset}


def artifact_detail(engine: Engine, artifact_id: str, window: Window) -> dict | None:
    try:
        aid = str(uuid.UUID(artifact_id))
    except ValueError:
        return None
    params = {**window.params(), "aid": aid}
    with engine.connect() as conn:
        rows = rows_to_dicts(
            conn.execute(text(f"{_ARTIFACT_SELECT} {_ARTIFACT_METRICS} WHERE a.artifact_id = :aid"), params)
        )
        if not rows:
            return None
        artifact = rows[0]
        versions = rows_to_dicts(
            conn.execute(
                text(
                    "SELECT version_id, content_hash, frontmatter, estimated_tokens_always_on, "
                    "estimated_tokens_body, seen_from, seen_until, commit_sha, "
                    "(content_text IS NOT NULL) AS synced, length(content_text) AS content_length "
                    "FROM artifact_versions WHERE artifact_id = :aid ORDER BY seen_from DESC"
                ),
                params,
            )
        )
        activity_kind = "agent" if artifact["kind"] == "agent" else "skill"
        series = rows_to_dicts(
            conn.execute(
                text(
                    "SELECT day AS bucket, count(*) AS invocations, count(DISTINCT session_id) AS sessions, "
                    "count(DISTINCT auth_oid) AS users FROM analytics.activity "
                    "WHERE skill_or_agent = :name AND kind = :k AND day BETWEEN :start AND :end "
                    "GROUP BY day ORDER BY day"
                ),
                {**params, "name": artifact["name"], "k": activity_kind},
            )
        )
    return {"artifact": artifact, "versions": versions, "series": series}



# --- sessions list (Phase 4 follow-up) ------------------------------------------------------

SESSION_ORDERS = {
    "started": "started_at DESC NULLS LAST, session_id",
    "cost": "cost_usd DESC NULLS LAST, session_id",
    "turns": "turns DESC, session_id",
    "corrections": "user_corrected DESC, session_id",
    "tokens": "tokens DESC NULLS LAST, session_id",
}


def sessions(engine: Engine, window: Window, *, project_key: str | None = None, upn: str | None = None,
             q: str | None = None, order: str = "started", limit: int = 50, offset: int = 0) -> dict:
    """Paginated session list for ``/engineering/sessions``: one row per session
    whose start (or first receipt) falls in the window, with turn / tool /
    compaction counts, cost and skill outcomes. ``q`` matches a session-id
    prefix or the project key."""
    if order not in SESSION_ORDERS:
        raise ValueError(f"order must be one of {sorted(SESSION_ORDERS)}")
    params = {**window.params(), "limit": limit, "offset": offset}
    where = ["coalesce(s.started_at, s.received_at) >= :start_ts",
             "coalesce(s.started_at, s.received_at) < :end_ts"]
    if project_key:
        where.append("s.project_key = :project_key")
        params["project_key"] = project_key
    if upn:
        where.append("s.auth_upn = :upn")
        params["upn"] = upn
    if q and q.strip():
        where.append("(s.session_id::text LIKE :q_prefix OR s.project_key ILIKE :q_like)")
        params["q_prefix"] = q.strip().lower() + "%"
        params["q_like"] = f"%{q.strip()}%"
    where_sql = " AND ".join(where)
    base = f"""
        SELECT s.session_id, s.started_at, s.ended_at, s.source, s.model_initial, s.agent_type,
               s.project_key, s.end_reason, s.title_redacted, s.auth_upn,
               (SELECT count(*) FROM turns t WHERE t.session_id = s.session_id) AS turns,
               (SELECT count(*) FROM tool_calls tc WHERE tc.session_id = s.session_id) AS tool_calls,
               (SELECT count(*) FROM tool_calls tc WHERE tc.session_id = s.session_id
                  AND tc.success IS FALSE) AS tool_failures,
               (SELECT count(*) FROM compactions c WHERE c.session_id = s.session_id) AS compactions,
               cps.cost_usd, cps.api_requests,
               cps.input_tokens + cps.output_tokens + cps.cache_read + cps.cache_create AS tokens,
               coalesce(ev.outcomes, 0) AS outcomes, coalesce(ev.user_corrected, 0) AS user_corrected,
               coalesce(ev.failures, 0) AS failures
          FROM sessions s
          LEFT JOIN analytics.cost_per_session cps ON cps.session_id = s.session_id
          LEFT JOIN LATERAL (
              SELECT count(*) AS outcomes,
                     count(*) FILTER (WHERE e.status = 'user_corrected') AS user_corrected,
                     count(*) FILTER (WHERE e.status = 'failure') AS failures
                FROM events e WHERE e.session_id = s.session_id
          ) ev ON true
         WHERE {where_sql}"""
    with engine.connect() as conn:
        total = conn.execute(text(f"SELECT count(*) FROM sessions s WHERE {where_sql}"), params).scalar_one()
        rows = rows_to_dicts(conn.execute(
            text(f"SELECT * FROM ({base}) x ORDER BY {SESSION_ORDERS[order]} LIMIT :limit OFFSET :offset"),
            params,
        ))
        totals = conn.execute(
            text(f"SELECT count(*) AS sessions, coalesce(sum(cost_usd), 0) AS cost_usd, "
                 f"avg(cost_usd) AS mean_session_cost_usd, coalesce(sum(turns), 0) AS turns, "
                 f"coalesce(sum(outcomes), 0) AS outcomes, coalesce(sum(user_corrected), 0) AS user_corrected "
                 f"FROM ({base}) x"),
            params,
        ).mappings().one()
    for r in rows:
        r["session_id"] = str(r["session_id"])
    return {"sessions": rows, "total": total, "limit": limit, "offset": offset,
            "order": order, "totals": {k: plain(v) for k, v in dict(totals).items()}}
