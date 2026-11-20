"""Proposals API and state machine (design D14, tasks 5.2-5.4).

State machine (every transition writes ``audit_log``)::

    proposed --approve--> approved --PR opened--> pr_opened --merged--> applied
        |                    |                        |
        +--reject--> rejected  +--PR failed--> failed  +--closed unmerged--> failed

Role rules: ``admin`` approves any risk class, ``engineer`` approves ``low``
only, ``leadership`` reads (redacted). Rejecting is engineer+ for any risk
(it only ever keeps the status quo). Nothing transitions past ``proposed``
without a human action recorded in ``audit_log``; there is no auto-approval
path in v1 (the reserved ``AUTO_APPROVE`` setting does not exist yet).

When GitHub is not configured an approval is still recorded and the proposal
stays ``approved`` with the note "approved, PR creation not configured";
``POST /v1/proposals/{id}/open-pr`` (admin) retries once it is.

``POST /v1/proposals/github-webhook`` is the one ``/v1`` path outside the
Gateway login: the auth middleware lets it through and it authenticates the
request itself with the ``GITHUB_WEBHOOK_SECRET`` HMAC.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine
from starlette.concurrency import run_in_threadpool

from . import github as gh
from .analyst import job
from .auth import Principal, current_principal, require_role, write_audit
from .queries import plain
from .redaction import RedactingRoute, marker
from .settings import Settings

log = logging.getLogger("ouroboros.reporting.proposals")

STATES = ("proposed", "approved", "rejected", "pr_opened", "applied", "failed", "superseded")
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "proposed": ("approved", "rejected", "superseded"),
    "approved": ("pr_opened", "failed"),
    "pr_opened": ("applied", "failed"),
    "rejected": (),
    "applied": (),
    "failed": (),
    "superseded": (),
}
WEBHOOK_PATH = "/v1/proposals/github-webhook"
NOTE_PR_NOT_CONFIGURED = "approved, PR creation not configured"


class InvalidTransition(Exception):
    pass


def can_approve(principal: Principal, risk_class: str) -> bool:
    return principal.role == "admin" or (principal.role == "engineer" and risk_class == "low")


# --- reads ---------------------------------------------------------------------------------

_SELECT = """
    SELECT p.proposal_id, p.target_artifact_id, a.name AS target_artifact_name, a.kind AS target_artifact_kind,
           a.owner_repo AS target_owner_repo, coalesce(p.target_path, a.path) AS target_path,
           p.base_version_id, p.rationale, p.expected_effect, p.evidence_event_ids, p.diff, p.risk_class,
           p.status, p.created_by, p.created_at, p.updated_at, p.decided_by, p.decided_at, p.decision_reason,
           p.pr_url, p.pr_repo, p.pr_number, p.pr_branch, p.merge_sha, p.applied_at, p.applied_version_id,
           p.failure_reason, p.analyst_run_id, p.evidence,
           o.before_window, o.after_window, o.metrics_before, o.metrics_after, o.verdict, o.computed_at
      FROM improvement_proposals p
      JOIN artifacts a ON a.artifact_id = p.target_artifact_id
      LEFT JOIN improvement_outcomes o ON o.proposal_id = p.proposal_id
"""


def _range(value) -> dict | None:
    if value is None:
        return None
    lower, upper = getattr(value, "lower", None), getattr(value, "upper", None)
    return {
        "from": lower.isoformat() if lower else None,
        "to": upper.isoformat() if upper else None,
        "days": round((upper - lower).total_seconds() / 86400, 2) if lower and upper else None,
        "bounds": "[)",
    }


def serialize(row: dict, *, pr_mode: str) -> dict:
    row = plain(dict(row))
    out = {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in row.items()
           if k not in ("before_window", "after_window", "metrics_before", "metrics_after", "verdict",
                        "computed_at")}
    out["evidence_event_ids"] = [str(e) for e in (row.get("evidence_event_ids") or [])]
    if row.get("verdict"):
        before = dict(row.get("metrics_before") or {})
        detail = before.pop("_verdict", None)
        out["outcome"] = {
            "proposal_id": out["proposal_id"],
            "before_window": _range(row.get("before_window")),
            "after_window": _range(row.get("after_window")),
            "metrics_before": before,
            "metrics_after": row.get("metrics_after") or {},
            "verdict": row["verdict"],
            "verdict_detail": detail,
            "computed_at": row.get("computed_at"),
        }
    else:
        out["outcome"] = None
    out["status_note"] = status_note(out["status"], pr_mode, out.get("failure_reason"))
    return out


def status_note(status: str, pr_mode: str, failure_reason: str | None = None) -> str | None:
    if status == "approved":
        return NOTE_PR_NOT_CONFIGURED if pr_mode == "disabled" else "approved, PR not opened yet"
    if status == "failed" and failure_reason:
        return f"failed: {failure_reason}"
    return None


def redact_for(principal: Principal, proposal: dict) -> dict:
    """Leadership: the rationale is sensitive when the analyst saw verbatim user
    text (``ANALYST_SEES_SENSITIVE=on``); example feedback keys are handled by
    the RedactingRoute key set."""
    if principal.redacted and (proposal.get("evidence") or {}).get("sees_sensitive"):
        proposal["rationale"] = marker(proposal.get("rationale") or "")
    return proposal


def list_proposals(engine: Engine, *, status: str | None, risk_class: str | None,
                   target_artifact_id: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
    where, params = [], {"limit": limit, "offset": offset}
    if status:
        where.append("p.status = :status")
        params["status"] = status
    if risk_class:
        where.append("p.risk_class = :risk")
        params["risk"] = risk_class
    if target_artifact_id:
        where.append("p.target_artifact_id = CAST(:aid AS UUID)")
        params["aid"] = target_artifact_id
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with engine.connect() as conn:
        total = conn.execute(
            text(f"SELECT count(*) FROM improvement_proposals p {clause}"), params
        ).scalar_one()
        rows = conn.execute(
            text(f"{_SELECT} {clause} ORDER BY p.created_at DESC, p.proposal_id LIMIT :limit OFFSET :offset"),
            params,
        ).mappings().all()
    return [dict(r) for r in rows], total


def get_proposal(conn_or_engine, proposal_id: str, *, for_update: bool = False) -> dict | None:
    try:
        pid = str(uuid.UUID(proposal_id))
    except ValueError:
        return None
    sql = f"{_SELECT} WHERE p.proposal_id = :pid"
    if for_update:
        sql += " FOR UPDATE OF p"
    if isinstance(conn_or_engine, Engine):
        with conn_or_engine.connect() as conn:
            row = conn.execute(text(sql), {"pid": pid}).mappings().one_or_none()
    else:
        row = conn_or_engine.execute(text(sql), {"pid": pid}).mappings().one_or_none()
    return dict(row) if row else None


def audit_trail(engine: Engine, proposal_id: str) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT at, actor_email, actor_role, action, details FROM audit_log "
                "WHERE target_kind = 'proposal' AND target_id = :pid ORDER BY at, id"
            ),
            {"pid": proposal_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def summary(engine: Engine) -> dict:
    with engine.connect() as conn:
        statuses = conn.execute(
            text("SELECT status, count(*) AS n FROM improvement_proposals GROUP BY status")
        ).mappings().all()
        verdicts = conn.execute(
            text("SELECT verdict, count(*) AS n FROM improvement_outcomes GROUP BY verdict")
        ).mappings().all()
        recent = conn.execute(
            text(
                "SELECT o.proposal_id, o.verdict, o.computed_at, a.name AS target_artifact_name, p.risk_class "
                "FROM improvement_outcomes o JOIN improvement_proposals p USING (proposal_id) "
                "JOIN artifacts a ON a.artifact_id = p.target_artifact_id ORDER BY o.computed_at DESC LIMIT 5"
            )
        ).mappings().all()
    return {
        "by_status": {s: 0 for s in STATES} | {r["status"]: r["n"] for r in statuses},
        "verdicts": {v: 0 for v in ("improved", "neutral", "worse", "insufficient_data")}
        | {r["verdict"]: r["n"] for r in verdicts},
        "recent_outcomes": [{**dict(r), "proposal_id": str(r["proposal_id"])} for r in recent],
    }


# --- transitions -------------------------------------------------------------------------------


def transition(conn, proposal: dict, to: str, actor: Principal | None, action: str, *,
               fields: dict | None = None, details: dict | None = None) -> None:
    """Move one proposal (row locked by the caller) and audit it."""
    frm = proposal["status"]
    if to not in TRANSITIONS.get(frm, ()):
        raise InvalidTransition(f"cannot move a proposal from {frm} to {to}")
    fields = dict(fields or {})
    sets = ", ".join(["status = :to", "updated_at = now()"] + [f"{k} = :{k}" for k in fields])
    conn.execute(
        text(f"UPDATE improvement_proposals SET {sets} WHERE proposal_id = :pid AND status = :frm"),
        {"to": to, "frm": frm, "pid": proposal["proposal_id"], **fields},
    )
    write_audit(conn, actor, action, target_kind="proposal", target_id=str(proposal["proposal_id"]),
                details={"from": frm, "to": to, **(details or {})})
    proposal["status"] = to
    proposal.update(fields)


def decide(engine: Engine, proposal_id: str, *, actor: Principal, approve: bool,
           reason: str | None = None) -> dict:
    with engine.begin() as conn:
        p = get_proposal(conn, proposal_id, for_update=True)
        if p is None:
            raise HTTPException(status_code=404, detail="No such proposal")
        if approve and not can_approve(actor, p["risk_class"]):
            raise HTTPException(
                status_code=403,
                detail=f"{actor.role} cannot approve a {p['risk_class']} risk proposal "
                       "(admin: any risk; engineer: low only)",
            )
        if not approve and not actor.at_least("engineer"):
            raise HTTPException(status_code=403, detail="Requires role engineer or higher")
        try:
            transition(
                conn, p, "approved" if approve else "rejected", actor,
                "proposal.approve" if approve else "proposal.reject",
                fields={"decided_by": actor.email, "decided_at": datetime.now(timezone.utc),
                        "decision_reason": reason},
                details={"risk_class": p["risk_class"], "reason": reason},
            )
        except InvalidTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    return p


def open_pr(engine: Engine, settings: Settings, proposal_id: str, *, actor: Principal | None,
            client: gh.GitHub | None) -> dict:
    """approved -> pr_opened | failed. With GitHub disabled, a no-op that
    returns the proposal unchanged (the caller reports the note)."""
    if client is None or not client.enabled:
        with engine.begin() as conn:
            write_audit(conn, actor, "proposal.pr_skipped", target_kind="proposal", target_id=proposal_id,
                        details={"reason": "PR creation not configured"})
        return get_proposal(engine, proposal_id)
    p = get_proposal(engine, proposal_id)
    if p is None:
        raise HTTPException(status_code=404, detail="No such proposal")
    if p["status"] != "approved":
        raise HTTPException(status_code=409, detail=f"cannot open a PR for a proposal in status {p['status']}")
    proposal = serialize(p, pr_mode=client.mode)
    title = f"ouroboros: {p['target_artifact_name']} ({p['risk_class']} risk) - " \
            f"{((p.get('expected_effect') or {}).get('summary') or 'improvement proposal')[:80]}"
    try:
        pr = client.open_pull_request(
            repo=p["target_owner_repo"], path=p["target_path"], diff=p["diff"],
            proposal_id=str(p["proposal_id"]), title=title,
            body=gh.pr_body(proposal, base_url=settings.public_base_url),
        )
    except Exception as exc:  # GitHubError, PatchError, transport errors
        reason = str(exc)[:500]
        log.warning("PR creation for proposal %s failed: %s", proposal_id, reason)
        with engine.begin() as conn:
            locked = get_proposal(conn, proposal_id, for_update=True)
            if locked and locked["status"] == "approved":
                transition(conn, locked, "failed", actor, "proposal.failed",
                           fields={"failure_reason": reason}, details={"stage": "open_pr"})
        return get_proposal(engine, proposal_id)
    with engine.begin() as conn:
        locked = get_proposal(conn, proposal_id, for_update=True)
        transition(conn, locked, "pr_opened", actor, "proposal.pr_opened",
                   fields={"pr_url": pr.url, "pr_repo": pr.repo, "pr_number": pr.number, "pr_branch": pr.branch},
                   details={"pr_url": pr.url, "base": pr.base, "github_mode": client.mode})
    return get_proposal(engine, proposal_id)


def record_pr_result(engine: Engine, *, repo: str, number: int, merged: bool, merge_sha: str | None,
                     merged_at: str | None, actor: Principal, source: str) -> str | None:
    """pr_opened -> applied (merged) | failed (closed unmerged). Returns the proposal id or None."""
    with engine.begin() as conn:
        pid = conn.execute(
            text("SELECT proposal_id FROM improvement_proposals WHERE pr_repo = :r AND pr_number = :n "
                 "AND status = 'pr_opened' FOR UPDATE"),
            {"r": repo, "n": number},
        ).scalar_one_or_none()
        if pid is None:
            return None
        p = get_proposal(conn, str(pid))
        if merged:
            applied_at = datetime.fromisoformat(merged_at.replace("Z", "+00:00")) if merged_at else datetime.now(timezone.utc)
            transition(conn, p, "applied", actor, "proposal.applied",
                       fields={"merge_sha": merge_sha, "applied_at": applied_at},
                       details={"merge_sha": merge_sha, "source": source})
        else:
            transition(conn, p, "failed", actor, "proposal.failed",
                       fields={"failure_reason": "pull request closed without merge"},
                       details={"source": source})
    return str(pid)


def poll_open_prs(engine: Engine, client: gh.GitHub) -> dict:
    """Scheduler fallback for a missed webhook."""
    if client is None or not client.enabled:
        return {"skipped": "PR creation not configured"}
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT pr_repo, pr_number FROM improvement_proposals WHERE status = 'pr_opened' "
                 "AND pr_number IS NOT NULL")
        ).all()
    actor = Principal(email="github-poller", role="admin", via="token")
    updated, errors = 0, 0
    for repo, number in rows:
        try:
            state = client.pull_request_state(repo, number)
        except Exception:
            errors += 1
            log.warning("polling %s#%s failed", repo, number, exc_info=True)
            continue
        if state["merged"] or state["state"] == "closed":
            if record_pr_result(engine, repo=repo, number=number, merged=state["merged"],
                                merge_sha=state["merge_sha"], merged_at=state["merged_at"], actor=actor,
                                source="poll"):
                updated += 1
    return {"checked": len(rows), "updated": updated, "errors": errors}


# --- status ------------------------------------------------------------------------------------


def loop_status(engine: Engine, settings: Settings) -> dict:
    reason = job.disabled_reason(settings)
    try:
        runs = job.last_runs(engine, 5)
    except Exception:
        runs = []
    pr_mode = gh.mode(settings)
    return {
        "analyst": {
            "status": "disabled" if reason else "enabled",
            "reason": reason,
            "model": settings.analyst_model,
            "schedule": settings.analyst_schedule or "",
            "schedule_seconds": settings.analyst_interval_seconds,
            "max_artifacts": settings.analyst_max_artifacts,
            "sees_sensitive": settings.analyst_sees_sensitive,
            "window_days": settings.analyst_window_days,
            "last_runs": runs,
        },
        "github": {
            "status": "disabled" if pr_mode == "disabled" else "enabled",
            "mode": pr_mode,
            "reason": "PR creation not configured" if pr_mode == "disabled" else None,
            "webhook": "enabled" if settings.github_webhook_secret else "disabled",
            "poll_seconds": settings.github_poll_seconds,
        },
        "outcomes": {
            "window_days": settings.outcome_window_days,
            "min_sample": settings.outcome_min_sample,
            "relative_threshold": settings.outcome_relative_threshold,
            "rate_points": settings.outcome_rate_points,
        },
        "auto_approve": "not available in v1",
    }


# --- router --------------------------------------------------------------------------------------


class RejectIn(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/v1/proposals", route_class=RedactingRoute)

    def engine(request: Request) -> Engine:
        return request.app.state.engine

    def github_client(request: Request) -> gh.GitHub | None:
        return getattr(request.app.state, "github", None)

    def pr_mode(request: Request) -> str:
        client = github_client(request)
        return client.mode if client is not None else "disabled"

    def detail(request: Request, proposal_id: str, principal: Principal) -> dict:
        row = get_proposal(engine(request), proposal_id)
        if row is None:
            raise HTTPException(status_code=404, detail="No such proposal")
        out = redact_for(principal, serialize(row, pr_mode=pr_mode(request)))
        out["audit"] = [plain(a) for a in audit_trail(engine(request), out["proposal_id"])]
        return out

    @router.get("")
    def list_route(
        request: Request,
        status: Literal["proposed", "approved", "rejected", "pr_opened", "applied", "failed",
                        "superseded"] | None = None,
        risk_class: Literal["low", "medium", "high"] | None = None,
        target_artifact_id: uuid.UUID | None = None,
        limit: int = Query(default=50, ge=1),
        offset: int = Query(default=0, ge=0),
        principal: Principal = Depends(require_role("leadership")),
    ) -> dict:
        limit = min(limit, settings.max_page_size)
        rows, total = list_proposals(engine(request), status=status, risk_class=risk_class,
                                     target_artifact_id=str(target_artifact_id) if target_artifact_id else None,
                                     limit=limit, offset=offset)
        mode_ = pr_mode(request)
        return {"proposals": [redact_for(principal, serialize(r, pr_mode=mode_)) for r in rows],
                "total": total, "limit": limit, "offset": offset}

    @router.get("/status")
    def status_route(request: Request, principal: Principal = Depends(require_role("leadership"))) -> dict:
        return loop_status(engine(request), settings)

    @router.get("/summary")
    def summary_route(request: Request, principal: Principal = Depends(require_role("leadership"))) -> dict:
        return plain(summary(engine(request)))

    @router.post("/run")
    async def run_route(
        request: Request,
        wait: bool = False,
        principal: Principal = Depends(require_role("admin")),
    ):
        reason = job.disabled_reason(settings)
        if reason:
            raise HTTPException(status_code=503, detail=f"analyst disabled: {reason}")
        scheduler = request.app.state.scheduler
        if scheduler.analyst_lock.locked():
            raise HTTPException(status_code=409, detail="an analyst run is already in progress")
        if wait:
            return await scheduler.run_analyst(trigger="manual", actor=principal)
        task = asyncio.create_task(scheduler.run_analyst(trigger="manual", actor=principal))
        scheduler.background.add(task)
        task.add_done_callback(scheduler.background.discard)
        return JSONResponse(status_code=202, content={"status": "started", "trigger": "manual"})

    @router.post(WEBHOOK_PATH.removeprefix("/v1/proposals"))
    async def webhook_route(request: Request) -> dict:
        secret = settings.github_webhook_secret
        if not secret:
            raise HTTPException(status_code=503, detail="webhook disabled: GITHUB_WEBHOOK_SECRET is not set")
        body = await request.body()
        if not gh.verify_signature(secret, body, request.headers.get("x-hub-signature-256")):
            raise HTTPException(status_code=401, detail="invalid signature")
        event = request.headers.get("x-github-event", "")
        if event == "ping":
            return {"ok": True, "event": "ping"}
        if event != "pull_request":
            return {"ok": True, "ignored": event}
        payload = json.loads(body or b"{}")
        pr = payload.get("pull_request") or {}
        if payload.get("action") != "closed":
            return {"ok": True, "ignored": f"pull_request.{payload.get('action')}"}
        repo = (payload.get("repository") or {}).get("full_name")
        actor = Principal(email="github-webhook", role="admin", via="token")
        pid = await run_in_threadpool(
            record_pr_result, request.app.state.engine, repo=repo, number=int(pr.get("number") or 0),
            merged=bool(pr.get("merged")), merge_sha=pr.get("merge_commit_sha"), merged_at=pr.get("merged_at"),
            actor=actor, source="webhook",
        )
        return {"ok": True, "proposal_id": pid}

    @router.get("/{proposal_id}")
    def get_route(proposal_id: str, request: Request,
                  principal: Principal = Depends(require_role("leadership"))) -> dict:
        return detail(request, proposal_id, principal)

    @router.post("/{proposal_id}/approve")
    def approve_route(proposal_id: str, request: Request,
                      principal: Principal = Depends(current_principal)) -> dict:
        decide(engine(request), proposal_id, actor=principal, approve=True)
        open_pr(engine(request), settings, proposal_id, actor=principal, client=github_client(request))
        return detail(request, proposal_id, principal)

    @router.post("/{proposal_id}/open-pr")
    def open_pr_route(proposal_id: str, request: Request,
                      principal: Principal = Depends(require_role("admin"))) -> dict:
        client = github_client(request)
        if client is None or not client.enabled:
            raise HTTPException(status_code=503, detail="PR creation not configured")
        open_pr(engine(request), settings, proposal_id, actor=principal, client=client)
        return detail(request, proposal_id, principal)

    @router.post("/{proposal_id}/reject")
    def reject_route(proposal_id: str, request: Request, body: RejectIn | None = Body(default=None),
                     principal: Principal = Depends(current_principal)) -> dict:
        decide(engine(request), proposal_id, actor=principal, approve=False,
               reason=(body.reason.strip() or None) if body and body.reason else None)
        return detail(request, proposal_id, principal)

    return router

