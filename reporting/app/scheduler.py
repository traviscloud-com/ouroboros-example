"""In-process scheduler (design D13) and retention (design D11).

An asyncio ticker runs every ``AGGREGATE_REFRESH_SECONDS`` (0 disables it):

1. wait for the schema (``/readyz`` semantics) and upsert the bootstrap admins;
2. ``REFRESH MATERIALIZED VIEW`` every ``analytics.*`` matview - ``CONCURRENTLY``
   when the view is populated and has a unique index (readers never block),
   plain otherwise (first refresh of a view created ``WITH NO DATA``);
3. persist ``analytics.sentiment_daily`` and ``analytics.correction_themes``;
4. once per ``RETENTION_INTERVAL_SECONDS`` (nightly) run retention and write
   one ``audit_log`` row (actor ``system``, action ``retention.run``);
5. improvement loop (design D14): fill due ``improvement_outcomes``, poll open
   PRs every ``GITHUB_POLL_SECONDS`` when GitHub is configured, and - outside
   the refresh lock, under its own - start the analyst when ``ANALYST_SCHEDULE``
   is set, ``ANTHROPIC_API_KEY`` is present and the last scheduled run is older
   than the interval.

``POST /internal/refresh`` runs the same tick on demand (optionally forcing
retention) under the same lock, so a CronJob can replace the ticker when the
service ever runs with more than one replica. The service is single-replica
today; there is no leader election.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine
from starlette.concurrency import run_in_threadpool

from . import db, sentiment, themes
from .analyst import job as analyst_job
from .analyst import outcomes as analyst_outcomes
from .auth import Principal, bootstrap_admins, write_audit
from .settings import Settings

log = logging.getLogger("ouroboros.reporting.scheduler")

# Sensitive text keys that may sit inside the projected hook payload / raw JSONB
# (client/ouroboros_client/hook.py whitelist) - stripped by retention.
PAYLOAD_TEXT_KEYS = (
    "prompt", "last_assistant_message", "response", "compact_summary", "custom_instructions",
    "tool_input", "tool_response", "session_title",
)
FEEDBACK_TEXT_KEYS = ("notes", "user_corrections", "suggested_patch")
INTERACTION_TEXT_KEYS = ("user_prompt", "assistant_response")


def _pg_array(keys) -> str:
    return "ARRAY[" + ", ".join(f"'{k}'" for k in keys) + "]"


# --- materialized views ----------------------------------------------------------------


def list_matviews(engine: Engine, schema: str = "analytics") -> list[dict]:
    sql = """
        SELECT mv.matviewname AS name, mv.ispopulated,
               EXISTS (SELECT 1 FROM pg_index i
                         JOIN pg_class c ON c.oid = i.indrelid
                         JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = mv.schemaname AND c.relname = mv.matviewname
                          AND i.indisunique) AS has_unique_index
          FROM pg_matviews mv
         WHERE mv.schemaname = :schema
         ORDER BY mv.matviewname"""
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), {"schema": schema}).mappings().all()]


def refresh_matviews(engine: Engine, schema: str = "analytics") -> list[dict]:
    results = []
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for view in list_matviews(engine, schema):
            concurrently = bool(view["ispopulated"] and view["has_unique_index"])
            started = time.monotonic()
            conn.execute(
                text(
                    f"REFRESH MATERIALIZED VIEW {'CONCURRENTLY ' if concurrently else ''}"
                    f"{schema}.{view['name']}"
                )
            )
            results.append(
                {"view": f"{schema}.{view['name']}", "concurrently": concurrently,
                 "ms": round((time.monotonic() - started) * 1000, 1)}
            )
    return results


# --- retention -----------------------------------------------------------------------


def run_retention(engine: Engine, settings: Settings, *, now: datetime | None = None) -> dict:
    """Null sensitive text older than RETENTION_SENSITIVE_DAYS, delete internal rows
    older than RETENTION_INTERNAL_DAYS, write one audit_log row. Returns the counts."""
    now = now or datetime.now(timezone.utc)
    cut_s = now - timedelta(days=settings.retention_sensitive_days)
    cut_i = now - timedelta(days=settings.retention_internal_days)
    payload_keys = _pg_array(PAYLOAD_TEXT_KEYS)
    feedback_keys = _pg_array(FEEDBACK_TEXT_KEYS)
    interaction_keys = _pg_array(INTERACTION_TEXT_KEYS)
    marker = """'{"retention_redacted": true}'::jsonb"""

    sensitive = {
        "turns.text": (
            "UPDATE turns SET prompt_text = NULL, response_text = NULL "
            "WHERE coalesce(submitted_at, received_at) < :cut "
            "AND (prompt_text IS NOT NULL OR response_text IS NOT NULL)"
        ),
        "events.feedback_interaction": (
            f"UPDATE events SET feedback = (feedback - {feedback_keys}) || {marker}, "
            f"interaction = CASE WHEN interaction IS NULL THEN NULL "
            f"ELSE (interaction - {interaction_keys}) || {marker} END "
            f"WHERE client_ts < :cut "
            f"AND (feedback ?| {feedback_keys} OR interaction ?| {interaction_keys})"
        ),
        "compactions.summary_text": (
            "UPDATE compactions SET summary_text = NULL WHERE at < :cut AND summary_text IS NOT NULL"
        ),
        "tool_calls.content": (
            "UPDATE tool_calls SET input_content = NULL, output_content = NULL "
            "WHERE coalesce(started_at, received_at) < :cut "
            "AND (input_content IS NOT NULL OR output_content IS NOT NULL)"
        ),
        "sessions.title_redacted": (
            "UPDATE sessions SET title_redacted = NULL "
            "WHERE coalesce(started_at, received_at) < :cut AND title_redacted IS NOT NULL"
        ),
        "transcript_lines.content": (
            "UPDATE transcript_lines SET content = NULL WHERE timestamp < :cut AND content IS NOT NULL"
        ),
        "turn_errors.message": (
            "UPDATE turn_errors SET message = NULL WHERE at < :cut AND message IS NOT NULL"
        ),
        "hook_events_raw.payload": (
            f"UPDATE hook_events_raw SET payload = (payload - {payload_keys}) || {marker} "
            f"WHERE client_ts < :cut AND payload ?| {payload_keys}"
        ),
    }
    # The `raw` projection copies on the normalized tables can carry the same keys.
    for table, ts_col in (
        ("sessions", "coalesce(started_at, received_at)"),
        ("turns", "coalesce(submitted_at, received_at)"),
        ("tool_calls", "coalesce(started_at, received_at)"),
        ("compactions", "at"),
    ):
        sensitive[f"{table}.raw"] = (
            f"UPDATE {table} SET raw = (raw - {payload_keys}) || {marker} "
            f"WHERE {ts_col} < :cut AND raw ?| {payload_keys}"
        )

    internal = {
        "hook_events_raw": "DELETE FROM hook_events_raw WHERE client_ts < :cut",
        "transcript_lines": "DELETE FROM transcript_lines WHERE timestamp < :cut",
        "hook_runs": "DELETE FROM hook_runs WHERE at < :cut",
        "turn_errors": "DELETE FROM turn_errors WHERE at < :cut",
        "model_switches": "DELETE FROM model_switches WHERE at < :cut",
        "artifact_versions.content_text": (
            "UPDATE artifact_versions SET content_text = NULL "
            "WHERE seen_until IS NOT NULL AND seen_until < :cut AND content_text IS NOT NULL"
        ),
    }

    counts: dict[str, dict[str, int]] = {"sensitive": {}, "internal": {}}
    started = time.monotonic()
    with engine.begin() as conn:
        for name, sql in sensitive.items():
            counts["sensitive"][name] = conn.execute(text(sql), {"cut": cut_s}).rowcount
        for name, sql in internal.items():
            counts["internal"][name] = conn.execute(text(sql), {"cut": cut_i}).rowcount
        details = {
            "sensitive_cutoff": cut_s.isoformat(),
            "internal_cutoff": cut_i.isoformat(),
            "retention_sensitive_days": settings.retention_sensitive_days,
            "retention_internal_days": settings.retention_internal_days,
            "counts": counts,
            "ms": round((time.monotonic() - started) * 1000, 1),
        }
        write_audit(conn, None, "retention.run", target_kind="database", details=details)
    return details


def last_retention_at(engine: Engine) -> datetime | None:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT max(at) FROM audit_log WHERE action = 'retention.run'")
        ).scalar_one_or_none()


# --- the ticker ---------------------------------------------------------------------------


class Scheduler:
    def __init__(self, settings: Settings, engine_getter, scorer: sentiment.SentimentScorer):
        self.settings = settings
        self._engine_getter = engine_getter
        self.scorer = scorer
        self.lock = asyncio.Lock()
        self.last_result: dict | None = None
        self.last_retention: datetime | None = None
        self._bootstrapped = False
        self._task: asyncio.Task | None = None
        # Improvement loop: the analyst runs under its own lock (Claude calls can
        # take minutes and must not hold up aggregate refreshes).
        self.analyst_lock = asyncio.Lock()
        self.analyst_client_factory = analyst_job.make_client  # tests inject a mock
        self.github = None  # set by the app (github.GitHub) when configured
        self.background: set[asyncio.Task] = set()
        self.last_poll: float | None = None
        self.last_analyst: datetime | None = None

    @property
    def engine(self) -> Engine:
        return self._engine_getter()

    def start(self) -> None:
        if self.settings.aggregate_refresh_seconds > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop(), name="ouroboros-scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        interval = self.settings.aggregate_refresh_seconds
        while True:
            try:
                await self.run_tick()
            except Exception:  # keep ticking; the result records the error
                log.exception("scheduler tick failed")
            try:
                if await run_in_threadpool(self.analyst_due, datetime.now(timezone.utc)):
                    task = asyncio.create_task(self.run_analyst(trigger="schedule"))
                    self.background.add(task)
                    task.add_done_callback(self.background.discard)
            except Exception:
                log.exception("analyst scheduling failed")
            await asyncio.sleep(interval)

    # --- improvement loop -----------------------------------------------------------

    def analyst_due(self, now: datetime) -> bool:
        interval = self.settings.analyst_interval_seconds
        if interval is None or analyst_job.disabled_reason(self.settings) or self.analyst_lock.locked():
            return False
        if self.last_analyst is None:
            self.last_analyst = analyst_job.last_scheduled_at(self.engine)
        return self.last_analyst is None or (now - self.last_analyst).total_seconds() >= interval

    async def run_analyst(self, *, trigger: str = "manual", actor: Principal | None = None) -> dict:
        reason = analyst_job.disabled_reason(self.settings)
        if reason:
            raise analyst_job.AnalystDisabled(reason)
        if self.analyst_lock.locked():
            raise analyst_job.AnalystBusy("an analyst run is already in progress")
        async with self.analyst_lock:
            if trigger == "schedule":
                self.last_analyst = datetime.now(timezone.utc)
            client = self.analyst_client_factory(self.settings)
            return await run_in_threadpool(
                analyst_job.run, self.engine, self.settings, client=client, trigger=trigger, actor=actor
            )

    def poll_due(self) -> bool:
        if self.github is None or not self.github.enabled or self.settings.github_poll_seconds <= 0:
            return False
        return self.last_poll is None or time.monotonic() - self.last_poll >= self.settings.github_poll_seconds

    async def run_tick(self, *, force_retention: bool = False, actor: Principal | None = None) -> dict:
        async with self.lock:
            result = await run_in_threadpool(self.tick_sync, force_retention, actor)
        self.last_result = result
        return result

    def retention_due(self, now: datetime) -> bool:
        if self.last_retention is None:
            self.last_retention = last_retention_at(self.engine)
        if self.last_retention is None:
            return True
        return (now - self.last_retention).total_seconds() >= self.settings.retention_interval_seconds

    def tick_sync(self, force_retention: bool = False, actor: Principal | None = None) -> dict:
        now = datetime.now(timezone.utc)
        result: dict = {"at": now.isoformat(), "trigger": "manual" if actor else "ticker"}
        engine = self.engine
        try:
            db.check_ready(engine)
        except db.SchemaNotReady as exc:
            result["skipped"] = str(exc)
            return result
        if not self._bootstrapped:
            result["bootstrap_admins"] = bootstrap_admins(engine, self.settings.admin_emails)
            self._bootstrapped = True
        started = time.monotonic()
        result["matviews"] = refresh_matviews(engine)
        result["sentiment"] = sentiment.persist_daily(
            engine, self.scorer, recompute_days=self.settings.sentiment_recompute_days
        )
        result["themes"] = themes.persist(
            engine, self.scorer, recompute_days=self.settings.sentiment_recompute_days
        )
        if force_retention or self.retention_due(now):
            result["retention"] = run_retention(engine, self.settings, now=now)
            self.last_retention = now
        try:
            result["outcomes"] = analyst_outcomes.measure(engine, self.settings, now=now)
        except Exception as exc:  # never let the loop break the refresh
            log.exception("outcome measurement failed")
            result["outcomes"] = {"error": repr(exc)}
        if self.poll_due():
            from .proposals import poll_open_prs

            self.last_poll = time.monotonic()
            result["github_poll"] = poll_open_prs(engine, self.github)
        result["ms"] = round((time.monotonic() - started) * 1000, 1)
        if actor is not None:
            with engine.begin() as conn:
                write_audit(
                    conn, actor, "refresh.manual", target_kind="analytics",
                    details={"force_retention": force_retention, "ms": result["ms"],
                             "matviews": len(result["matviews"])},
                )
        return result
