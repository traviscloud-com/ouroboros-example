"""Sentiment scoring (design D3) and the persisted daily series (task 3.6).

Text sentiment is computed from user-authored content only — user_prompt,
user_corrections, notes. assistant_response is never scored: user sentiment must
reflect what the user said, not the assistant's tone. Deterministic and offline.

``persist_daily`` is run by the scheduler and writes ``analytics.sentiment_daily``
(one row per UTC event-time day and skill) so the analytics routes read a table
instead of re-scoring events per request. The legacy ``/v1/reports/sentiment``
keeps computing its richer shape (per-session rollups) on request.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from .settings import Settings


def _clamp(x: float) -> float:
    return max(-1.0, min(1.0, x))


@dataclass(frozen=True)
class EventSentiment:
    event_id: str
    session_id: str
    skill_name: str
    status: str
    text_score: float | None
    blended: float

    @property
    def has_text(self) -> bool:
        return self.text_score is not None


class SentimentScorer:
    def __init__(self, settings: Settings):
        self._analyzer = SentimentIntensityAnalyzer()
        self._text_w = settings.sentiment_text_weight
        self._behavior_w = settings.sentiment_behavior_weight
        self._status_weights = {
            "failure": settings.sentiment_weight_failure,
            "user_corrected": settings.sentiment_weight_user_corrected,
            "success": settings.sentiment_weight_success,
        }

    def behavioral(self, status: str) -> float:
        return self._status_weights[status]

    def text_compound(self, text_value: str) -> float:
        return self._analyzer.polarity_scores(text_value)["compound"]

    def score(self, row: dict) -> EventSentiment:
        feedback = row.get("feedback") or {}
        interaction = row.get("interaction") or {}
        user_texts = [
            interaction.get("user_prompt"),
            feedback.get("user_corrections"),
            feedback.get("notes"),
        ]
        joined = " ".join(t.strip() for t in user_texts if isinstance(t, str) and t.strip())
        behavioral = self._status_weights[row["status"]]
        if joined:
            text_score = self.text_compound(joined)
            blended = _clamp(self._text_w * text_score + self._behavior_w * behavioral)
        else:
            text_score = None
            blended = _clamp(behavioral)
        return EventSentiment(
            event_id=str(row["event_id"]),
            session_id=str(row["session_id"]),
            skill_name=row["skill_name"],
            status=row["status"],
            text_score=text_score,
            blended=blended,
        )


def rollup(events: list[EventSentiment], key: str) -> list[dict]:
    """Aggregate scored events by 'skill_name' or 'session_id'."""
    groups: dict[str, list[EventSentiment]] = {}
    for e in events:
        groups.setdefault(getattr(e, key), []).append(e)
    out = []
    for group_key, members in groups.items():
        n = len(members)
        out.append(
            {
                key: group_key,
                "sentiment": sum(m.blended for m in members) / n,
                "event_count": n,
                "text_coverage": sum(1 for m in members if m.has_text) / n,
                "user_corrected_count": sum(1 for m in members if m.status == "user_corrected"),
                "failure_count": sum(1 for m in members if m.status == "failure"),
            }
        )
    out.sort(key=lambda r: r["sentiment"])
    return out


def _event_day(row: dict) -> date:
    ts: datetime = row.get("client_ts") or row["received_at"]
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).date()


def trend(rows: list[dict], scored: list[EventSentiment]) -> list[dict]:
    """Daily (event-time) mean blended score; rows and scored are index-aligned."""
    buckets: dict[str, list[float]] = {}
    for row, event in zip(rows, scored):
        buckets.setdefault(_event_day(row).isoformat(), []).append(event.blended)
    return [
        {"bucket": day, "sentiment": sum(v) / len(v), "event_count": len(v)}
        for day, v in sorted(buckets.items())
    ]


# --- persisted daily series ----------------------------------------------------------


def daily_rows(rows: list[dict], scored: list[EventSentiment], scorer: SentimentScorer) -> list[dict]:
    """Collapse scored events into analytics.sentiment_daily rows."""
    groups: dict[tuple[date, str], list[tuple[dict, EventSentiment]]] = defaultdict(list)
    for row, event in zip(rows, scored):
        groups[(_event_day(row), event.skill_name)].append((row, event))
    out = []
    for (day, skill), members in sorted(groups.items()):
        n = len(members)
        texts = [e.text_score for _, e in members if e.text_score is not None]
        out.append(
            {
                "day": day,
                "skill_name": skill,
                "event_count": n,
                "text_count": len(texts),
                "text_coverage": len(texts) / n,
                "text_sentiment": (sum(texts) / len(texts)) if texts else None,
                "behavioral_sentiment": sum(scorer.behavioral(e.status) for _, e in members) / n,
                "sentiment": sum(e.blended for _, e in members) / n,
            }
        )
    return out


def persist_daily(engine: Engine, scorer: SentimentScorer, *, recompute_days: int) -> dict:
    """Recompute analytics.sentiment_daily for the last ``recompute_days`` days (all
    history when the table is empty). Returns a small stats dict."""
    with engine.connect() as conn:
        empty = conn.execute(text("SELECT count(*) = 0 FROM analytics.sentiment_daily")).scalar_one()
    since: date | None = None
    if not empty:
        since = datetime.now(timezone.utc).date() - timedelta(days=recompute_days)
    where = "WHERE client_ts >= :since" if since is not None else ""
    params = {"since": datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc)} if since else {}
    with engine.connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                text(
                    f"SELECT event_id, session_id, skill_name, status, feedback, interaction, client_ts, "
                    f"received_at FROM events {where}"
                ),
                params,
            ).mappings().all()
        ]
    scored = [scorer.score(r) for r in rows]
    daily = daily_rows(rows, scored, scorer)
    with engine.begin() as conn:
        if since is None:
            conn.execute(text("DELETE FROM analytics.sentiment_daily"))
        else:
            conn.execute(text("DELETE FROM analytics.sentiment_daily WHERE day >= :since"), {"since": since})
        if daily:
            conn.execute(
                text(
                    "INSERT INTO analytics.sentiment_daily (day, skill_name, event_count, text_count, "
                    "text_coverage, text_sentiment, behavioral_sentiment, sentiment) VALUES "
                    "(:day, :skill_name, :event_count, :text_count, :text_coverage, :text_sentiment, "
                    ":behavioral_sentiment, :sentiment)"
                ),
                daily,
            )
    return {"events_scored": len(rows), "rows_written": len(daily),
            "since": since.isoformat() if since else None}
