"""Transcript ingestion: ``POST /v1/telemetry/transcripts`` (design D8, task 2.6).

The client (``python3 -m ouroboros_client.transcript``) parses a Claude Code
transcript into per-line metadata records and uploads them here in batches of
at most 500 lines, under the same ``Telemetry.Write`` bearer as the other
ingestion endpoints. Writes are idempotent on ``(session_id, line_uuid)``;
replaying a batch reports the lines as ``duplicates``.

Beyond ``transcript_lines`` the batch converges, with fill-nulls-only upserts
(design D9), into:

- ``turns`` by ``prompt_id``: ``model``, ``response_length`` (text chars of the
  assistant lines), ``stopped_at`` (timestamp of the last assistant line in
  chain order), ``submitted_at`` / ``prompt_length`` from the human prompt line.
- ``subagent_runs`` by ``agent_id`` (subagent transcripts only): token totals,
  tool-use count, ``started_at`` / ``stopped_at`` and the client-computed
  ``transcript_hash``.

``content`` (sensitive class) is stored only when the batch says
``content_included`` and is otherwise dropped even if a line carries it; the
server never derives text from metadata. No age window applies: uploads are
explicit operator actions bounded client-side by the local 30-day transcript
retention (``cleanupPeriodDays``), and the retention job governs lifetime.
"""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine
from uuid import UUID

from . import db
from . import projection as pj
from .auth import AuthContext, require_auth

router = APIRouter(tags=["transcripts"])

MAX_LINES = 500
LineType = Literal["user", "assistant", "system", "attachment"]


class TranscriptUsage(BaseModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_creation_tokens: int | None = Field(default=None, ge=0)
    thinking_tokens: int | None = Field(default=None, ge=0)


class TranscriptLine(BaseModel):
    line_uuid: str = Field(min_length=1, max_length=128)
    parent_uuid: str | None = Field(default=None, max_length=128)
    seq: int | None = Field(default=None, ge=0)
    type: LineType
    subtype: str | None = Field(default=None, max_length=128)
    prompt_id: str | None = Field(default=None, max_length=128)
    role: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=128)
    message_id: str | None = Field(default=None, max_length=128)
    request_id: str | None = Field(default=None, max_length=128)
    stop_reason: str | None = Field(default=None, max_length=64)
    timestamp: AwareDatetime
    usage: TranscriptUsage | None = None
    tool_use_ids: list[str] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)
    content_chars: int | None = Field(default=None, ge=0)
    is_sidechain: bool = False
    is_error: bool | None = None
    attribution_skill: str | None = Field(default=None, max_length=256)
    version: str | None = Field(default=None, max_length=64)
    # SENSITIVE: message text; stored only when the batch has content_included.
    content: str | None = None


class ClientInfo(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)


class TranscriptBatch(BaseModel):
    session_id: UUID
    agent_id: str | None = Field(default=None, max_length=64)
    lines: list[TranscriptLine] = Field(min_length=1, max_length=MAX_LINES)
    content_included: bool = False
    transcript_hash: str | None = Field(default=None, max_length=128)
    client: ClientInfo


# --- SQL -------------------------------------------------------------------------

UPSERT_SESSION_STUB = text(
    """
    INSERT INTO sessions (session_id, auth_oid, auth_upn)
    VALUES (:session_id, :auth_oid, :auth_upn)
    ON CONFLICT (session_id) DO NOTHING
    """
)

UPSERT_LINE = text(
    """
    INSERT INTO transcript_lines (
        session_id, agent_id, line_uuid, parent_uuid, seq, type, subtype, prompt_id,
        role, model, message_id, request_id, stop_reason, timestamp,
        input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, thinking_tokens,
        tool_use_ids, tool_names, content_chars, is_sidechain, is_error,
        attribution_skill, client_version, content, auth_oid, auth_upn, raw
    ) VALUES (
        :session_id, :agent_id, :line_uuid, :parent_uuid, :seq, :type, :subtype, :prompt_id,
        :role, :model, :message_id, :request_id, :stop_reason, :timestamp,
        :input_tokens, :output_tokens, :cache_read_tokens, :cache_creation_tokens, :thinking_tokens,
        :tool_use_ids, :tool_names, :content_chars, :is_sidechain, :is_error,
        :attribution_skill, :client_version, :content, :auth_oid, :auth_upn, CAST(:raw AS JSONB)
    )
    ON CONFLICT (session_id, line_uuid) DO UPDATE SET
        content   = COALESCE(transcript_lines.content, EXCLUDED.content),
        prompt_id = COALESCE(transcript_lines.prompt_id, EXCLUDED.prompt_id),
        seq       = COALESCE(transcript_lines.seq, EXCLUDED.seq)
    RETURNING (xmax = 0) AS inserted
    """
)

# Fill-nulls-only (design D9): a turn created from hooks keeps what it has.
UPSERT_TURN = text(
    """
    INSERT INTO turns (
        session_id, prompt_id, submitted_at, stopped_at, prompt_length,
        response_length, model, auth_oid, auth_upn, raw
    ) VALUES (
        :session_id, :prompt_id, :submitted_at, :stopped_at, :prompt_length,
        :response_length, :model, :auth_oid, :auth_upn, CAST(:raw AS JSONB)
    )
    ON CONFLICT (prompt_id) DO UPDATE SET
        submitted_at    = COALESCE(turns.submitted_at, EXCLUDED.submitted_at),
        stopped_at      = COALESCE(turns.stopped_at, EXCLUDED.stopped_at),
        prompt_length   = COALESCE(turns.prompt_length, EXCLUDED.prompt_length),
        response_length = COALESCE(turns.response_length, EXCLUDED.response_length),
        model           = COALESCE(turns.model, EXCLUDED.model),
        raw             = turns.raw || EXCLUDED.raw
    """
)

UPSERT_SUBAGENT_RUN = text(
    """
    INSERT INTO subagent_runs (
        agent_id, session_id, prompt_id, started_at, stopped_at, total_tokens,
        total_tool_uses, transcript_hash, auth_oid, auth_upn, raw
    ) VALUES (
        :agent_id, :session_id, :prompt_id, :started_at, :stopped_at, :total_tokens,
        :total_tool_uses, :transcript_hash, :auth_oid, :auth_upn, CAST(:raw AS JSONB)
    )
    ON CONFLICT (agent_id) DO UPDATE SET
        prompt_id       = COALESCE(subagent_runs.prompt_id, EXCLUDED.prompt_id),
        started_at      = COALESCE(subagent_runs.started_at, EXCLUDED.started_at),
        stopped_at      = COALESCE(subagent_runs.stopped_at, EXCLUDED.stopped_at),
        total_tokens    = COALESCE(subagent_runs.total_tokens, EXCLUDED.total_tokens),
        total_tool_uses = COALESCE(subagent_runs.total_tool_uses, EXCLUDED.total_tool_uses),
        transcript_hash = COALESCE(subagent_runs.transcript_hash, EXCLUDED.transcript_hash),
        raw             = subagent_runs.raw || EXCLUDED.raw
    """
)


# --- projection ------------------------------------------------------------------

def _synthetic(model: str | None) -> bool:
    return model is None or model.startswith("<")


def _line_row(batch: TranscriptBatch, line: TranscriptLine, identity: AuthContext) -> dict:
    usage = line.usage or TranscriptUsage()
    # `raw` keeps the metadata record as uploaded (never the content) so a later
    # parser fix can be replayed server-side.
    raw = line.model_dump(exclude_none=True, exclude={"content"}, mode="json")
    return {
        "session_id": str(batch.session_id),
        "agent_id": batch.agent_id,
        "line_uuid": line.line_uuid,
        "parent_uuid": line.parent_uuid,
        "seq": line.seq,
        "type": line.type,
        "subtype": line.subtype,
        "prompt_id": line.prompt_id,
        "role": line.role,
        "model": line.model,
        "message_id": line.message_id,
        "request_id": line.request_id,
        "stop_reason": line.stop_reason,
        "timestamp": line.timestamp,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_creation_tokens": usage.cache_creation_tokens,
        "thinking_tokens": usage.thinking_tokens,
        "tool_use_ids": list(line.tool_use_ids),
        "tool_names": list(line.tool_names),
        "content_chars": line.content_chars,
        "is_sidechain": line.is_sidechain or batch.agent_id is not None,
        "is_error": line.is_error,
        "attribution_skill": line.attribution_skill,
        "client_version": line.version,
        "content": line.content if batch.content_included else None,
        "auth_oid": identity.oid,
        "auth_upn": identity.upn,
        "raw": json.dumps(raw),
    }


def _chain_key(line: TranscriptLine) -> tuple:
    # Chain order first, timestamp as a tie-break only (clocks can step back).
    return (line.seq if line.seq is not None else 1 << 30, line.timestamp)


def turn_rows(batch: TranscriptBatch, identity: AuthContext) -> list[dict]:
    """Per-prompt_id convergence rows for ``turns`` from a main-session batch."""
    if batch.agent_id is not None:
        return []  # subagent lines carry the parent's prompt_id; they are not the turn
    by_prompt: dict[str, list[TranscriptLine]] = {}
    for line in batch.lines:
        if line.prompt_id:
            by_prompt.setdefault(line.prompt_id, []).append(line)

    rows = []
    for prompt_id, lines in by_prompt.items():
        lines.sort(key=_chain_key)
        human = [
            l for l in lines
            if l.type == "user" and not l.tool_use_ids and l.subtype not in ("compact_summary", "meta")
        ]
        assistant = [l for l in lines if l.type == "assistant"]
        model = next((l.model for l in assistant if not _synthetic(l.model)), None)
        text_lines = [l for l in assistant if not l.tool_use_ids and l.content_chars is not None]
        response_length = sum(l.content_chars or 0 for l in text_lines) if text_lines else None
        rows.append(
            {
                "session_id": str(batch.session_id),
                "prompt_id": prompt_id,
                "submitted_at": human[0].timestamp if human else None,
                "stopped_at": assistant[-1].timestamp if assistant else None,
                "prompt_length": human[0].content_chars if human else None,
                "response_length": response_length,
                "model": model,
                "auth_oid": identity.oid,
                "auth_upn": identity.upn,
                "raw": json.dumps(
                    {"transcript": {"lines": len(lines), "assistant_lines": len(assistant)}}
                ),
            }
        )
    return rows


def subagent_row(batch: TranscriptBatch, identity: AuthContext) -> dict | None:
    if batch.agent_id is None:
        return None
    lines = sorted(batch.lines, key=_chain_key)
    usage = [l.usage for l in lines if l.usage is not None]
    total_tokens = (
        sum(
            (u.input_tokens or 0) + (u.output_tokens or 0)
            + (u.cache_read_tokens or 0) + (u.cache_creation_tokens or 0)
            for u in usage
        )
        if usage
        else None
    )
    tool_uses = sum(len(l.tool_use_ids) for l in lines if l.type == "assistant")
    prompt_id = next((l.prompt_id for l in lines if l.prompt_id), None)
    return {
        "agent_id": batch.agent_id,
        "session_id": str(batch.session_id),
        "prompt_id": prompt_id,
        "started_at": min(l.timestamp for l in lines),
        "stopped_at": max(l.timestamp for l in lines),
        "total_tokens": total_tokens,
        "total_tool_uses": tool_uses or None,
        "transcript_hash": batch.transcript_hash,
        "auth_oid": identity.oid,
        "auth_upn": identity.upn,
        "raw": json.dumps({"transcript": {"lines": len(lines)}}),
    }


def ingest_transcript_batch(
    engine: Engine, batch: TranscriptBatch, identity: AuthContext
) -> tuple[int, int]:
    """Store the batch; return ``(accepted, duplicates)``."""
    rows = [_line_row(batch, line, identity) for line in batch.lines]
    turns = turn_rows(batch, identity)
    subagent = subagent_row(batch, identity)
    inserted = 0
    with engine.begin() as conn:
        conn.execute(
            UPSERT_SESSION_STUB,
            {"session_id": str(batch.session_id), "auth_oid": identity.oid, "auth_upn": identity.upn},
        )
        for row in rows:  # RETURNING per row: executemany cannot return
            if conn.execute(UPSERT_LINE, row).scalar_one():
                inserted += 1
        if turns:
            conn.execute(UPSERT_TURN, turns)
            for turn in turns:
                pj.link_outcomes_to_turn(conn, turn["prompt_id"])
        if subagent is not None:
            conn.execute(UPSERT_SUBAGENT_RUN, subagent)
    return inserted, len(rows) - inserted


@router.post("/v1/telemetry/transcripts", status_code=202)
def ingest_transcripts(
    batch: TranscriptBatch,
    request: Request,
    identity: AuthContext = Depends(require_auth),
) -> dict:
    state = request.app.state
    if not state.schema_applied:
        db.apply_schema(state.engine, state.settings.migrations_dir)
        state.schema_applied = True
    accepted, duplicates = ingest_transcript_batch(state.engine, batch, identity)
    return {"accepted": accepted, "duplicates": duplicates}
