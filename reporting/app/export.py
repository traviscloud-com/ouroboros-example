"""Modeling export (design D12, task 3.5): ``GET /v1/export/{dataset}``.

Streams a dataset for an event-time range as NDJSON (always available) or
Parquet (when the optional ``pyarrow`` extra - ``ouroboros-reporting[parquet]``
- is installed). Sensitive text is included only for ``engineer``+ and can be
forced off with ``redact=1``; ``raw`` projection copies and tsvector columns
are never exported. Rows are capped at ``EXPORT_MAX_ROWS`` (the response says
so in ``X-Export-Truncated``) and every export writes an ``audit_log`` row.
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .auth import Principal, write_audit
from .queries import Window
from .redaction import SENSITIVE_KEYS, redact

log = logging.getLogger("ouroboros.reporting.export")

# dataset -> (event-time column expression, ordering)
DATASETS: dict[str, dict] = {
    "sessions": {
        "columns": (
            "session_id, started_at, ended_at, source, model_initial, agent_type, cwd_hash, project_key, "
            "permission_mode, effort, title_redacted, end_reason, context_tokens_at_start, client_version, "
            "auth_oid, auth_upn, received_at"
        ),
        "time": "coalesce(started_at, received_at)",
        "order": "coalesce(started_at, received_at), session_id",
    },
    "turns": {
        "columns": (
            "turn_id, session_id, prompt_id, seq, submitted_at, stopped_at, prompt_length, prompt_text, "
            "response_length, response_text, model, expansion_type, command_name, command_source, "
            "auth_oid, auth_upn, received_at"
        ),
        "time": "coalesce(submitted_at, received_at)",
        "order": "coalesce(submitted_at, received_at), turn_id",
    },
    "tool_calls": {
        "columns": (
            "tool_use_id, session_id, prompt_id, agent_id, tool_name, mcp_server, started_at, duration_ms, "
            "success, is_interrupt, input_bytes, output_bytes, error_class, decision, input_content, "
            "output_content, auth_oid, auth_upn, received_at"
        ),
        "time": "coalesce(started_at, received_at)",
        "order": "coalesce(started_at, received_at), tool_use_id",
    },
    "usage_facts": {
        "columns": (
            "id, session_id, prompt_id, at, kind, model, token_type, value, cost_usd, skill_name, "
            "plugin_name, agent_name, query_source, request_id, attrs, auth_oid, auth_upn, received_at"
        ),
        "time": "at",
        "order": "at, id",
    },
    "events": {
        "columns": (
            "event_id, session_id, client_ts, received_at, skill_name, skill_version, status, model, "
            "latency_ms, error_code, auth_oid, auth_upn, feedback, interaction, prompt_id, turn_id, artifact_id"
        ),
        "time": "client_ts",
        "order": "client_ts, event_id",
    },
    "artifact_versions": {
        "columns": (
            "version_id, artifact_id, content_hash, content_text, frontmatter, estimated_tokens_always_on, "
            "estimated_tokens_body, seen_from, seen_until, commit_sha, auth_oid, auth_upn, received_at"
        ),
        "time": "seen_from",
        "order": "seen_from, version_id",
    },
}
FORMATS = ("ndjson", "parquet")


def parquet_available() -> bool:
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        return False
    return True


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)


def _select(dataset: str, window: Window, cap: int) -> tuple[str, dict]:
    spec = DATASETS[dataset]
    sql = (
        f"SELECT {spec['columns']} FROM {dataset} "
        f"WHERE {spec['time']} >= :start_ts AND {spec['time']} < :end_ts "
        f"ORDER BY {spec['order']} LIMIT :cap"
    )
    return sql, {**window.params(), "cap": cap}


def count_rows(engine: Engine, dataset: str, window: Window) -> int:
    spec = DATASETS[dataset]
    with engine.connect() as conn:
        return conn.execute(
            text(f"SELECT count(*) FROM {dataset} WHERE {spec['time']} >= :start_ts AND {spec['time']} < :end_ts"),
            window.params(),
        ).scalar_one()


class Export:
    """One export run: iterate rows in batches, redact, encode, then audit."""

    def __init__(self, engine: Engine, *, dataset: str, window: Window, fmt: str,
                 principal: Principal, redact_rows: bool, max_rows: int, batch_rows: int = 1000):
        if dataset not in DATASETS:
            raise ValueError(f"unknown dataset {dataset!r}")
        if fmt not in FORMATS:
            raise ValueError(f"format must be one of {FORMATS}")
        self.engine = engine
        self.dataset = dataset
        self.window = window
        self.fmt = fmt
        self.principal = principal
        self.redact_rows = redact_rows
        self.max_rows = max_rows
        self.batch_rows = batch_rows
        self.rows = 0
        self.total = count_rows(engine, dataset, window)

    @property
    def truncated(self) -> bool:
        return self.total > self.max_rows

    @property
    def media_type(self) -> str:
        return "application/x-ndjson" if self.fmt == "ndjson" else "application/vnd.apache.parquet"

    @property
    def filename(self) -> str:
        return f"{self.dataset}_{self.window.start.isoformat()}_{self.window.end.isoformat()}.{self.fmt}"

    def headers(self) -> dict[str, str]:
        return {
            "Content-Disposition": f'attachment; filename="{self.filename}"',
            "X-Export-Total-Rows": str(self.total),
            "X-Export-Row-Cap": str(self.max_rows),
            "X-Export-Truncated": "true" if self.truncated else "false",
            "X-Export-Redacted": "true" if self.redact_rows else "false",
        }

    def _batches(self) -> Iterator[tuple[list[dict], list]]:
        sql, params = _select(self.dataset, self.window, self.max_rows)
        with self.engine.connect().execution_options(stream_results=True, yield_per=self.batch_rows) as conn:
            result = conn.execute(text(sql), params)
            description = result.cursor.description
            yielded = False
            for partition in result.mappings().partitions(self.batch_rows):
                rows = [dict(r) for r in partition]
                if self.redact_rows:
                    rows = [redact(r, keys=SENSITIVE_KEYS) for r in rows]
                self.rows += len(rows)
                yielded = True
                yield rows, description
            if not yielded:  # empty result: callers still need the column description
                yield [], description

    def _audit(self, *, error: str | None = None) -> None:
        try:
            with self.engine.begin() as conn:
                write_audit(
                    conn, self.principal, "export", target_kind="dataset", target_id=self.dataset,
                    details={
                        "from": self.window.start.isoformat(), "to": self.window.end.isoformat(),
                        "format": self.fmt, "redacted": self.redact_rows, "rows": self.rows,
                        "total_rows": self.total, "truncated": self.truncated, "error": error,
                    },
                )
        except Exception:  # never mask the export itself
            log.exception("audit write for export failed")

    def stream(self) -> Iterator[bytes]:
        try:
            if self.fmt == "ndjson":
                yield from self._ndjson()
            else:
                yield from self._parquet()
        except Exception as exc:
            self._audit(error=repr(exc))
            raise
        self._audit()

    def _ndjson(self) -> Iterator[bytes]:
        for rows, _ in self._batches():
            yield "".join(json.dumps(r, default=_json_default, separators=(",", ":")) + "\n" for r in rows).encode()

    def _parquet(self) -> Iterator[bytes]:
        import pyarrow as pa
        import pyarrow.parquet as pq

        sink = io.BytesIO()
        writer = None
        schema = None
        try:
            for rows, description in self._batches():
                if schema is None:
                    schema = _arrow_schema(description, pa)
                    writer = pq.ParquetWriter(sink, schema, compression="zstd")
                if rows:
                    table = pa.Table.from_pylist([_arrow_row(r, schema) for r in rows], schema=schema)
                    writer.write_table(table)
                    yield _drain(sink)
        finally:
            if writer is not None:
                writer.close()
        yield _drain(sink)


def _drain(sink: io.BytesIO) -> bytes:
    data = sink.getvalue()
    sink.seek(0)
    sink.truncate()
    return data


# psycopg type OIDs -> arrow types; everything else is exported as a string.
_OID_INT = {20, 21, 23, 26}
_OID_FLOAT = {700, 701, 1700}
_OID_BOOL = {16}
_OID_TIMESTAMPTZ = {1184}
_OID_TIMESTAMP = {1114}
_OID_DATE = {1082}


def _arrow_schema(description, pa):
    fields = []
    for column in description:
        oid = getattr(column, "type_code", None)
        if oid in _OID_INT:
            typ = pa.int64()
        elif oid in _OID_FLOAT:
            typ = pa.float64()
        elif oid in _OID_BOOL:
            typ = pa.bool_()
        elif oid in _OID_TIMESTAMPTZ:
            typ = pa.timestamp("us", tz="UTC")
        elif oid in _OID_TIMESTAMP:
            typ = pa.timestamp("us")
        elif oid in _OID_DATE:
            typ = pa.date32()
        else:
            typ = pa.string()
        fields.append(pa.field(column.name, typ, nullable=True))
    return pa.schema(fields)


def _arrow_row(row: dict, schema) -> dict:
    import pyarrow as pa

    out = {}
    for field in schema:
        value = row.get(field.name)
        if value is None:
            out[field.name] = None
        elif pa.types.is_string(field.type):
            if isinstance(value, str):
                out[field.name] = value
            elif isinstance(value, (dict, list)):
                out[field.name] = json.dumps(value, default=_json_default)
            else:
                out[field.name] = _json_default(value)
        elif pa.types.is_floating(field.type):
            out[field.name] = float(value)
        else:
            out[field.name] = value
    return out

