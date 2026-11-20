"""Weekly correction themes (design D13, task 3.6): stdlib keyword/bigram
clustering of negative user corrections, written to
``analytics.correction_themes`` by the scheduler.

A correction is an ``events`` row whose ``feedback.user_corrections`` carries
text. It counts as *negative* when its blended sentiment (the same VADER +
behavioural blend the dashboard shows) is below zero: a neutral-toned
correction on a ``user_corrected`` outcome is negative, a correction phrased
as praise is not. Per ISO week (UTC) the 50 most frequent word bigrams
(stop words removed, counted once per correction) become the themes, each
with up to five example event ids. LLM theme scoring stays behind
``THEMES_LLM`` (off, not implemented in v1).
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .sentiment import SentimentScorer

TOP_PER_WEEK = 50
EXAMPLES_PER_THEME = 5
MIN_TOKEN_LENGTH = 3

STOP_WORDS = frozenset(
    """
    the and that this with for you not but are was have has had from your should would could
    please instead than then into its our use used just also about when what which there their
    they them been were will can did does don didn doesn all any one out more only here where
    why how who whom whose because while before after again further once each few most other
    some such own same too very can't cannot can't don't isn't aren't wasn't weren't won't
    wouldn't shouldn't couldn't doesn't didn't hasn't haven't hadn't you're you've it's that's
    there's let's i'm we're they're want wanted need needed make made like get got did doing
    done being having over under between through during without within along across behind
    around still already always never ever actually really maybe perhaps rather quite much many
    these those thing things something anything nothing everything way ways
    """.split()
)

_TOKEN = re.compile(r"[a-z0-9][a-z0-9'_-]*")


def tokenize(value: str) -> list[str]:
    return [
        t for t in _TOKEN.findall(value.lower())
        if len(t) >= MIN_TOKEN_LENGTH and t not in STOP_WORDS
    ]


def bigrams(tokens: list[str]) -> set[str]:
    return {f"{a} {b}" for a, b in zip(tokens, tokens[1:]) if a != b}


def week_of(ts: datetime) -> date:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    d = ts.astimezone(timezone.utc).date()
    return d - timedelta(days=d.weekday())


def cluster(corrections: list[dict], scorer: SentimentScorer) -> list[dict]:
    """``corrections`` rows need event_id, session_id, skill_name, status, feedback,
    interaction, client_ts. Returns analytics.correction_themes rows."""
    per_week: dict[date, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for row in corrections:
        feedback = row.get("feedback") or {}
        txt = feedback.get("user_corrections")
        if not isinstance(txt, str) or not txt.strip():
            continue
        if scorer.score(row).blended >= 0:
            continue
        week = week_of(row["client_ts"])
        for theme in bigrams(tokenize(txt)):
            per_week[week][theme].add(str(row["event_id"]))
    out = []
    for week, themes in sorted(per_week.items()):
        ranked = sorted(themes.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:TOP_PER_WEEK]
        for theme, ids in ranked:
            out.append(
                {
                    "week": week,
                    "theme": theme,
                    "count": len(ids),
                    "example_event_ids": sorted(ids)[:EXAMPLES_PER_THEME],
                }
            )
    return out


def persist(engine: Engine, scorer: SentimentScorer, *, recompute_days: int) -> dict:
    """Recompute themes for weeks overlapping the last ``recompute_days`` (all history
    when the table is empty)."""
    with engine.connect() as conn:
        empty = conn.execute(text("SELECT count(*) = 0 FROM analytics.correction_themes")).scalar_one()
    since_week: date | None = None
    if not empty:
        since_week = week_of(datetime.now(timezone.utc) - timedelta(days=recompute_days))
    where = "AND client_ts >= :since" if since_week else ""
    params = {"since": datetime.combine(since_week, datetime.min.time(), tzinfo=timezone.utc)} if since_week else {}
    with engine.connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                text(
                    f"SELECT event_id, session_id, skill_name, status, feedback, interaction, client_ts "
                    f"FROM events WHERE feedback ? 'user_corrections' {where}"
                ),
                params,
            ).mappings().all()
        ]
    themes = cluster(rows, scorer)
    with engine.begin() as conn:
        if since_week is None:
            conn.execute(text("DELETE FROM analytics.correction_themes"))
        else:
            conn.execute(text("DELETE FROM analytics.correction_themes WHERE week >= :w"), {"w": since_week})
        if themes:
            conn.execute(
                text(
                    "INSERT INTO analytics.correction_themes (week, theme, count, example_event_ids) "
                    "VALUES (:week, :theme, :count, CAST(:example_event_ids AS UUID[]))"
                ),
                themes,
            )
    return {"corrections_scanned": len(rows), "themes_written": len(themes),
            "since_week": since_week.isoformat() if since_week else None}
