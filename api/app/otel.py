"""OTLP/HTTP receiver for Claude Code's built-in telemetry (design D5/D7/D9, task 2.4).

``POST /v1/otel/v1/metrics`` and ``POST /v1/otel/v1/logs`` accept
``application/x-protobuf`` or ``application/json`` (OTLP/JSON), require the
same ``Telemetry.Write`` bearer as ``/v1/telemetry/events``, decode with the
``opentelemetry-proto`` classes, run every record through the attribute
coercion layer (spike 0.2: the event name lives in ``attributes["event.name"]``,
integers arrive as strings, booleans as ``boolValue`` or ``"true"``, counters
are delta sums as ``asDouble``, ``cost_usd_micros`` beats the double
``cost_usd``) and map it onto the normalized tables through ``projection``.

Unmapped metric/event names are counted (``UNMAPPED``) and dropped, never
rejected; the OTLP success response is returned whenever the body decodes.

Privacy: ``anthropic.custom_base_url`` is dropped from every record;
``prompt``/``prompt_text``/``response``/``tool_input``/``tool_parameters`` are
dropped unless the client opts in through the ``X-Ouroboros-Capture`` header
(``prompts`` and/or ``tool_content``, set via ``OTEL_EXPORTER_OTLP_HEADERS``);
``user.id`` is kept only as an opaque correlation hash in ``attrs``.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from google.protobuf import json_format
from google.protobuf.message import DecodeError, Message
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
    ExportLogsServiceResponse,
)
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
    ExportMetricsServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue
from sqlalchemy.engine import Connection, Engine

from . import db
from . import projection as pj
from .auth import AuthContext, require_auth

log = logging.getLogger("ouroboros.otel")
if not log.handlers:  # uvicorn configures only its own loggers; keep the summary visible
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s:     %(name)s %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False
router = APIRouter(prefix="/v1/otel", tags=["otel"])

PROTOBUF = "application/x-protobuf"
JSON = "application/json"
CAPTURE_HEADER = "x-ouroboros-capture"

# Process-wide receiver counters (D7: "count records by event.name and unmapped names").
MAPPED: Counter = Counter()
UNMAPPED: Counter = Counter()

# Always dropped: leaks the configured ANTHROPIC_BASE_URL.
ALWAYS_DROP = frozenset({"anthropic.custom_base_url"})
# Sensitive content attributes, dropped unless the matching capture opt-in applies.
PROMPT_CONTENT = frozenset({"prompt", "prompt_text", "response", "response_text"})
TOOL_CONTENT = frozenset({"tool_input", "tool_parameters", "tool_response", "tool_result"})
# Identifier-like strings that must never be coerced to numbers/bools.
KEEP_AS_STRING = frozenset(
    {
        "session.id", "prompt.id", "request_id", "client_request_id", "message.uuid",
        "user.id", "user.email", "user.account_uuid", "organization.id",
        "plugin_id_hash", "event.timestamp", "event.name", "plugin.version",
        "service.version", "agent_id", "agent.id", "tool_use_id", "model",
    }
)

METRIC_KINDS = {
    "claude_code.token.usage": "token",
    "claude_code.cost.usage": "cost",
    "claude_code.session.count": "session",
    "claude_code.lines_of_code.count": "loc",
    "claude_code.commit.count": "commit",
    "claude_code.pull_request.count": "pr",
    "claude_code.active_time.total": "active_time",
}


# ----------------------------------------------------------------- decoding


def any_value(v: AnyValue):
    kind = v.WhichOneof("value")
    if kind is None:
        return None
    if kind == "array_value":
        return [any_value(x) for x in v.array_value.values]
    if kind == "kvlist_value":
        return {kv.key: any_value(kv.value) for kv in v.kvlist_value.values}
    if kind == "bytes_value":
        return v.bytes_value.hex()
    return getattr(v, kind)


def attrs(kvs) -> dict:
    return {kv.key: any_value(kv.value) for kv in kvs}


def _content_type(request: Request) -> str:
    ct = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if ct not in (PROTOBUF, JSON):
        raise HTTPException(status_code=415, detail=f"Unsupported Content-Type {ct!r}")
    return ct


async def _decode(request: Request, message: Message) -> str:
    ct = _content_type(request)
    body = await request.body()
    try:
        if ct == PROTOBUF:
            message.ParseFromString(body)
        else:
            json_format.Parse(body, message, ignore_unknown_fields=True)
    except (DecodeError, json_format.ParseError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Undecodable OTLP body: {exc}")
    return ct


def _respond(message: Message, ct: str) -> Response:
    if ct == PROTOBUF:
        return Response(content=message.SerializeToString(), media_type=PROTOBUF)
    return Response(content=json_format.MessageToJson(message), media_type=JSON)


def capture_optins(request: Request) -> frozenset[str]:
    raw = request.headers.get(CAPTURE_HEADER, "")
    return frozenset(p.strip().lower().replace("-", "_") for p in raw.split(",") if p.strip())


# ----------------------------------------------------------------- coercion


def coerce_value(key: str, v: Any) -> Any:
    """String ints -> int, "true"/"false" -> bool; identifiers stay strings."""
    if key in KEEP_AS_STRING or not isinstance(v, str):
        return v
    s = v.strip()
    i = pj.as_int(s)
    if i is not None:
        return i
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    return v


def coerce_attributes(raw: dict[str, Any], optins: frozenset[str] = frozenset()) -> dict[str, Any]:
    """The coercion + redaction layer every record passes through before mapping."""
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k in ALWAYS_DROP:
            continue
        if k in PROMPT_CONTENT and "prompts" not in optins:
            continue
        if k in TOOL_CONTENT and "tool_content" not in optins:
            continue
        out[k] = coerce_value(k, v)
    return out


def event_name(rec, a: dict[str, Any]) -> str | None:
    name = a.get("event.name") or rec.event_name or None
    if not name:
        body = any_value(rec.body)
        if isinstance(body, str) and body:
            name = body
    if not name:
        return None
    name = str(name)
    return name[len("claude_code."):] if name.startswith("claude_code.") else name


def record_time(rec, a: dict[str, Any]) -> datetime:
    ts = pj.ts_from_nanos(rec.time_unix_nano) or pj.ts_from_nanos(rec.observed_time_unix_nano)
    if ts is None and isinstance(a.get("event.timestamp"), str):
        try:
            ts = datetime.fromisoformat(a["event.timestamp"].replace("Z", "+00:00"))
        except ValueError:
            ts = None
    return ts or datetime.now(timezone.utc)


def _content(a: dict[str, Any], *keys: str) -> str | None:
    """Content attribute if it survived redaction and is not Claude Code's own placeholder."""
    for k in keys:
        v = a.get(k)
        if isinstance(v, str) and v and v != "<REDACTED>":
            return v
    return None


def _cost(a: dict[str, Any]) -> float | None:
    micros = pj.as_int(a.get("cost_usd_micros"))
    if micros is not None:
        return micros / 1_000_000
    return pj.as_float(a.get("cost_usd"))


# ------------------------------------------------------------------ summary


def summarize_metrics(req: ExportMetricsServiceRequest) -> dict:
    names: Counter = Counter()
    attr_keys: dict[str, set] = {}
    resource_keys: set = set()
    points = 0
    for rm in req.resource_metrics:
        resource_keys |= {kv.key for kv in rm.resource.attributes}
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                data = m.WhichOneof("data")
                dps = getattr(m, data).data_points if data else []
                names[f"{m.name} ({data})"] += len(dps)
                keys = attr_keys.setdefault(m.name, set())
                for dp in dps:
                    points += 1
                    keys |= {kv.key for kv in dp.attributes}
    return {
        "resource_attribute_keys": sorted(resource_keys),
        "metrics": dict(names),
        "attribute_keys": {k: sorted(v) for k, v in attr_keys.items()},
        "data_points": points,
    }


def summarize_logs(req: ExportLogsServiceRequest) -> dict:
    names: Counter = Counter()
    attr_keys: dict[str, set] = {}
    resource_keys: set = set()
    records = 0
    for rl in req.resource_logs:
        resource_keys |= {kv.key for kv in rl.resource.attributes}
        for sl in rl.scope_logs:
            for rec in sl.log_records:
                records += 1
                a = attrs(rec.attributes)
                name = event_name(rec, a) or "<unnamed>"
                names[name] += 1
                attr_keys.setdefault(name, set()).update(a.keys())
    return {
        "resource_attribute_keys": sorted(resource_keys),
        "events": dict(names),
        "attribute_keys": {k: sorted(v) for k, v in attr_keys.items()},
        "records": records,
    }


# ------------------------------------------------------------------ metrics


def ingest_metrics(engine: Engine, req: ExportMetricsServiceRequest, ident: pj.Identity) -> Counter:
    """Delta counters -> ``usage_facts`` reconciliation rows (D9: ``api_request``
    log events are the primary usage fact; counters lack ``prompt.id``)."""
    outcome: Counter = Counter()
    with engine.begin() as conn:
        for rm in req.resource_metrics:
            resource = coerce_attributes(attrs(rm.resource.attributes))
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    kind = METRIC_KINDS.get(m.name)
                    data = m.WhichOneof("data")
                    dps = getattr(m, data).data_points if data in ("sum", "gauge") else []
                    if kind is None or data not in ("sum", "gauge"):
                        UNMAPPED[f"metric:{m.name}"] += len(dps) or 1
                        outcome["unmapped"] += len(dps) or 1
                        continue
                    for dp in dps:
                        if _metric_point(conn, ident, m.name, kind, dp, resource):
                            MAPPED[f"metric:{m.name}"] += 1
                            outcome["mapped"] += 1
                        else:
                            UNMAPPED[f"metric:{m.name}:no_session"] += 1
                            outcome["unmapped"] += 1
    return outcome


def _metric_point(conn: Connection, ident: pj.Identity, name: str, kind: str, dp, resource) -> bool:
    a = coerce_attributes(attrs(dp.attributes))
    session = pj.as_uuid(a.get("session.id"))
    if session is None:
        return False
    sid = str(session)
    which = dp.WhichOneof("value")
    value = pj.as_float(getattr(dp, which)) if which else None
    at = pj.ts_from_nanos(dp.time_unix_nano) or datetime.now(timezone.utc)
    pj.ensure_session(conn, ident, sid, client_version=pj.as_str(resource.get("service.version"), 64))
    extra = {k: v for k, v in a.items() if k not in ("session.id", "model", "query_source")}
    extra["metric"] = name
    if dp.start_time_unix_nano:
        extra["start_time_unix_nano"] = int(dp.start_time_unix_nano)
    pj.insert_usage_fact(
        conn,
        ident,
        pj.derived_id("metric", name, sid, int(dp.time_unix_nano), int(dp.start_time_unix_nano), pj.jsonb(a)),
        session_id=sid,
        at=at,
        kind=kind,
        model=pj.as_str(a.get("model"), 128),
        token_type=pj.as_str(a.get("type"), 32) if kind == "token" else None,
        value=value,
        cost_usd=value if kind == "cost" else None,
        skill_name=pj.as_str(a.get("skill.name"), 200),
        plugin_name=pj.as_str(a.get("plugin.name"), 200),
        agent_name=pj.as_str(a.get("agent.name"), 200),
        query_source=pj.as_str(a.get("query_source"), 32),
        attrs=extra,
    )
    return True


# --------------------------------------------------------------------- logs


def ingest_logs(
    engine: Engine, req: ExportLogsServiceRequest, ident: pj.Identity, optins: frozenset[str] = frozenset()
) -> Counter:
    outcome: Counter = Counter()
    with engine.begin() as conn:
        for rl in req.resource_logs:
            resource = coerce_attributes(attrs(rl.resource.attributes))
            for sl in rl.scope_logs:
                for rec in sl.log_records:
                    raw = attrs(rec.attributes)
                    name = event_name(rec, raw)
                    handler = _LOG_HANDLERS.get(name or "")
                    if handler is None:
                        UNMAPPED[f"event:{name or '<unnamed>'}"] += 1
                        outcome["unmapped"] += 1
                        continue
                    a = coerce_attributes(raw, optins)
                    session = pj.as_uuid(a.get("session.id"))
                    if session is None:
                        UNMAPPED[f"event:{name}:no_session"] += 1
                        outcome["unmapped"] += 1
                        continue
                    sid = str(session)
                    at = record_time(rec, a)
                    pj.ensure_session(
                        conn, ident, sid, client_version=pj.as_str(resource.get("service.version"), 64)
                    )
                    try:
                        with conn.begin_nested():
                            handled = handler(conn, ident, sid, at, a, name)
                    except Exception as exc:  # one bad record never fails the export
                        log.warning("otel event %s not stored: %s", name, exc)
                        handled = False
                    if handled:
                        MAPPED[f"event:{name}"] += 1
                        outcome["mapped"] += 1
                    else:
                        UNMAPPED[f"event:{name}:incomplete"] += 1
                        outcome["unmapped"] += 1
    return outcome


def _seq_id(prefix: str, sid: str, at: datetime, a: dict, *extra) -> str:
    return pj.derived_id(prefix, sid, at.isoformat(), a.get("event.sequence"), *extra)


def _attrs_for_raw(a: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in a.items() if k not in ("session.id", "event.name")}


def _api_request(conn, ident, sid, at, a, name) -> bool:
    tokens = [pj.as_int(a.get(k)) or 0 for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")]
    request_id = pj.as_str(a.get("request_id"), 200)
    pj.insert_usage_fact(
        conn,
        ident,
        _seq_id("api_request", sid, at, a, request_id),
        session_id=sid,
        prompt_id=pj.as_str(a.get("prompt.id"), 200),
        at=at,
        kind="api_request",
        model=pj.as_str(a.get("model"), 128),
        value=sum(tokens),
        cost_usd=_cost(a),
        skill_name=pj.as_str(a.get("skill.name"), 200),
        plugin_name=pj.as_str(a.get("plugin.name"), 200),
        agent_name=pj.as_str(a.get("agent.name"), 200),
        query_source=pj.as_str(a.get("query_source"), 32),
        request_id=request_id,
        attrs=_attrs_for_raw(a),
    )
    return True


def _skill_activated(conn, ident, sid, at, a, name) -> bool:
    skill = pj.as_str(a.get("skill.name"), 200)
    if not skill:
        return False
    artifact_id = None
    if skill not in pj.REDACTED_NAMES:
        artifact_id = pj.upsert_artifact(
            conn,
            ident,
            "skill",
            skill,
            source=pj.source_from_claude(a.get("skill.source")),
            seen_at=at,
        )
    pj.insert_skill_activation(
        conn,
        ident,
        pj.skill_activation_id(sid, pj.as_str(a.get("prompt.id"), 200), skill, at.isoformat(), a.get("event.sequence")),
        session_id=sid,
        prompt_id=pj.as_str(a.get("prompt.id"), 200),
        artifact_id=artifact_id,
        skill_name=skill,
        skill_source=pj.as_str(a.get("skill.source"), 64),
        invocation_trigger=pj.as_str(a.get("invocation_trigger"), 64),
        at=at,
        raw=_attrs_for_raw(a),
    )
    return True


def _user_prompt(conn, ident, sid, at, a, name) -> bool:
    prompt_id = pj.as_str(a.get("prompt.id"), 200)
    if not prompt_id:
        return False
    text = _content(a, "prompt", "prompt_text")
    pj.upsert_turn(
        conn,
        ident,
        sid,
        prompt_id,
        submitted_at=at,
        prompt_length=pj.first(pj.as_int32(a.get("prompt_length")), pj.text_length(text)),
        prompt_text=text,
        raw={"otel_user_prompt": _attrs_for_raw({k: v for k, v in a.items() if k not in PROMPT_CONTENT})},
    )
    return True


def _assistant_response(conn, ident, sid, at, a, name) -> bool:
    prompt_id = pj.as_str(a.get("prompt.id"), 200)
    if not prompt_id:
        return False
    text = _content(a, "response", "response_text")
    pj.upsert_turn(
        conn,
        ident,
        sid,
        prompt_id,
        stopped_at=at,
        response_length=pj.first(pj.as_int32(a.get("response_length")), pj.text_length(text)),
        response_text=text,
        model=pj.as_str(a.get("model"), 128),
        raw={"otel_assistant_response": _attrs_for_raw({k: v for k, v in a.items() if k not in PROMPT_CONTENT})},
    )
    return True


def _tool_event(conn, ident, sid, at, a, name) -> bool:
    tool_use_id = pj.as_str(a.get("tool_use_id"), 200)
    if not tool_use_id:
        return False
    fields: dict[str, Any] = {
        "prompt_id": pj.as_str(a.get("prompt.id"), 200),
        "tool_name": pj.as_str(a.get("tool_name"), 200),
        "mcp_server": pj.as_str(a.get("mcp_server"), 200),
        "raw": {f"otel_{name}": _attrs_for_raw({k: v for k, v in a.items() if k not in TOOL_CONTENT})},
    }
    if name == "tool_decision":
        fields["decision"] = pj.as_str(a.get("decision"), 32)
        fields["started_at"] = at
        content = _content(a, "tool_parameters")
    else:  # tool_result
        duration = pj.as_int32(a.get("duration_ms"))
        fields.update(
            success=pj.as_bool(a.get("success")),
            # Spike 0.2 saw negative values for Bash; hooks remain the latency source.
            duration_ms=duration if duration is not None and duration >= 0 else None,
            input_bytes=pj.as_int32(a.get("tool_input_size_bytes")),
            output_bytes=pj.as_int32(a.get("tool_result_size_bytes")),
            error_class=pj.first_line(a.get("error")),
        )
        content = _content(a, "tool_input", "tool_parameters")
    if content:
        fields["input_content"] = content
    pj.upsert_tool_call(conn, ident, sid, tool_use_id, **fields)
    return True


def _subagent_completed(conn, ident, sid, at, a, name) -> bool:
    agent_id = pj.as_str(a.get("agent_id") or a.get("agent.id"), 200)
    if not agent_id:
        return False
    duration = pj.as_int32(a.get("duration_ms"))
    pj.upsert_subagent_run(
        conn,
        ident,
        sid,
        agent_id,
        prompt_id=pj.as_str(a.get("prompt.id"), 200),
        agent_type=pj.as_str(a.get("agent_type") or a.get("agent.type") or a.get("agent.name"), 128),
        stopped_at=at,
        started_at=(at - timedelta(milliseconds=duration)) if duration else None,
        total_tokens=pj.as_int(a.get("total_tokens")),
        total_tool_uses=pj.as_int32(a.get("total_tool_uses") if "total_tool_uses" in a else a.get("tool_uses")),
        raw={"otel_subagent_completed": _attrs_for_raw(a)},
    )
    return True


def _compaction(conn, ident, sid, at, a, name) -> bool:
    pj.attach_compaction(
        conn,
        ident,
        sid,
        at,
        "otel",
        _seq_id("compaction", sid, at, a),
        prompt_id=pj.as_str(a.get("prompt.id"), 200),
        trigger=pj.as_str(a.get("trigger"), 32),
        pre_tokens=pj.as_int32(a.get("pre_tokens")),
        post_tokens=pj.as_int32(a.get("post_tokens")),
        raw=_attrs_for_raw(a),
    )
    return True


def _api_error(conn, ident, sid, at, a, name) -> bool:
    err = a.get("error")
    pj.insert_turn_error(
        conn,
        ident,
        _seq_id(name, sid, at, a),
        session_id=sid,
        prompt_id=pj.as_str(a.get("prompt.id"), 200),
        kind=name,
        model=pj.as_str(a.get("model"), 128),
        error_code=pj.as_str(a.get("error_code") or a.get("error_type"), 64),
        status_code=pj.as_int32(a.get("status_code")),
        error_class=pj.first_line(err, 120) if isinstance(err, str) else None,
        duration_ms=pj.as_int32(a.get("duration_ms")),
        at=at,
        # `error` may quote request content; keep it out of the row (sensitive class).
        raw=_attrs_for_raw({k: v for k, v in a.items() if k != "error"}),
    )
    return True


def _hook_execution(conn, ident, sid, at, a, name) -> bool:
    hook_event = pj.as_str(a.get("hook_event"), 64)
    if not hook_event:
        return False
    phase = "complete" if name == "hook_execution_complete" else "start"
    num_hooks = pj.as_int32(a.get("num_hooks"))
    num_success = pj.as_int32(a.get("num_success"))
    pj.insert_hook_run(
        conn,
        ident,
        _seq_id("hook_run", sid, at, a, phase, hook_event),
        session_id=sid,
        prompt_id=pj.as_str(a.get("prompt.id"), 200),
        phase=phase,
        hook_event=hook_event,
        hook_name=pj.as_str(a.get("hook_name"), 200),
        hook_source=pj.as_str(a.get("hook_source"), 64),
        plugin_name=pj.as_str(a.get("plugin.name"), 200),
        num_hooks=num_hooks,
        num_success=num_success,
        num_blocking=pj.as_int32(a.get("num_blocking")),
        num_non_blocking_error=pj.as_int32(a.get("num_non_blocking_error")),
        num_cancelled=pj.as_int32(a.get("num_cancelled")),
        duration_ms=pj.as_int32(pj.first(a.get("total_duration_ms"), a.get("duration_ms"))),
        success=(num_success == num_hooks) if phase == "complete" and num_hooks is not None and num_success is not None else None,
        stdout_chars=pj.as_int32(a.get("stdout_chars")),
        additional_context_chars=pj.as_int32(a.get("additional_context_chars")),
        system_message_chars=pj.as_int32(a.get("system_message_chars")),
        initial_user_message_chars=pj.as_int32(a.get("initial_user_message_chars")),
        num_outputs_persisted=pj.as_int32(a.get("num_outputs_persisted")),
        at=at,
        raw=_attrs_for_raw(a),
    )
    return True


def _plugin_identity(a: dict[str, Any]) -> tuple[str, str, str | None, bool]:
    """``(name, owner_repo, plugin_id_hash, redacted)`` for plugin_loaded / hook_registered."""
    plugin = pj.as_str(a.get("plugin.name"), 200)
    id_hash = pj.as_str(a.get("plugin_id_hash"), 128)
    redacted = not plugin or plugin in pj.REDACTED_NAMES
    if redacted:
        name = f"third-party:{id_hash}" if id_hash else "third-party"
        return name, "", id_hash, True
    marketplace = pj.as_str(a.get("marketplace.name"), 200)
    owner = marketplace if marketplace and marketplace not in pj.REDACTED_NAMES else ""
    return plugin, owner, id_hash, False


def _plugin_loaded(conn, ident, sid, at, a, name) -> bool:
    plugin, owner, id_hash, redacted = _plugin_identity(a)
    if not redacted and id_hash:
        pj.rename_redacted_plugin(conn, id_hash, "plugin", plugin, owner, "")
    artifact_id = pj.upsert_artifact(
        conn,
        ident,
        "plugin",
        plugin,
        owner_repo=owner,
        source=pj.source_from_claude(a.get("plugin.scope") or a.get("enabled_via")) or "plugin",
        plugin_id_hash=id_hash,
        seen_at=at,
        raw={"otel_plugin_loaded": _attrs_for_raw(a)},
    )
    version = pj.as_str(a.get("plugin.version"), 64)
    if version and not redacted:
        # No content hash is exported for plugins; the declared version is the
        # best available identity for a version row until sync_artifacts runs.
        pj.upsert_artifact_version(
            conn,
            ident,
            artifact_id,
            f"plugin-version:{version}",
            seen_at=at,
            frontmatter={"version": version},
        )
    return True


def _hook_registered(conn, ident, sid, at, a, name) -> bool:
    hook_event = pj.as_str(a.get("hook_event"), 64)
    if not hook_event:
        return False
    plugin, owner, id_hash, redacted = _plugin_identity(a)
    source = pj.source_from_claude(a.get("hook_source"))
    owner_repo = plugin if source == "plugin" else (owner or source or "")
    pj.upsert_artifact(
        conn,
        ident,
        "hook",
        hook_event,
        owner_repo=owner_repo,
        path=pj.as_str(a.get("hook_type"), 64) or "",
        source=source,
        plugin_id_hash=id_hash if source == "plugin" else None,
        seen_at=at,
        raw={"otel_hook_registered": _attrs_for_raw(a)},
    )
    return True


_LOG_HANDLERS = {
    "api_request": _api_request,
    "skill_activated": _skill_activated,
    "user_prompt": _user_prompt,
    "assistant_response": _assistant_response,
    "tool_result": _tool_event,
    "tool_decision": _tool_event,
    "subagent_completed": _subagent_completed,
    "compaction": _compaction,
    "api_error": _api_error,
    "api_refusal": _api_error,
    "hook_execution_start": _hook_execution,
    "hook_execution_complete": _hook_execution,
    "plugin_loaded": _plugin_loaded,
    "hook_registered": _hook_registered,
}


# ---------------------------------------------------------------- endpoints


@router.post("/v1/metrics")
async def receive_metrics(
    request: Request, identity: AuthContext = Depends(require_auth)
) -> Response:
    req = ExportMetricsServiceRequest()
    ct = await _decode(request, req)
    db.ensure_schema(request.app.state)
    outcome = ingest_metrics(
        request.app.state.engine, req, pj.Identity(identity.oid, identity.upn)
    )
    log.info(
        "otlp metrics ct=%s upn=%s mapped=%d unmapped=%d %s",
        ct, identity.upn, outcome["mapped"], outcome["unmapped"], summarize_metrics(req)["metrics"],
    )
    return _respond(ExportMetricsServiceResponse(), ct)


@router.post("/v1/logs")
async def receive_logs(
    request: Request, identity: AuthContext = Depends(require_auth)
) -> Response:
    req = ExportLogsServiceRequest()
    ct = await _decode(request, req)
    db.ensure_schema(request.app.state)
    outcome = ingest_logs(
        request.app.state.engine,
        req,
        pj.Identity(identity.oid, identity.upn),
        capture_optins(request),
    )
    log.info(
        "otlp logs ct=%s upn=%s mapped=%d unmapped=%d %s",
        ct, identity.upn, outcome["mapped"], outcome["unmapped"], summarize_logs(req)["events"],
    )
    return _respond(ExportLogsServiceResponse(), ct)
