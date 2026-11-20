"""Outcome measurement for applied proposals (design D14, task 5.5).

Once ``OUTCOME_WINDOW_DAYS`` (default 14) have passed since a proposal was
applied (merged), the scheduler stores one ``improvement_outcomes`` row with
equal-length windows ``before = [applied_at - W, applied_at)`` and
``after = [applied_at, applied_at + W)`` (half-open ``tstzrange``s) and the
same metric set the evidence pack used (``evidence.artifact_metrics``).

Verdict rules (recorded with the outcome so they can be recalibrated):

* ``insufficient_data`` - either window's sample (``max(invocations,
  sessions_loaded)``) is below ``OUTCOME_MIN_SAMPLE`` (default 20); no
  improvement is claimed.
* Each lower-is-better metric is compared: ``correction_rate`` and
  ``failure_rate`` by absolute points (``OUTCOME_RATE_POINTS``, 0.02);
  ``always_on_tokens`` and ``cost_per_invocation`` relatively
  (``OUTCOME_RELATIVE_THRESHOLD``, 10 %). A drop in ``invocations`` larger
  than the relative threshold counts as a regression ("invocations held").
* ``improved`` - at least one metric improved and none regressed;
  ``worse`` - at least one regressed and none improved; otherwise ``neutral``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..auth import write_audit
from ..settings import Settings
from .evidence import artifact_metrics

log = logging.getLogger("ouroboros.reporting.outcomes")

RATE_METRICS = ("correction_rate", "failure_rate")
RELATIVE_METRICS = ("always_on_tokens", "cost_per_invocation")


def _sample(m: dict) -> int:
    return max(int(m.get("invocations") or 0), int(m.get("sessions_loaded") or 0))


def verdict(before: dict, after: dict, settings: Settings) -> tuple[str, dict]:
    detail: dict = {"improved": [], "regressed": [], "min_sample": settings.outcome_min_sample,
                    "sample_before": _sample(before), "sample_after": _sample(after)}
    if detail["sample_before"] < settings.outcome_min_sample or detail["sample_after"] < settings.outcome_min_sample:
        return "insufficient_data", detail
    for key in RATE_METRICS:
        b, a = before.get(key), after.get(key)
        if b is None or a is None:
            continue
        if a <= b - settings.outcome_rate_points:
            detail["improved"].append(key)
        elif a >= b + settings.outcome_rate_points:
            detail["regressed"].append(key)
    t = settings.outcome_relative_threshold
    for key in RELATIVE_METRICS:
        b, a = before.get(key), after.get(key)
        if b is None or a is None:
            continue
        if b == 0:
            if a > 0:
                detail["regressed"].append(key)
            continue
        change = (a - b) / b
        if change <= -t:
            detail["improved"].append(key)
        elif change >= t:
            detail["regressed"].append(key)
    b_inv, a_inv = int(before.get("invocations") or 0), int(after.get("invocations") or 0)
    if b_inv > 0 and (a_inv - b_inv) / b_inv <= -t:
        detail["regressed"].append("invocations")
    if detail["improved"] and not detail["regressed"]:
        return "improved", detail
    if detail["regressed"] and not detail["improved"]:
        return "worse", detail
    return "neutral", detail


def due(engine: Engine, now: datetime, window_days: int) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT p.proposal_id, p.target_artifact_id, p.applied_at FROM improvement_proposals p "
                "LEFT JOIN improvement_outcomes o USING (proposal_id) "
                "WHERE p.status = 'applied' AND p.applied_at IS NOT NULL AND o.proposal_id IS NULL "
                "AND p.applied_at + make_interval(days => :days) <= :now ORDER BY p.applied_at"
            ),
            {"days": window_days, "now": now},
        ).mappings().all()
    return [dict(r) for r in rows]


def measure(engine: Engine, settings: Settings, *, now: datetime | None = None) -> dict:
    """Fill every due outcome; returns ``{"measured": n, "verdicts": {...}}``."""
    now = now or datetime.now(timezone.utc)
    w = timedelta(days=settings.outcome_window_days)
    counts: dict[str, int] = {}
    for row in due(engine, now, settings.outcome_window_days):
        applied = row["applied_at"]
        aid = str(row["target_artifact_id"])
        before = artifact_metrics(engine, aid, applied - w, applied)
        after = artifact_metrics(engine, aid, applied, applied + w)
        v, detail = verdict(before, after, settings)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO improvement_outcomes (proposal_id, before_window, after_window, "
                    "metrics_before, metrics_after, verdict) VALUES (:pid, tstzrange(:b0, :b1, '[)'), "
                    "tstzrange(:a0, :a1, '[)'), CAST(:mb AS JSONB), CAST(:ma AS JSONB), :v) "
                    "ON CONFLICT (proposal_id) DO NOTHING"
                ),
                {"pid": row["proposal_id"], "b0": applied - w, "b1": applied, "a0": applied,
                 "a1": applied + w, "mb": json.dumps({**before, "_verdict": detail}, default=str),
                 "ma": json.dumps(after, default=str), "v": v},
            )
            # Link the artifact version that appeared after the merge, when synced.
            conn.execute(
                text(
                    "UPDATE improvement_proposals p SET applied_version_id = v.version_id FROM ("
                    " SELECT version_id FROM artifact_versions WHERE artifact_id = :aid AND seen_from >= :at"
                    " ORDER BY seen_from LIMIT 1) v WHERE p.proposal_id = :pid AND p.applied_version_id IS NULL"
                ),
                {"aid": aid, "at": applied, "pid": row["proposal_id"]},
            )
            write_audit(conn, None, "proposal.outcome", target_kind="proposal", target_id=str(row["proposal_id"]),
                        details={"verdict": v, **{k: detail[k] for k in ("improved", "regressed")}})
        counts[v] = counts.get(v, 0) + 1
        log.info("outcome for proposal %s: %s", row["proposal_id"], v)
    return {"measured": sum(counts.values()), "verdicts": counts}
