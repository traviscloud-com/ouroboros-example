"""Evidence packs for the analyst (design D14, task 5.1).

One pack per target artifact: the latest synced version text and token
estimates, the window's aggregates (the same attribution the artifact registry
uses - invocations, outcomes, correction/failure rate, cost per invocation,
always-on tokens, never-invoked status, compactions in sessions that loaded
it), and up to ``ANALYST_MAX_EXAMPLES`` correction/failure examples.

Examples are passed through the D12 redaction layer unless
``ANALYST_SEES_SENSITIVE=on``: by default the prompt contains aggregates,
artifact text (internal class, our own source) and ``{"redacted": true,
"length": n}`` markers - never verbatim user text.

``artifact_metrics`` is shared with outcome measurement so a proposal's
before/after snapshots use exactly the metric set its evidence did.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..analytics import _ARTIFACT_METRICS, _ARTIFACT_SELECT
from ..queries import rows_to_dicts
from ..redaction import redact

# Kinds the loop may propose edits for (plugins are packaging, not text).
TARGET_KINDS = ("skill", "command", "agent", "hook", "rule", "instructions")
OPEN_STATES = ("proposed", "approved", "pr_opened")

METRIC_KEYS = (
    "invocations", "outcomes", "success", "failure", "user_corrected", "correction_rate",
    "failure_rate", "cost_usd", "cost_per_invocation", "sessions_loaded", "never_invoked",
    "always_on_tokens", "always_on_tokens_total", "compactions_when_loaded",
)


def _ts_params(start: datetime, end: datetime, artifact_id: str | None = None) -> dict:
    params = {"start_ts": start, "end_ts": end, "start": start.date(), "end": end.date()}
    if artifact_id is not None:
        params["aid"] = artifact_id
    return params


def artifact_metrics(engine: Engine, artifact_id: str, start: datetime, end: datetime) -> dict:
    """The evidence / outcome metric set for one artifact over ``[start, end)``."""
    params = _ts_params(start, end, artifact_id)
    with engine.connect() as conn:
        rows = rows_to_dicts(
            conn.execute(text(f"{_ARTIFACT_SELECT} {_ARTIFACT_METRICS} WHERE a.artifact_id = :aid"), params)
        )
        if not rows:
            return {}
        row = rows[0]
        # Always-on estimate of the version that was current at the end of the window.
        always_on = conn.execute(
            text(
                "SELECT estimated_tokens_always_on FROM artifact_versions "
                "WHERE artifact_id = :aid AND seen_from < :end_ts ORDER BY seen_from DESC LIMIT 1"
            ),
            params,
        ).scalar_one_or_none()
        compactions = conn.execute(
            text(
                "SELECT count(*) FROM compactions c WHERE c.at >= :start_ts AND c.at < :end_ts "
                "AND c.session_id IN (SELECT il.session_id FROM instructions_loaded il "
                "WHERE il.artifact_id = :aid AND il.at >= :start_ts AND il.at < :end_ts)"
            ),
            params,
        ).scalar_one()
    outcomes = row.get("outcomes") or 0
    sessions_loaded = row.get("sessions_loaded") or 0
    metrics = {
        "invocations": row.get("invocations") or 0,
        "outcomes": outcomes,
        "success": row.get("success") or 0,
        "failure": row.get("failure") or 0,
        "user_corrected": row.get("user_corrected") or 0,
        "correction_rate": row.get("correction_rate"),
        "failure_rate": (row.get("failure") or 0) / outcomes if outcomes else None,
        "cost_usd": row.get("cost_usd") or 0,
        "cost_per_invocation": row.get("cost_per_invocation"),
        "sessions_loaded": sessions_loaded,
        "never_invoked": bool(row.get("never_invoked")),
        "always_on_tokens": always_on,
        "always_on_tokens_total": (always_on or 0) * sessions_loaded,
        "compactions_when_loaded": compactions,
    }
    return metrics


def _latest_version(conn, artifact_id: str) -> dict | None:
    rows = rows_to_dicts(
        conn.execute(
            text(
                "SELECT version_id, content_hash, content_text, frontmatter, estimated_tokens_always_on, "
                "estimated_tokens_body, seen_from, commit_sha FROM artifact_versions "
                "WHERE artifact_id = :aid ORDER BY seen_until IS NOT NULL, seen_from DESC LIMIT 1"
            ),
            {"aid": artifact_id},
        )
    )
    return rows[0] if rows else None


def candidates(engine: Engine, *, end: datetime, window_days: int, limit: int) -> list[dict]:
    """Artifacts worth analysing, best first.

    Eligible: a target kind, a known ``owner_repo`` and ``path`` (a PR needs
    both), synced content for the latest version, and no open proposal.
    Score: always-on tokens actually paid in the window plus weighted
    corrections and failures, with a bonus for loaded-but-never-invoked.
    """
    start = end - timedelta(days=window_days)
    params = {**_ts_params(start, end), "kinds": list(TARGET_KINDS), "open": list(OPEN_STATES),
              "limit": limit}
    sql = f"""
        SELECT * FROM ({_ARTIFACT_SELECT} {_ARTIFACT_METRICS}
                WHERE a.kind = ANY(:kinds) AND a.owner_repo <> '' AND a.path <> ''
                  AND NOT EXISTS (SELECT 1 FROM improvement_proposals p
                                   WHERE p.target_artifact_id = a.artifact_id AND p.status = ANY(:open))) x
         WHERE synced
         ORDER BY (coalesce(always_on_tokens_total, 0)
                   + 1000 * coalesce(user_corrected, 0) + 500 * coalesce(failure, 0)
                   + CASE WHEN never_invoked THEN 5000 ELSE 0 END) DESC, name
         LIMIT :limit"""
    with engine.connect() as conn:
        rows = rows_to_dicts(conn.execute(text(sql), params))
    return [
        r for r in rows
        if (r.get("always_on_tokens_total") or 0) > 0 or (r.get("user_corrected") or 0) > 0
        or (r.get("failure") or 0) > 0 or r.get("never_invoked")
    ]


def examples(engine: Engine, artifact: dict, start: datetime, end: datetime, *, limit: int,
             sees_sensitive: bool) -> list[dict]:
    """Recent corrected / failed outcome events attributed to the artifact."""
    params = {"aid": artifact["artifact_id"], "name": artifact["name"], "kind": artifact["kind"],
              "start_ts": start, "end_ts": end, "limit": limit}
    with engine.connect() as conn:
        rows = rows_to_dicts(
            conn.execute(
                text(
                    "SELECT e.event_id, e.client_ts, e.status, e.error_code, e.latency_ms, e.feedback, "
                    "e.interaction FROM events e "
                    "WHERE e.client_ts >= :start_ts AND e.client_ts < :end_ts "
                    "AND e.status IN ('user_corrected', 'failure') "
                    "AND (e.artifact_id = :aid OR (e.artifact_id IS NULL AND :kind IN ('skill', 'command') "
                    "AND e.skill_name = :name)) "
                    "ORDER BY e.status = 'user_corrected' DESC, e.client_ts DESC LIMIT :limit"
                ),
                params,
            )
        )
    out = []
    for r in rows:
        item = {
            "event_id": str(r["event_id"]),
            "at": r["client_ts"].isoformat() if r["client_ts"] else None,
            "status": r["status"],
            "error_code": r["error_code"],
            "latency_ms": r["latency_ms"],
            "feedback": r["feedback"] or {},
            "interaction": r["interaction"] or {},
        }
        out.append(item if sees_sensitive else redact(item))
    return out


def build_pack(engine: Engine, artifact: dict, *, end: datetime | None = None, window_days: int = 30,
               max_examples: int = 10, sees_sensitive: bool = False) -> dict | None:
    """The evidence pack for one artifact row (from :func:`candidates`)."""
    end = end or datetime.now(timezone.utc)
    start = end - timedelta(days=window_days)
    aid = str(artifact["artifact_id"])
    with engine.connect() as conn:
        version = _latest_version(conn, aid)
    if not version or version.get("content_text") is None:
        return None
    metrics = artifact_metrics(engine, aid, start, end)
    return {
        "artifact": {
            "artifact_id": aid,
            "kind": artifact["kind"],
            "name": artifact["name"],
            "owner_repo": artifact["owner_repo"],
            "path": artifact["path"],
            "source": artifact.get("source"),
        },
        "version": {
            "version_id": str(version["version_id"]),
            "content_hash": version["content_hash"],
            "estimated_tokens_always_on": version["estimated_tokens_always_on"],
            "estimated_tokens_body": version["estimated_tokens_body"],
            "frontmatter": version["frontmatter"],
            "content_text": version["content_text"],
        },
        "window": {"from": start.isoformat(), "to": end.isoformat(), "days": window_days},
        "metrics": metrics,
        "examples": examples(engine, {**artifact, "artifact_id": aid}, start, end,
                             limit=max_examples, sees_sensitive=sees_sensitive),
        "examples_redacted": not sees_sensitive,
    }
