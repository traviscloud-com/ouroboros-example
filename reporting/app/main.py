import logging
import mimetypes
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, github, proposals, queries, routes
from .auth import AuthMiddleware, bootstrap_admins, require_role
from .redaction import RedactingRoute
from .scheduler import Scheduler
from .sentiment import SentimentScorer, rollup, trend
from .settings import Settings, get_settings

log = logging.getLogger("ouroboros.reporting")

Status = Literal["success", "failure", "user_corrected"]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    scorer = SentimentScorer(settings)
    sentiment_cache: dict[tuple, tuple[float, dict]] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.engine = db.make_engine(settings.database_url)
        app.state.scheduler = Scheduler(settings, lambda: app.state.engine, scorer)
        app.state.github = github.GitHub(settings) if github.mode(settings) != "disabled" else None
        app.state.scheduler.github = app.state.github
        try:
            bootstrap_admins(app.state.engine, settings.admin_emails)
        except Exception:  # database not up yet: the scheduler retries, /readyz reports it
            log.warning("bootstrap of ADMIN_EMAILS deferred: database unavailable")
        app.state.scheduler.start()
        yield
        await app.state.scheduler.stop()
        if app.state.github is not None:
            app.state.github.close()
        app.state.engine.dispose()

    app = FastAPI(title="Ouroboros Telemetry Reporting", lifespan=lifespan)
    app.add_middleware(AuthMiddleware, settings=settings, engine_getter=lambda: app.state.engine)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "alive"}

    @app.get("/readyz")
    def readyz(request: Request) -> dict:
        try:
            version = db.check_ready(request.app.state.engine)
        except db.SchemaNotReady as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception:
            raise HTTPException(status_code=503, detail="Database unavailable")
        return {"status": "ready", "schema_version": version}

    # Legacy report routes: response shapes unchanged for the current SPA; filters
    # now use client_ts and websearch_to_tsquery (task 3.4); redaction applies
    # through RedactingRoute (task 3.2).
    reports = APIRouter(prefix="/v1/reports", route_class=RedactingRoute,
                        dependencies=[Depends(require_role("leadership"))])

    @reports.get("/events")
    def events(
        request: Request,
        skill_name: str | None = None,
        status: Status | None = None,
        session_id: str | None = None,
        upn: str | None = None,
        ts_from: datetime | None = Query(default=None, alias="from"),
        ts_to: datetime | None = Query(default=None, alias="to"),
        q: str | None = None,
        error_code: str | None = None,
        limit: int = Query(default=50, ge=1),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        limit = min(limit, settings.max_page_size)
        rows, total = queries.search_events(
            request.app.state.engine,
            limit=limit,
            offset=offset,
            skill_name=skill_name,
            status=status,
            session_id=session_id,
            upn=upn,
            ts_from=ts_from,
            ts_to=ts_to,
            q=q,
            error_code=error_code,
        )
        return {"events": rows, "total": total, "limit": limit, "offset": offset}

    @reports.get("/skills")
    def skills(
        request: Request,
        ts_from: datetime | None = Query(default=None, alias="from"),
        ts_to: datetime | None = Query(default=None, alias="to"),
    ) -> dict:
        return {
            "skills": queries.skill_aggregates(
                request.app.state.engine, ts_from=ts_from, ts_to=ts_to
            )
        }

    @reports.get("/overview")
    def overview(
        request: Request,
        ts_from: datetime | None = Query(default=None, alias="from"),
        ts_to: datetime | None = Query(default=None, alias="to"),
        bucket: Literal["hour", "day", "week"] = "day",
    ) -> dict:
        return queries.overview(
            request.app.state.engine, bucket=bucket, ts_from=ts_from, ts_to=ts_to
        )

    @reports.get("/sentiment")
    def sentiment(
        request: Request,
        ts_from: datetime | None = Query(default=None, alias="from"),
        ts_to: datetime | None = Query(default=None, alias="to"),
    ) -> dict:
        cache_key = (ts_from, ts_to)
        cached = sentiment_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < settings.sentiment_cache_ttl_seconds:
            return cached[1]
        rows = queries.sentiment_rows(
            request.app.state.engine, ts_from=ts_from, ts_to=ts_to
        )
        scored = [scorer.score(r) for r in rows]
        result = {
            "overall": (
                {
                    "sentiment": sum(e.blended for e in scored) / len(scored),
                    "event_count": len(scored),
                    "text_coverage": sum(1 for e in scored if e.has_text) / len(scored),
                }
                if scored
                else {"sentiment": None, "event_count": 0, "text_coverage": 0.0}
            ),
            "skills": rollup(scored, "skill_name"),
            "sessions": rollup(scored, "session_id"),
            "trend": trend(rows, scored),
        }
        sentiment_cache[cache_key] = (time.monotonic(), result)
        return result

    app.include_router(reports)
    app.include_router(routes.build_identity_router(settings))
    app.include_router(routes.build_analytics_router(settings))
    app.include_router(routes.build_export_router(settings))
    app.include_router(routes.build_internal_router())
    app.include_router(proposals.build_router(settings))

    if settings.static_dir and Path(settings.static_dir).is_dir():
        mount_spa(app, Path(settings.static_dir))

    return app


# python-slim ships no /etc/mime.types, so StaticFiles would serve fonts as
# application/octet-stream.
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")

SPA_RESERVED_PREFIXES = ("v1/", "internal/", "assets/")
SPA_RESERVED_PATHS = ("healthz", "readyz")


def mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Serve the Vite build with an SPA fallback (task 4.12).

    * ``/assets/*`` (hashed Vite output) is a plain ``StaticFiles`` mount: a
      missing asset is a 404, never ``index.html`` (a stale deploy must fail
      loudly, not execute HTML as a module).
    * Any other file that exists under ``static_dir`` is served as is
      (``favicon``, ``robots.txt``...).
    * Everything else - react-router paths such as ``/engineering/sessions``
      on deep link or refresh - gets ``index.html`` with ``Cache-Control:
      no-cache`` so a new deploy is picked up on the next load.
    * ``/v1/*``, ``/internal/*``, ``/healthz`` and ``/readyz`` are never
      shadowed: the API routers are registered before this catch-all and an
      unknown API path stays a 404 (the auth middleware still gates them).
    """
    static_dir = static_dir.resolve()
    index = static_dir / "index.html"
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith(SPA_RESERVED_PREFIXES) or path in SPA_RESERVED_PATHS:
            raise HTTPException(status_code=404, detail="Not Found")
        if path:
            candidate = (static_dir / path).resolve()
            if candidate.is_file() and candidate.is_relative_to(static_dir):
                return FileResponse(candidate)
        if not index.is_file():
            raise HTTPException(status_code=404, detail="Dashboard not built")
        return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-cache"})
