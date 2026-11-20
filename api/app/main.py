from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import db, hook_ingest
from .artifacts import router as artifacts_router
from .auth import AuthContext, build_validator, require_auth
from .models import HookEventBatch, HookEventResult, TelemetryBatch
from .otel import router as otel_router
from .projection import Identity
from .settings import Settings, get_settings
from .transcripts import router as transcripts_router


def _ensure_schema(app: FastAPI) -> None:
    db.ensure_schema(app.state)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.auth_validator = build_validator(settings)
        app.state.engine = db.make_engine(settings.database_url)
        app.state.schema_applied = False
        try:
            _ensure_schema(app)
        except Exception:
            pass  # retried by /readyz
        yield
        app.state.engine.dispose()

    app = FastAPI(title="Ouroboros Telemetry Ingestion", lifespan=lifespan)
    app.include_router(otel_router)
    app.include_router(transcripts_router)
    app.include_router(artifacts_router)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        # hook-events contract: 400 for a body that is not JSON, 422 for a
        # well-formed body with the wrong envelope shape.
        if request.url.path == "/v1/telemetry/hook-events" and any(
            e.get("type") == "json_invalid" for e in exc.errors()
        ):
            return JSONResponse(status_code=400, content={"detail": "Malformed JSON body"})
        return await request_validation_exception_handler(request, exc)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "alive"}

    @app.get("/readyz")
    def readyz(request: Request) -> dict:
        try:
            _ensure_schema(request.app)
            db.check_ready(request.app.state.engine)
        except Exception:
            raise HTTPException(status_code=503, detail="Database unavailable")
        return {"status": "ready"}

    @app.post("/v1/telemetry/events", status_code=202)
    def ingest(
        batch: TelemetryBatch,
        request: Request,
        response: Response,
        identity: AuthContext = Depends(require_auth),
    ) -> dict:
        window = request.app.state.settings.freshness_window_seconds
        now = datetime.now(timezone.utc)
        stale = [
            str(e.event_id)
            for e in batch.events
            if (now - e.client_ts).total_seconds() > window
        ]
        if stale:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": f"Events older than freshness window ({window}s)",
                    "stale_event_ids": stale,
                },
            )

        _ensure_schema(request.app)
        db.insert_events(
            request.app.state.engine, batch.events, identity.oid, identity.upn
        )
        return {"status": "accepted", "received": len(batch.events)}

    @app.post("/v1/telemetry/hook-events", status_code=202, response_model=HookEventResult)
    def ingest_hook_events(
        batch: HookEventBatch,
        request: Request,
        identity: AuthContext = Depends(require_auth),
    ) -> HookEventResult:
        """Structured hook events (design D6/D9/D17): 202 with per-event
        accounting; only a malformed envelope fails the whole batch (422)."""
        _ensure_schema(request.app)
        return hook_ingest.ingest_batch(
            request.app.state.engine,
            batch.events,
            Identity(identity.oid, identity.upn),
            request.app.state.settings.hook_events_max_age_seconds,
        )

    return app
