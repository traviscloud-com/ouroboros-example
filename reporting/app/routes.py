"""Phase 3 routes: identity, principals, analytics series, export, internal
refresh. Every router uses :class:`RedactingRoute`, so leadership responses
pass through the redaction layer; roles are enforced per route with
:func:`require_role`."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import analytics, auth, export, github
from .analyst import job
from .auth import LANDING, ROLES, Principal, require_role
from .queries import Window, deltas, envelope, make_window
from .redaction import RedactingRoute
from .settings import Settings

Bucket = Literal["day", "week"]
Compare = Literal["previous"] | None


def _engine(request: Request):
    return request.app.state.engine


def _window(ts_from: datetime | None, ts_to: datetime | None) -> Window:
    try:
        return make_window(ts_from, ts_to)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _series(request: Request, fn, window: Window, compare_to: Compare, bucket_label: str | None,
            **kwargs) -> dict:
    engine = _engine(request)
    try:
        current = fn(engine, window, **kwargs)
        previous = fn(engine, window.previous(), **kwargs) if compare_to == "previous" else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return envelope(window, bucket_label, current, previous)


# --- identity + principals ----------------------------------------------------------


class PrincipalIn(BaseModel):
    email: str = Field(min_length=3, pattern=r"^[^@\s]+@[^@\s]+$")
    role: Literal["leadership", "engineer", "admin"]


def build_identity_router(settings: Settings) -> APIRouter:
    router = APIRouter(route_class=RedactingRoute)
    bootstrap = set(settings.admin_emails)

    @router.get("/v1/me")
    def me(principal: Principal = Depends(auth.current_principal)) -> dict:
        return {
            "email": principal.email,
            "role": principal.role,
            "landing": LANDING[principal.role],
            "via": principal.via,
            # Improvement loop availability (design D14); details at /v1/proposals/status.
            "features": {
                "analyst": "disabled" if job.disabled_reason(settings) else "enabled",
                "pr_creation": "not_configured" if github.mode(settings) == "disabled" else github.mode(settings),
            },
        }

    def list_users(request: Request, principal: Principal = Depends(require_role("admin"))) -> dict:
        return {
            "users": auth.list_principals(_engine(request)),
            "bootstrap_admins": sorted(bootstrap),
            "roles": list(ROLES),
        }

    def add_user(body: PrincipalIn, request: Request,
                 principal: Principal = Depends(require_role("admin"))) -> dict:
        email = body.email.strip().lower()
        if email in bootstrap:
            raise HTTPException(status_code=409, detail="Bootstrap admins are managed by ADMIN_EMAILS")
        return auth.upsert_principal(_engine(request), email=email, role=body.role, actor=principal)

    def remove_user(email: str, request: Request,
                    principal: Principal = Depends(require_role("admin"))) -> Response:
        email = email.strip().lower()
        if email in bootstrap:
            raise HTTPException(status_code=409, detail="Bootstrap admins are managed by ADMIN_EMAILS")
        if not auth.delete_principal(_engine(request), email=email, actor=principal):
            raise HTTPException(status_code=404, detail="No such principal")
        return Response(status_code=204)

    for prefix in ("/v1/settings/users", "/v1/principals"):  # task 3.1 path and the spec's alias
        router.add_api_route(prefix, list_users, methods=["GET"])
        router.add_api_route(prefix, add_user, methods=["POST"], status_code=201)
        router.add_api_route(prefix + "/{email}", remove_user, methods=["DELETE"], status_code=204)

    return router


# --- analytics -----------------------------------------------------------------------


def build_analytics_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/v1/analytics", route_class=RedactingRoute,
                       dependencies=[Depends(require_role("leadership"))])

    @router.get("/adoption")
    def adoption(
        request: Request,
        ts_from: datetime | None = Query(default=None, alias="from"),
        ts_to: datetime | None = Query(default=None, alias="to"),
        compare_to: Compare = None,
        bucket: Bucket = "day",
        kind: Literal["skill", "agent"] | None = None,
        name: str | None = None,
    ) -> dict:
        return _series(request, analytics.adoption, _window(ts_from, ts_to), compare_to, bucket,
                       bucket=bucket, kind=kind, name=name)

    @router.get("/outcomes")
    def outcomes(
        request: Request,
        ts_from: datetime | None = Query(default=None, alias="from"),
        ts_to: datetime | None = Query(default=None, alias="to"),
        compare_to: Compare = None,
        bucket: Bucket = "day",
        skill_name: str | None = None,
    ) -> dict:
        return _series(request, analytics.outcomes, _window(ts_from, ts_to), compare_to, bucket,
                       bucket=bucket, skill_name=skill_name)

    @router.get("/latency")
    def latency(
        request: Request,
        ts_from: datetime | None = Query(default=None, alias="from"),
        ts_to: datetime | None = Query(default=None, alias="to"),
        compare_to: Compare = None,
        bucket: Bucket = "day",
        kind: Literal["skill", "tool"] | None = None,
        name: str | None = None,
    ) -> dict:
        return _series(request, analytics.latency, _window(ts_from, ts_to), compare_to, bucket,
                       bucket=bucket, kind=kind, name=name)

    @router.get("/cost")
    def cost(
        request: Request,
        ts_from: datetime | None = Query(default=None, alias="from"),
        ts_to: datetime | None = Query(default=None, alias="to"),
        compare_to: Compare = None,
        bucket: Bucket = "day",
        group_by: Literal["none", "model", "skill", "agent"] = "none",
        model: str | None = None,
        skill_name: str | None = None,
        agent_name: str | None = None,
    ) -> dict:
        return _series(request, analytics.cost, _window(ts_from, ts_to), compare_to, bucket,
                       bucket=bucket, group_by=group_by, model=model, skill_name=skill_name,
                       agent_name=agent_name)

    @router.get("/context-bloat")
    def context_bloat(
        request: Request,
        ts_from: datetime | None = Query(default=None, alias="from"),
        ts_to: datetime | None = Query(default=None, alias="to"),
        compare_to: Compare = None,
        bucket: Bucket = "day",
        project_key: str | None = None,
    ) -> dict:
        return _series(request, analytics.context_bloat, _window(ts_from, ts_to), compare_to, bucket,
                       bucket=bucket, project_key=project_key)

    @router.get("/sentiment")
    def sentiment(
        request: Request,
        ts_from: datetime | None = Query(default=None, alias="from"),
        ts_to: datetime | None = Query(default=None, alias="to"),
        compare_to: Compare = None,
        bucket: Bucket = "day",
        skill_name: str | None = None,
    ) -> dict:
        return _series(request, analytics.sentiment, _window(ts_from, ts_to), compare_to, bucket,
                       bucket=bucket, skill_name=skill_name)

    @router.get("/themes")
    def themes(
        request: Request,
        ts_from: datetime | None = Query(default=None, alias="from"),
        ts_to: datetime | None = Query(default=None, alias="to"),
        compare_to: Compare = None,
        top: int = Query(default=20, ge=1, le=50),
        principal: Principal = Depends(auth.current_principal),
    ) -> dict:
        body = _series(request, analytics.themes, _window(ts_from, ts_to), compare_to, "week", top=top)
        if principal.redacted:  # leadership sees label and count only (analytics-aggregations spec)
            for part in (body["current"], body["previous"] or {}):
                for row in part.get("series", []) + part.get("top", []):
                    row.pop("example_event_ids", None)
        return body

    @router.get("/sessions")
    def sessions(
        request: Request,
        ts_from: datetime | None = Query(default=None, alias="from"),
        ts_to: datetime | None = Query(default=None, alias="to"),
        project_key: str | None = None,
        upn: str | None = None,
        q: str | None = None,
        order: Literal["started", "cost", "turns", "corrections", "tokens"] = "started",
        limit: int = Query(default=50, ge=1),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        window = _window(ts_from, ts_to)
        body = analytics.sessions(_engine(request), window, project_key=project_key, upn=upn, q=q,
                                  order=order, limit=min(limit, settings.max_page_size), offset=offset)
        return {**window.as_dict(), **body}

    @router.get("/sessions/{session_id}/timeline")
    def session_timeline(session_id: str, request: Request) -> dict:
        body = analytics.session_timeline(_engine(request), session_id)
        if body is None:
            raise HTTPException(status_code=404, detail="No such session")
        return body

    @router.get("/artifacts")
    def artifacts(
        request: Request,
        ts_from: datetime | None = Query(default=None, alias="from"),
        ts_to: datetime | None = Query(default=None, alias="to"),
        kind: Literal["skill", "hook", "agent", "command", "instructions", "rule", "plugin"] | None = None,
        q: str | None = None,
        never_invoked: bool | None = None,
        order: Literal["invocations", "name", "cost", "always_on", "last_seen"] = "invocations",
        limit: int = Query(default=100, ge=1),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        window = _window(ts_from, ts_to)
        body = analytics.artifacts(
            _engine(request), window, kind=kind, q=q, never_invoked=never_invoked,
            limit=min(limit, settings.max_page_size), offset=offset, order=order,
        )
        return {**window.as_dict(), **body}

    @router.get("/artifacts/{artifact_id}")
    def artifact(
        artifact_id: str,
        request: Request,
        ts_from: datetime | None = Query(default=None, alias="from"),
        ts_to: datetime | None = Query(default=None, alias="to"),
        compare_to: Compare = None,
    ) -> dict:
        window = _window(ts_from, ts_to)
        engine = _engine(request)
        body = analytics.artifact_detail(engine, artifact_id, window)
        if body is None:
            raise HTTPException(status_code=404, detail="No such artifact")
        previous = None
        if compare_to == "previous":
            prev = analytics.artifact_detail(engine, artifact_id, window.previous())
            previous = {"artifact": prev["artifact"], "series": prev["series"]} if prev else None
        current = {"artifact": body["artifact"], "series": body["series"]}
        out = envelope(window, "day", current, previous, versions=body["versions"])
        if previous is not None:
            metric_keys = ("invocations", "outcomes", "success", "failure", "user_corrected",
                           "cost_usd", "tokens", "api_requests", "sessions_loaded")
            cur_m = {k: body["artifact"].get(k) or 0 for k in metric_keys}
            prev_m = {k: previous["artifact"].get(k) or 0 for k in metric_keys}
            out["deltas"] = deltas(cur_m, prev_m)
        return out

    return router


# --- export ------------------------------------------------------------------------------


def build_export_router(settings: Settings) -> APIRouter:
    router = APIRouter(route_class=RedactingRoute)

    @router.get("/v1/export/{dataset}")
    def export_dataset(
        dataset: str,
        request: Request,
        ts_from: datetime | None = Query(default=None, alias="from"),
        ts_to: datetime | None = Query(default=None, alias="to"),
        format: Literal["ndjson", "parquet"] = "ndjson",
        redact: bool = False,
        principal: Principal = Depends(require_role("engineer")),
    ):
        if dataset not in export.DATASETS:
            raise HTTPException(status_code=404, detail=f"Unknown dataset; one of {sorted(export.DATASETS)}")
        if format == "parquet" and not export.parquet_available():
            raise HTTPException(status_code=501, detail="Parquet export needs the 'parquet' extra (pyarrow)")
        run = export.Export(
            _engine(request), dataset=dataset, window=_window(ts_from, ts_to), fmt=format,
            principal=principal, redact_rows=redact or principal.redacted,
            max_rows=settings.export_max_rows, batch_rows=settings.export_batch_rows,
        )
        return StreamingResponse(run.stream(), media_type=run.media_type, headers=run.headers())

    return router


# --- internal ------------------------------------------------------------------------------


def build_internal_router() -> APIRouter:
    router = APIRouter(route_class=RedactingRoute)

    @router.post("/internal/refresh")
    async def refresh(
        request: Request,
        retention: bool = False,
        principal: Principal = Depends(require_role("admin")),
    ) -> dict:
        return await request.app.state.scheduler.run_tick(force_retention=retention, actor=principal)

    return router
