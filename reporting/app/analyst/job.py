"""The analyst job (design D14, task 5.2).

For up to ``ANALYST_MAX_ARTIFACTS`` candidate artifacts per run: build the
evidence pack, ask Claude (``ANALYST_MODEL``, default the current Sonnet)
with the fixed system prompt in ``system_prompt.md`` and a JSON-schema
structured output, validate the answer again server-side, and insert an
``improvement_proposals`` row (``status=proposed``, ``created_by=loop``).

Discarded outputs (schema failure, refusal, truncation, wrong target, a diff
that touches other paths or does not apply to the stored ``content_text``)
are logged and counted on the ``analyst_runs`` row, never stored as
proposals. Token usage is logged per call and summed per run.

The job is disabled unless ``ANTHROPIC_API_KEY`` is set; nothing in this
module is imported from the request path unless it is enabled.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..auth import Principal, write_audit
from ..settings import Settings
from . import evidence, patch

log = logging.getLogger("ouroboros.reporting.analyst")

SYSTEM_PROMPT = (Path(__file__).with_name("system_prompt.md")).read_text(encoding="utf-8")

# Models that accept the server-side refusal fallback (`fallbacks: "default"`).
FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
# D14: hooks, agents and instruction files change semantics -> always high.
HIGH_RISK_KINDS = ("hook", "agent", "rule", "instructions")

OUTPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["target", "rationale", "expected_effect", "evidence_ids", "unified_diff", "risk_class"],
    "properties": {
        "target": {
            "type": "object",
            "additionalProperties": False,
            "required": ["artifact_id", "path"],
            "properties": {"artifact_id": {"type": "string"}, "path": {"type": "string"}},
        },
        "rationale": {"type": "string"},
        "expected_effect": {
            "type": "object",
            "additionalProperties": False,
            "required": ["tokens_saved", "correction_rate_delta", "summary"],
            "properties": {
                "tokens_saved": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                "correction_rate_delta": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                "summary": {"type": "string"},
            },
        },
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "unified_diff": {"type": "string"},
        "risk_class": {"type": "string", "enum": ["low", "medium", "high"]},
    },
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Target(_Strict):
    artifact_id: str
    path: str


class ExpectedEffect(_Strict):
    tokens_saved: int | None
    correction_rate_delta: float | None
    summary: str


class AnalystOutput(_Strict):
    """Server-side re-validation of the structured output (the API's schema
    guarantee does not hold on refusal or truncation)."""

    target: Target
    rationale: str
    expected_effect: ExpectedEffect
    evidence_ids: list[str]
    unified_diff: str
    risk_class: Literal["low", "medium", "high"]


class AnalystDisabled(RuntimeError):
    pass


class AnalystBusy(RuntimeError):
    pass


def disabled_reason(settings: Settings) -> str | None:
    if not settings.anthropic_api_key:
        return "ANTHROPIC_API_KEY is not set"
    try:
        import anthropic  # noqa: F401
    except ImportError:  # pragma: no cover - the dependency ships with the image
        return "the anthropic package is not installed"
    return None


def make_client(settings: Settings):
    reason = disabled_reason(settings)
    if reason:
        raise AnalystDisabled(reason)
    import anthropic

    return anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=2, timeout=300.0)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    def add(self, usage) -> dict:
        part = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_read_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            "cache_creation_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        }
        for k, v in part.items():
            setattr(self, k, getattr(self, k) + v)
        return part


@dataclass
class RunResult:
    run_id: str
    trigger: str
    model: str
    artifacts_considered: int = 0
    proposals: list[str] = field(default_factory=list)
    discarded: list[dict] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    status: str = "running"
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "trigger": self.trigger,
            "model": self.model,
            "status": self.status,
            "artifacts_considered": self.artifacts_considered,
            "proposals_created": len(self.proposals),
            "proposal_ids": self.proposals,
            "outputs_discarded": len(self.discarded),
            "discarded": self.discarded,
            "usage": self.usage.__dict__,
            "error": self.error,
        }


class Discard(Exception):
    """One artifact's output is rejected; the run continues."""


def request_params(settings: Settings, pack: dict) -> dict:
    """The Messages API request for one pack. The system prompt is fixed and
    cached (prefix match); the volatile pack comes after the breakpoint."""
    params: dict = {
        "model": settings.analyst_model,
        "max_tokens": settings.analyst_max_tokens,
        "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": json.dumps(pack, sort_keys=True, default=str)}],
        "output_config": {
            "effort": settings.analyst_effort,
            "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
        },
    }
    if settings.analyst_fallbacks and settings.analyst_model in FALLBACK_MODELS:
        params["betas"] = [FALLBACK_BETA]
        params["fallbacks"] = "default"
    return params


def call_claude(client, settings: Settings, pack: dict, usage: Usage) -> tuple[AnalystOutput, dict]:
    import anthropic

    params = request_params(settings, pack)
    started = time.monotonic()
    try:
        response = client.beta.messages.create(**params)
    except anthropic.AuthenticationError:
        raise  # a bad key fails the whole run
    except anthropic.PermissionDeniedError:
        raise
    except anthropic.BadRequestError as exc:
        raise Discard(f"bad_request: {exc.message}") from exc
    except anthropic.RateLimitError as exc:
        raise Discard("rate_limited") from exc
    except anthropic.APIStatusError as exc:
        raise Discard(f"api_error_{exc.status_code}") from exc
    except anthropic.APIConnectionError as exc:
        raise Discard("connection_error") from exc
    part = usage.add(getattr(response, "usage", None))
    meta = {"model": getattr(response, "model", settings.analyst_model),
            "stop_reason": getattr(response, "stop_reason", None),
            "ms": round((time.monotonic() - started) * 1000), **part}
    log.info("analyst call artifact=%s model=%s stop=%s in=%d out=%d cache_read=%d cache_write=%d",
             pack["artifact"]["artifact_id"], meta["model"], meta["stop_reason"], part["input_tokens"],
             part["output_tokens"], part["cache_read_tokens"], part["cache_creation_tokens"])
    if meta["stop_reason"] == "refusal":
        raise Discard("refusal")
    if meta["stop_reason"] == "max_tokens":
        raise Discard("max_tokens")
    raw = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
    if raw is None:
        raise Discard("no_text_block")
    try:
        return AnalystOutput.model_validate(json.loads(raw)), meta
    except (json.JSONDecodeError, ValidationError) as exc:
        raise Discard(f"schema: {str(exc).splitlines()[0][:200]}") from exc


def risk_floor(kind: str, content: str, fp: patch.FilePatch) -> str:
    if kind in HIGH_RISK_KINDS:
        return "high"
    touched = patch.changed_old_lines(fp)
    front = patch.frontmatter_lines(content)
    return "low" if front and touched <= front else "medium"


def validate_output(out: AnalystOutput, pack: dict) -> dict:
    """Checks beyond the schema; returns the row values or raises Discard."""
    art, ver = pack["artifact"], pack["version"]
    if out.target.artifact_id != art["artifact_id"]:
        raise Discard("target artifact_id does not match the pack")
    if not out.unified_diff.strip():
        raise Discard("no_change")
    try:
        fp = patch.single_file_patch(out.unified_diff, art["path"])
        new_text = patch.apply(ver["content_text"], fp)
    except patch.PatchError as exc:
        raise Discard(f"diff: {exc}") from exc
    if new_text == ver["content_text"]:
        raise Discard("diff is a no-op")
    known = {e["event_id"] for e in pack["examples"]}
    evidence_ids = [e for e in dict.fromkeys(out.evidence_ids) if e in known]
    floor = risk_floor(art["kind"], ver["content_text"], fp)
    risk = out.risk_class if RISK_ORDER[out.risk_class] >= RISK_ORDER[floor] else floor
    return {
        "evidence_ids": evidence_ids,
        "dropped_evidence_ids": len(out.evidence_ids) - len(evidence_ids),
        "risk_class": risk,
        "risk_raised_from": out.risk_class if risk != out.risk_class else None,
    }


def _insert_proposal(conn, run_id: str, pack: dict, out: AnalystOutput, checked: dict, meta: dict,
                     sees_sensitive: bool) -> str:
    expected = out.expected_effect.model_dump()
    evidence_doc = {
        "window": pack["window"],
        "metrics": pack["metrics"],
        "examples": [
            {k: e.get(k) for k in ("event_id", "at", "status", "error_code")} for e in pack["examples"]
        ],
        "sees_sensitive": sees_sensitive,
        "model": meta.get("model"),
        "risk_raised_from": checked["risk_raised_from"],
        "dropped_evidence_ids": checked["dropped_evidence_ids"],
        "usage": {k: meta.get(k) for k in ("input_tokens", "output_tokens", "cache_read_tokens",
                                           "cache_creation_tokens")},
    }
    return str(
        conn.execute(
            text(
                "INSERT INTO improvement_proposals (target_artifact_id, base_version_id, rationale, "
                "expected_effect, evidence_event_ids, diff, risk_class, status, created_by, target_path, "
                "analyst_run_id, evidence) VALUES (:aid, :vid, :rationale, CAST(:effect AS JSONB), "
                "CAST(:ids AS UUID[]), :diff, :risk, 'proposed', 'loop', :path, :run, "
                "CAST(:evidence AS JSONB)) RETURNING proposal_id"
            ),
            {
                "aid": pack["artifact"]["artifact_id"],
                "vid": pack["version"]["version_id"],
                "rationale": out.rationale,
                "effect": json.dumps(expected),
                "ids": checked["evidence_ids"],
                "diff": out.unified_diff if out.unified_diff.endswith("\n") else out.unified_diff + "\n",
                "risk": checked["risk_class"],
                "path": pack["artifact"]["path"],
                "run": run_id,
                "evidence": json.dumps(evidence_doc, default=str),
            },
        ).scalar_one()
    )


def run(engine: Engine, settings: Settings, *, client=None, trigger: str = "manual",
        actor: Principal | None = None, now: datetime | None = None) -> dict:
    """One analyst run; returns the run summary (also stored in ``analyst_runs``)."""
    if client is None:
        client = make_client(settings)
    now = now or datetime.now(timezone.utc)
    sees = settings.analyst_sees_sensitive
    with engine.begin() as conn:
        run_id = str(
            conn.execute(
                text("INSERT INTO analyst_runs (trigger, actor_email, model, details) VALUES "
                     "(:t, :a, :m, CAST(:d AS JSONB)) RETURNING run_id"),
                {"t": trigger, "a": actor.email if actor else None, "m": settings.analyst_model,
                 "d": json.dumps({"sees_sensitive": sees, "max_artifacts": settings.analyst_max_artifacts,
                                  "window_days": settings.analyst_window_days})},
            ).scalar_one()
        )
    result = RunResult(run_id=run_id, trigger=trigger, model=settings.analyst_model)
    try:
        targets = evidence.candidates(engine, end=now, window_days=settings.analyst_window_days,
                                      limit=settings.analyst_max_artifacts)
        for artifact in targets:
            pack = evidence.build_pack(engine, artifact, end=now, window_days=settings.analyst_window_days,
                                       max_examples=settings.analyst_max_examples, sees_sensitive=sees)
            if pack is None:
                continue
            result.artifacts_considered += 1
            aid = pack["artifact"]["artifact_id"]
            try:
                out, meta = call_claude(client, settings, pack, result.usage)
                checked = validate_output(out, pack)
            except Discard as exc:
                reason = str(exc)
                log.warning("analyst discarded output for artifact=%s: %s", aid, reason)
                result.discarded.append({"artifact_id": aid, "reason": reason})
                continue
            with engine.begin() as conn:
                pid = _insert_proposal(conn, run_id, pack, out, checked, meta, sees)
                write_audit(conn, actor, "proposal.create", target_kind="proposal", target_id=pid,
                            details={"run_id": run_id, "artifact_id": aid, "risk_class": checked["risk_class"],
                                     "created_by": "loop"})
            result.proposals.append(pid)
        result.status = "succeeded"
    except Exception as exc:
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {str(exc)[:300]}"
        log.exception("analyst run %s failed", run_id)
    finally:
        u = result.usage
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE analyst_runs SET finished_at = now(), status = :status, "
                    "artifacts_considered = :n, proposals_created = :p, outputs_discarded = :d, "
                    "input_tokens = :i, output_tokens = :o, cache_read_tokens = :cr, "
                    "cache_creation_tokens = :cw, error = :err, "
                    "details = details || CAST(:details AS JSONB) WHERE run_id = :run"
                ),
                {"status": result.status, "n": result.artifacts_considered, "p": len(result.proposals),
                 "d": len(result.discarded), "i": u.input_tokens, "o": u.output_tokens,
                 "cr": u.cache_read_tokens, "cw": u.cache_creation_tokens, "err": result.error,
                 "details": json.dumps({"discarded": result.discarded}), "run": run_id},
            )
            write_audit(conn, actor, "analyst.run", target_kind="analyst_run", target_id=run_id,
                        details={k: v for k, v in result.as_dict().items() if k != "discarded"})
        log.info("analyst run %s %s: considered=%d proposals=%d discarded=%d tokens in=%d out=%d "
                 "cache_read=%d cache_write=%d", run_id, result.status, result.artifacts_considered,
                 len(result.proposals), len(result.discarded), u.input_tokens, u.output_tokens,
                 u.cache_read_tokens, u.cache_creation_tokens)
    return result.as_dict()


def last_runs(engine: Engine, limit: int = 5) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT run_id, started_at, finished_at, trigger, actor_email, model, status, "
                "artifacts_considered, proposals_created, outputs_discarded, input_tokens, output_tokens, "
                "cache_read_tokens, cache_creation_tokens, error FROM analyst_runs "
                "ORDER BY started_at DESC LIMIT :limit"
            ),
            {"limit": limit},
        ).mappings().all()
    return [{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in dict(r).items()} for r in rows]


def last_scheduled_at(engine: Engine) -> datetime | None:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT max(started_at) FROM analyst_runs WHERE trigger = 'schedule'")
        ).scalar_one_or_none()
