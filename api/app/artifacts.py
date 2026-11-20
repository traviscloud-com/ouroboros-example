"""Artifact registry ingestion (design D10): ``POST /v1/telemetry/artifacts``.

Takes what ``python3 -m ouroboros_client.sync_artifacts`` scans (skills,
hooks, agents, commands, instructions, rules, plugin manifests), upserts
``artifacts`` on the natural key ``(kind, name, owner_repo, path)`` and keeps
``artifact_versions`` as a content-hash history: a new hash closes the open
version (``seen_until``) and opens a new one; a repeat of the open hash only
bumps ``last_seen``. Artifact text is ``internal`` class, so ``content_text``
is stored verbatim.
"""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from . import db
from .auth import AuthContext, require_auth

router = APIRouter(tags=["artifacts"])

MAX_BATCH = 100

Kind = Literal["skill", "hook", "agent", "command", "instructions", "rule", "plugin"]
Source = Literal["plugin", "user", "project", "managed"]


class ArtifactIn(BaseModel):
    kind: Kind
    name: str = Field(min_length=1, max_length=512)
    owner_repo: str = Field(default="", max_length=512)
    path: str = Field(default="", max_length=2048)
    source: Source | None = None
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    frontmatter: dict | None = None
    estimated_tokens_always_on: int | None = Field(default=None, ge=0)
    estimated_tokens_body: int | None = Field(default=None, ge=0)
    content_text: str | None = None  # internal class
    commit_sha: str | None = Field(default=None, max_length=64)
    plugin_id_hash: str | None = Field(default=None, max_length=128)
    listing_truncated: bool | None = None
    scoped_by_globs: bool | None = None


class ClientInfo(BaseModel):
    name: str = Field(default="", max_length=128)
    version: str = Field(default="", max_length=64)


class ArtifactBatch(BaseModel):
    artifacts: list[ArtifactIn] = Field(min_length=1, max_length=MAX_BATCH)
    client: ClientInfo | None = None


class SyncResult(BaseModel):
    accepted: int
    unchanged: int
    versions_created: int


UPSERT_ARTIFACT = text(
    """
    INSERT INTO artifacts (
        kind, name, owner_repo, path, source, plugin_id_hash,
        auth_oid, auth_upn, raw
    ) VALUES (
        :kind, :name, :owner_repo, :path, :source, :plugin_id_hash,
        :auth_oid, :auth_upn, CAST(:raw AS JSONB)
    )
    ON CONFLICT (kind, name, owner_repo, path) DO UPDATE SET
        last_seen      = now(),
        source         = COALESCE(EXCLUDED.source, artifacts.source),
        plugin_id_hash = COALESCE(EXCLUDED.plugin_id_hash, artifacts.plugin_id_hash),
        raw            = EXCLUDED.raw
    RETURNING artifact_id
    """
)

OPEN_VERSION = text(
    """
    SELECT version_id, content_hash FROM artifact_versions
    WHERE artifact_id = :artifact_id AND seen_until IS NULL
    ORDER BY seen_from DESC
    LIMIT 1
    """
)

CLOSE_OPEN_VERSIONS = text(
    """
    UPDATE artifact_versions SET seen_until = now()
    WHERE artifact_id = :artifact_id AND seen_until IS NULL
    """
)

# A hash that was seen before (and closed) reopens its row instead of violating
# UNIQUE (artifact_id, content_hash); xmax = 0 tells insert from update.
INSERT_VERSION = text(
    """
    INSERT INTO artifact_versions (
        artifact_id, content_hash, content_text, frontmatter,
        estimated_tokens_always_on, estimated_tokens_body, commit_sha,
        auth_oid, auth_upn, raw
    ) VALUES (
        :artifact_id, :content_hash, :content_text, CAST(:frontmatter AS JSONB),
        :estimated_tokens_always_on, :estimated_tokens_body, :commit_sha,
        :auth_oid, :auth_upn, CAST(:raw AS JSONB)
    )
    ON CONFLICT (artifact_id, content_hash) DO UPDATE SET
        seen_from   = now(),
        seen_until  = NULL,
        content_text = COALESCE(EXCLUDED.content_text, artifact_versions.content_text),
        frontmatter = COALESCE(EXCLUDED.frontmatter, artifact_versions.frontmatter),
        estimated_tokens_always_on = COALESCE(EXCLUDED.estimated_tokens_always_on,
                                              artifact_versions.estimated_tokens_always_on),
        estimated_tokens_body = COALESCE(EXCLUDED.estimated_tokens_body,
                                         artifact_versions.estimated_tokens_body),
        commit_sha  = COALESCE(EXCLUDED.commit_sha, artifact_versions.commit_sha),
        received_at = now(),
        raw         = EXCLUDED.raw
    RETURNING (xmax = 0) AS inserted
    """
)

TOUCH_VERSION = text(
    """
    UPDATE artifact_versions SET
        received_at = now(),
        commit_sha  = COALESCE(artifact_versions.commit_sha, :commit_sha)
    WHERE version_id = :version_id
    """
)


def _raw(item: ArtifactIn, client: ClientInfo | None) -> str:
    payload = item.model_dump(exclude_none=True, exclude={"content_text"})
    if client is not None:
        payload["client"] = client.model_dump()
    return json.dumps(payload)


def sync_artifact(
    conn: Connection, item: ArtifactIn, identity: AuthContext, client: ClientInfo | None
) -> bool:
    """Upsert one artifact; True when a new version row was created."""
    raw = _raw(item, client)
    artifact_id = conn.execute(
        UPSERT_ARTIFACT,
        {
            "kind": item.kind,
            "name": item.name,
            "owner_repo": item.owner_repo,
            "path": item.path,
            "source": item.source,
            "plugin_id_hash": item.plugin_id_hash,
            "auth_oid": identity.oid,
            "auth_upn": identity.upn,
            "raw": raw,
        },
    ).scalar_one()

    current = conn.execute(OPEN_VERSION, {"artifact_id": artifact_id}).first()
    if current is not None and current.content_hash == item.content_hash:
        conn.execute(
            TOUCH_VERSION, {"version_id": current.version_id, "commit_sha": item.commit_sha}
        )
        return False

    conn.execute(CLOSE_OPEN_VERSIONS, {"artifact_id": artifact_id})
    inserted = conn.execute(
        INSERT_VERSION,
        {
            "artifact_id": artifact_id,
            "content_hash": item.content_hash,
            "content_text": item.content_text,
            "frontmatter": json.dumps(item.frontmatter) if item.frontmatter is not None else None,
            "estimated_tokens_always_on": item.estimated_tokens_always_on,
            "estimated_tokens_body": item.estimated_tokens_body,
            "commit_sha": item.commit_sha,
            "auth_oid": identity.oid,
            "auth_upn": identity.upn,
            "raw": raw,
        },
    ).scalar_one()
    return bool(inserted)


def sync_batch(engine: Engine, batch: ArtifactBatch, identity: AuthContext) -> SyncResult:
    created = 0
    with engine.begin() as conn:
        for item in batch.artifacts:
            if sync_artifact(conn, item, identity, batch.client):
                created += 1
    return SyncResult(
        accepted=len(batch.artifacts),
        unchanged=len(batch.artifacts) - created,
        versions_created=created,
    )


@router.post("/v1/telemetry/artifacts", status_code=202, response_model=SyncResult)
def ingest_artifacts(
    batch: ArtifactBatch,
    request: Request,
    identity: AuthContext = Depends(require_auth),
) -> SyncResult:
    state = request.app.state
    if not state.schema_applied:  # same deferred-migration contract as /readyz
        db.apply_schema(state.engine, state.settings.migrations_dir)
        state.schema_applied = True
    return sync_batch(state.engine, batch, identity)
