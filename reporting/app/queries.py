"""Read-only SQL for the legacy ``/v1/reports/*`` routes plus the window /
bucket helpers shared with the analytics queries. Filters are composed
dynamically so psycopg never sees untyped NULL parameters.

Event time (design D17): every filter and bucket uses ``client_ts``, never
``received_at``, so spooled uploads land on the day they happened. Free text
search uses ``websearch_to_tsquery`` over the generated tsvector columns
(0007 ``feedback_tsv``, 0010 ``interaction_tsv``) instead of ILIKE.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

_EVENT_COLUMNS = (
    "event_id, session_id, client_ts, received_at, skill_name, skill_version, "
    "status, model, latency_ms, error_code, auth_oid, auth_upn, feedback, interaction, "
    "prompt_id, turn_id, artifact_id"
)

VALID_BUCKETS = {"hour", "day", "week"}
ANALYTICS_BUCKETS = ("day", "week")

_FTS = (
    "(feedback_tsv @@ websearch_to_tsquery('english', :q) "
    "OR interaction_tsv @@ websearch_to_tsquery('english', :q))"
)


# --- shared helpers -----------------------------------------------------------------


def plain(value: Any) -> Any:
    """Make driver values JSON-friendly and arithmetic-friendly (Decimal -> float)."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def rows_to_dicts(result) -> list[dict]:
    return [{k: plain(v) for k, v in dict(r).items()} for r in result.mappings().all()]


@dataclass(frozen=True)
class Window:
    """Inclusive date window in UTC; ``previous`` is the equal-length window before it."""

    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> "Window":
        return Window(self.start - timedelta(days=self.days), self.start - timedelta(days=1))

    @property
    def start_ts(self) -> datetime:
        return datetime(self.start.year, self.start.month, self.start.day, tzinfo=timezone.utc)

    @property
    def end_ts_exclusive(self) -> datetime:
        nxt = self.end + timedelta(days=1)
        return datetime(nxt.year, nxt.month, nxt.day, tzinfo=timezone.utc)

    def params(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts_exclusive,
        }

    def as_dict(self) -> dict:
        return {"from": self.start.isoformat(), "to": self.end.isoformat(), "days": self.days}


def to_utc_date(value: date | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).date()
    return value


def make_window(ts_from: date | datetime | None, ts_to: date | datetime | None,
                *, default_days: int = 30) -> Window:
    end = to_utc_date(ts_to) or datetime.now(timezone.utc).date()
    start = to_utc_date(ts_from) or end - timedelta(days=default_days - 1)
    if start > end:
        raise ValueError("'from' must not be after 'to'")
    return Window(start, end)


def bucket_expr(column: str, bucket: str) -> str:
    """SQL expression bucketing a DATE column; ``bucket`` is validated by the caller."""
    if bucket == "day":
        return column
    if bucket == "week":
        return f"date_trunc('week', {column})::date"
    raise ValueError(f"bucket must be one of {ANALYTICS_BUCKETS}")


def deltas(current: dict, previous: dict) -> dict:
    out = {}
    for key, cur in current.items():
        prev = previous.get(key)
        if isinstance(cur, (int, float)) and not isinstance(cur, bool) and isinstance(prev, (int, float)):
            diff = cur - prev
            out[key] = {"abs": diff, "pct": (diff / prev) if prev else None}
    return out


# Keys a series function may return that describe the whole response, not one window.
META_KEYS = ("approximate", "formula", "group_by")


def envelope(window: Window, bucket: str | None, current: dict, previous: dict | None = None,
             **extra) -> dict:
    current = dict(current)
    meta = {k: current.pop(k) for k in META_KEYS if k in current}
    body = {
        "bucket": bucket,
        "current": {**window.as_dict(), **current},
        "previous": None,
        "deltas": None,
        **meta,
        **extra,
    }
    if previous is not None:
        previous = {k: v for k, v in previous.items() if k not in META_KEYS}
        body["previous"] = {**window.previous().as_dict(), **previous}
        if "totals" in current and "totals" in previous:
            body["deltas"] = deltas(current["totals"], previous["totals"])
    return body


# --- legacy /v1/reports/* ----------------------------------------------------------


def _build_where(
    *,
    skill_name: str | None = None,
    status: str | None = None,
    session_id: str | None = None,
    upn: str | None = None,
    ts_from: datetime | None = None,
    ts_to: datetime | None = None,
    q: str | None = None,
    error_code: str | None = None,
) -> tuple[str, dict]:
    conditions, params = [], {}
    if skill_name is not None:
        conditions.append("skill_name = :skill_name")
        params["skill_name"] = skill_name
    if status is not None:
        conditions.append("status = :status")
        params["status"] = status
    if session_id is not None:
        conditions.append("session_id = :session_id")
        params["session_id"] = session_id
    if upn is not None:
        conditions.append("auth_upn = :upn")
        params["upn"] = upn
    if error_code is not None:
        conditions.append("error_code = :error_code")
        params["error_code"] = error_code
    if ts_from is not None:
        conditions.append("client_ts >= :ts_from")
        params["ts_from"] = ts_from
    if ts_to is not None:
        conditions.append("client_ts <= :ts_to")
        params["ts_to"] = ts_to
    if q is not None and q.strip():
        conditions.append(_FTS)
        params["q"] = q.strip()
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return where, params


def search_events(engine: Engine, *, limit: int, offset: int, **filters) -> tuple[list[dict], int]:
    where, params = _build_where(**filters)
    with engine.connect() as conn:
        total = conn.execute(
            text(f"SELECT count(*) FROM events {where}"), params
        ).scalar_one()
        rows = conn.execute(
            text(
                f"SELECT {_EVENT_COLUMNS} FROM events {where} "
                "ORDER BY client_ts DESC, event_id LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": limit, "offset": offset},
        ).mappings().all()
    return [dict(r) for r in rows], total


def skill_aggregates(engine: Engine, **filters) -> list[dict]:
    where, params = _build_where(**filters)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT skill_name,
                       count(*) AS total,
                       count(*) FILTER (WHERE status = 'success') AS success_count,
                       count(*) FILTER (WHERE status = 'failure') AS failure_count,
                       count(*) FILTER (WHERE status = 'user_corrected') AS user_corrected_count
                FROM events {where}
                GROUP BY skill_name
                ORDER BY total DESC, skill_name
                """
            ),
            params,
        ).mappings().all()
    out = []
    for r in rows:
        total = r["total"]
        out.append(
            {
                "skill_name": r["skill_name"],
                "total": total,
                "success_count": r["success_count"],
                "failure_count": r["failure_count"],
                "user_corrected_count": r["user_corrected_count"],
                "success_rate": r["success_count"] / total,
                "failure_rate": r["failure_count"] / total,
                "user_corrected_rate": r["user_corrected_count"] / total,
            }
        )
    return out


def overview(engine: Engine, *, bucket: str = "day", **filters) -> dict:
    if bucket not in VALID_BUCKETS:
        raise ValueError(f"bucket must be one of {sorted(VALID_BUCKETS)}")
    where, params = _build_where(**filters)
    with engine.connect() as conn:
        totals = conn.execute(
            text(
                f"""
                SELECT count(*) AS events,
                       count(DISTINCT session_id) AS sessions,
                       count(DISTINCT auth_upn) AS users,
                       count(DISTINCT skill_name) AS skills
                FROM events {where}
                """
            ),
            params,
        ).mappings().one()
        statuses = conn.execute(
            text(f"SELECT status, count(*) AS n FROM events {where} GROUP BY status"),
            params,
        ).mappings().all()
        # bucket is validated against VALID_BUCKETS above, safe to inline
        activity = conn.execute(
            text(
                f"""
                SELECT date_trunc('{bucket}', client_ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS bucket,
                       count(*) AS n
                FROM events {where}
                GROUP BY 1 ORDER BY 1
                """
            ),
            params,
        ).mappings().all()
    return {
        "totals": dict(totals),
        "status_breakdown": {r["status"]: r["n"] for r in statuses},
        "activity": [{"bucket": r["bucket"], "count": r["n"]} for r in activity],
    }


def sentiment_rows(engine: Engine, **filters) -> list[dict]:
    where, params = _build_where(**filters)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT event_id, session_id, skill_name, status, feedback, "
                f"interaction, client_ts, received_at FROM events {where}"
            ),
            params,
        ).mappings().all()
    return [dict(r) for r in rows]
