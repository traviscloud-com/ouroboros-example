"""Order-tolerant writers for the normalized session model (design D9).

Both ingestion paths — hook events (``hook_ingest``) and the OTLP receiver
(``otel``) — project into the same tables through these helpers, so a hook
``SubagentStop`` and an OTel ``subagent_completed`` for one ``agent_id``
converge on a single row whichever arrives first.

Every write is an ``INSERT ... ON CONFLICT (<natural key>) DO UPDATE`` that
fills only NULL columns (``COALESCE(existing, EXCLUDED)``) and merges the
``raw`` JSONB; identity (``auth_oid``/``auth_upn``) and ``received_at`` stay
with the first writer. Rows without a natural id (compactions, instruction
loads, model switches, hook runs, errors, activations, counter facts) get a
deterministic uuid5 derived from their source event so replays upsert instead
of duplicating.

Values are coerced defensively: hook payloads are client JSON and OTel
attributes arrive as strings, so ``as_int``/``as_bool`` accept both forms.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

NAMESPACE = uuid.UUID("3f1c2e6a-7b0d-4b7e-9a21-5c0d7e9f1a2b")

# Redacted names Claude Code substitutes when OTEL_LOG_TOOL_DETAILS is off.
REDACTED_NAMES = frozenset({"custom_skill", "third-party"})

_INT_RE = re.compile(r"^-?\d+$")
INT32_MAX = 2**31 - 1


# ---------------------------------------------------------------- coercion


def as_int(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v == v else None  # NaN guard
    if isinstance(v, str) and _INT_RE.match(v.strip()):
        return int(v.strip())
    return None


def as_int32(v: Any) -> int | None:
    """For INTEGER columns: out-of-range values are dropped rather than failing the row."""
    i = as_int(v)
    if i is None or i > INT32_MAX or i < -INT32_MAX - 1:
        return None
    return i


def as_float(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def as_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes"):
            return True
        if s in ("false", "0", "no"):
            return False
    return None


def as_str(v: Any, limit: int | None = None) -> str | None:
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        s = json.dumps(v, separators=(",", ":"), sort_keys=True)
    else:
        s = str(v)
    if limit is not None and len(s) > limit:
        s = s[:limit]
    return s


def as_uuid(v: Any) -> uuid.UUID | None:
    if isinstance(v, uuid.UUID):
        return v
    if isinstance(v, str):
        try:
            return uuid.UUID(v.strip())
        except ValueError:
            return None
    return None


def first(*values: Any) -> Any:
    """First value that is not None (0 and False count as present)."""
    for v in values:
        if v is not None:
            return v
    return None


def text_length(v: Any) -> int | None:
    return len(v) if isinstance(v, str) else None


def first_line(v: Any, limit: int = 200) -> str | None:
    if not isinstance(v, str) or not v.strip():
        return None
    return v.strip().splitlines()[0][:limit]


def ts_from_nanos(nanos: Any) -> datetime | None:
    n = as_int(nanos)
    if not n:
        return None
    return datetime.fromtimestamp(n / 1e9, tz=timezone.utc)


def derived_id(*parts: Any) -> str:
    """Deterministic uuid5 for rows without a natural id."""
    return str(uuid.uuid5(NAMESPACE, ":".join("" if p is None else str(p) for p in parts)))


def jsonb(obj: Any) -> str:
    return json.dumps(obj, default=str, separators=(",", ":"), sort_keys=True)


# ----------------------------------------------------------------- upserts

_IMMUTABLE = ("auth_oid", "auth_upn", "received_at")


def upsert(
    conn: Connection,
    table: str,
    conflict: tuple[str, ...],
    row: dict[str, Any],
    *,
    jsonb_cols: tuple[str, ...] = ("raw",),
    merge: dict[str, str] | None = None,
    conflict_where: str | None = None,
    returning: str | None = None,
):
    """Insert ``row`` or fill the NULL columns of the existing row.

    ``merge`` overrides the per-column conflict expression (``{col: sql}`` with
    ``t`` aliasing the existing row and ``EXCLUDED`` the new one). Table and
    column names are code constants, never caller input.
    """
    merge = merge or {}
    cols = [c for c in row if row[c] is not None]
    values = ", ".join(f"CAST(:{c} AS JSONB)" if c in jsonb_cols else f":{c}" for c in cols)
    sets = []
    for c in cols:
        if c in conflict or c in _IMMUTABLE:
            continue
        if c in merge:
            sets.append(f"{c} = {merge[c]}")
        elif c in jsonb_cols:
            sets.append(f"{c} = t.{c} || EXCLUDED.{c}")
        else:
            sets.append(f"{c} = COALESCE(t.{c}, EXCLUDED.{c})")
    action = f"DO UPDATE SET {', '.join(sets)}" if sets else "DO NOTHING"
    where = f" {conflict_where}" if conflict_where else ""
    sql = (
        f"INSERT INTO {table} AS t ({', '.join(cols)}) VALUES ({values}) "
        f"ON CONFLICT ({', '.join(conflict)}){where} {action}"
    )
    if returning:
        sql += f" RETURNING {returning}"
    params = {c: (jsonb(row[c]) if c in jsonb_cols else row[c]) for c in cols}
    result = conn.execute(text(sql), params)
    if returning:
        return result.scalar_one_or_none()
    return None


class Identity:
    __slots__ = ("oid", "upn")

    def __init__(self, oid: str, upn: str):
        self.oid = oid
        self.upn = upn

    def stamp(self, row: dict[str, Any]) -> dict[str, Any]:
        row["auth_oid"] = self.oid
        row["auth_upn"] = self.upn
        return row


def ensure_session(
    conn: Connection, ident: Identity, session_id: str, **fields: Any
) -> None:
    """Stub (or enrich) the ``sessions`` row every lifecycle table references."""
    row = ident.stamp({"session_id": session_id, **fields})
    row.setdefault("raw", {})
    upsert(conn, "sessions", ("session_id",), row)


_LINK_OUTCOMES_TO_TURN = text(
    """
    UPDATE events e SET turn_id = t.turn_id
    FROM turns t
    WHERE t.prompt_id = :prompt_id AND e.prompt_id = :prompt_id AND e.turn_id IS NULL
    """
)


def link_outcomes_to_turn(conn: Connection, prompt_id: str | None) -> None:
    """Fill ``events.turn_id`` for outcomes that arrived before their turn."""
    if prompt_id:
        conn.execute(_LINK_OUTCOMES_TO_TURN, {"prompt_id": prompt_id})


def upsert_turn(conn: Connection, ident: Identity, session_id: str, prompt_id: str, **fields):
    row = ident.stamp({"session_id": session_id, "prompt_id": prompt_id, **fields})
    row.setdefault("raw", {})
    if row.get("seq") is None:
        # No client_seq: fall back to arrival order within the session.
        row["seq"] = conn.execute(
            text("SELECT coalesce(max(seq), 0) + 1 FROM turns WHERE session_id = :s"),
            {"s": session_id},
        ).scalar_one()
    # The earliest event of a turn (its UserPromptSubmit) defines its order,
    # whichever event created the row.
    turn_id = upsert(
        conn, "turns", ("prompt_id",), row, merge={"seq": "LEAST(t.seq, EXCLUDED.seq)"}, returning="turn_id"
    )
    # A skill outcome reported for this prompt may have arrived first.
    link_outcomes_to_turn(conn, prompt_id)
    return turn_id


def upsert_tool_call(conn: Connection, ident: Identity, session_id: str, tool_use_id: str, **fields):
    row = ident.stamp({"session_id": session_id, "tool_use_id": tool_use_id, **fields})
    row.setdefault("raw", {})
    upsert(conn, "tool_calls", ("tool_use_id",), row)


def upsert_subagent_run(conn: Connection, ident: Identity, session_id: str, agent_id: str, **fields):
    row = ident.stamp({"session_id": session_id, "agent_id": agent_id, **fields})
    row.setdefault("raw", {})
    upsert(conn, "subagent_runs", ("agent_id",), row)


COMPACTION_WINDOW = timedelta(minutes=15)


def attach_compaction(
    conn: Connection,
    ident: Identity,
    session_id: str,
    at: datetime,
    source: str,
    row_id: str,
    **fields: Any,
) -> str:
    """One ``compactions`` row per compaction, assembled from up to three
    sources (hook ``PreCompact``/``PostCompact`` and OTel ``compaction``).

    A source attaches to the nearest row of the session within
    ``COMPACTION_WINDOW`` that has not yet seen that source (its marker key in
    ``raw``); otherwise it inserts a row with its own deterministic id.
    """
    raw = fields.pop("raw", {})
    existing = conn.execute(
        text(
            "SELECT id FROM compactions WHERE session_id = :s "
            "AND at BETWEEN :lo AND :hi AND NOT (raw ? :marker) "
            "ORDER BY abs(extract(epoch FROM (at - :at))) LIMIT 1"
        ),
        {
            "s": session_id,
            "lo": at - COMPACTION_WINDOW,
            "hi": at + COMPACTION_WINDOW,
            "marker": source,
            "at": at,
        },
    ).scalar_one_or_none()
    row = ident.stamp(
        {"id": str(existing) if existing else row_id, "session_id": session_id, "at": at, **fields}
    )
    row["raw"] = {source: raw}
    upsert(conn, "compactions", ("id",), row)
    return row["id"]


def insert_instructions_loaded(conn: Connection, ident: Identity, row_id: str, **fields):
    row = ident.stamp({"id": row_id, **fields})
    row.setdefault("raw", {})
    upsert(conn, "instructions_loaded", ("id",), row)


def insert_model_switch(conn: Connection, ident: Identity, row_id: str, **fields):
    row = ident.stamp({"id": row_id, **fields})
    row.setdefault("raw", {})
    upsert(conn, "model_switches", ("id",), row)


def insert_turn_error(conn: Connection, ident: Identity, row_id: str, **fields):
    row = ident.stamp({"id": row_id, **fields})
    row.setdefault("raw", {})
    upsert(conn, "turn_errors", ("id",), row)


def insert_hook_run(conn: Connection, ident: Identity, row_id: str, **fields):
    row = ident.stamp({"id": row_id, **fields})
    row.setdefault("raw", {})
    upsert(conn, "hook_runs", ("id",), row)


def skill_activation_id(session_id: str, prompt_id: str | None, skill: str, *fallback: Any) -> str:
    """Hook (PostToolUse Skill) and OTel (skill_activated) rows for one
    activation share this key when prompt_id is known (spike 0.4)."""
    if prompt_id:
        return derived_id("skill_activation", session_id, prompt_id, skill)
    return derived_id("skill_activation", session_id, skill, *fallback)


def insert_skill_activation(conn: Connection, ident: Identity, row_id: str, **fields):
    row = ident.stamp({"id": row_id, **fields})
    row.setdefault("raw", {})
    upsert(conn, "skill_activations", ("id",), row)


def insert_usage_fact(conn: Connection, ident: Identity, row_id: str, **fields):
    """``api_request`` rows are keyed by ``request_id`` (partial unique index);
    everything else by the deterministic ``id``."""
    row = ident.stamp({"id": row_id, **fields})
    row.setdefault("attrs", {})
    if row.get("kind") == "api_request" and row.get("request_id"):
        upsert(
            conn,
            "usage_facts",
            ("request_id",),
            row,
            jsonb_cols=("attrs",),
            conflict_where="WHERE kind = 'api_request' AND request_id IS NOT NULL",
        )
    else:
        upsert(conn, "usage_facts", ("id",), row, jsonb_cols=("attrs",))


_ARTIFACT_MERGE = {
    "first_seen": "LEAST(t.first_seen, EXCLUDED.first_seen)",
    "last_seen": "GREATEST(t.last_seen, EXCLUDED.last_seen)",
}


def upsert_artifact(
    conn: Connection,
    ident: Identity,
    kind: str,
    name: str,
    *,
    owner_repo: str = "",
    path: str = "",
    seen_at: datetime | None = None,
    **fields: Any,
) -> str:
    """Register (or touch) an artifact by its natural key; returns ``artifact_id``."""
    seen_at = seen_at or datetime.now(timezone.utc)
    row = ident.stamp(
        {
            "kind": kind,
            "name": name,
            "owner_repo": owner_repo,
            "path": path,
            "first_seen": seen_at,
            "last_seen": seen_at,
            **fields,
        }
    )
    row.setdefault("raw", {})
    return str(
        upsert(
            conn,
            "artifacts",
            ("kind", "name", "owner_repo", "path"),
            row,
            merge=_ARTIFACT_MERGE,
            returning="artifact_id",
        )
    )


def rename_redacted_plugin(
    conn: Connection, plugin_id_hash: str, kind: str, name: str, owner_repo: str, path: str
) -> str | None:
    """A named ``plugin_loaded`` arriving after redacted ones for the same
    ``plugin_id_hash`` claims the redacted row instead of creating a second
    artifact. Returns the renamed ``artifact_id`` or None when nothing matched
    (or the named row already exists, which the caller then upserts)."""
    return conn.execute(
        text(
            "UPDATE artifacts SET name = :name, owner_repo = :owner_repo, path = :path "
            "WHERE kind = :kind AND plugin_id_hash = :h AND name LIKE 'third-party%' "
            "AND NOT EXISTS (SELECT 1 FROM artifacts WHERE kind = :kind AND name = :name "
            "AND owner_repo = :owner_repo AND path = :path) RETURNING artifact_id"
        ),
        {"name": name, "owner_repo": owner_repo, "path": path, "kind": kind, "h": plugin_id_hash},
    ).scalar_one_or_none()


def upsert_artifact_version(
    conn: Connection,
    ident: Identity,
    artifact_id: str,
    content_hash: str,
    seen_at: datetime | None = None,
    **fields: Any,
) -> str:
    """One row per (artifact, hash); ``seen_until`` advances on every observation."""
    seen_at = seen_at or datetime.now(timezone.utc)
    row = ident.stamp(
        {
            "artifact_id": artifact_id,
            "content_hash": content_hash,
            "seen_from": seen_at,
            "seen_until": seen_at,
            **fields,
        }
    )
    row.setdefault("raw", {})
    return str(
        upsert(
            conn,
            "artifact_versions",
            ("artifact_id", "content_hash"),
            row,
            jsonb_cols=("raw", "frontmatter"),
            merge={
                "seen_from": "LEAST(t.seen_from, EXCLUDED.seen_from)",
                "seen_until": "GREATEST(t.seen_until, EXCLUDED.seen_until)",
            },
            returning="version_id",
        )
    )


def source_from_claude(value: Any) -> str | None:
    """Map Claude Code's ``*.source`` vocabularies onto artifacts.source."""
    s = as_str(value)
    if not s:
        return None
    s = s.lower()
    if "plugin" in s:
        return "plugin"
    if "user" in s:
        return "user"
    if "project" in s or s == "local":
        return "project"
    if "managed" in s or "policy" in s:
        return "managed"
    return None
