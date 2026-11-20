"""``POST /v1/telemetry/hook-events``: store hook events and project them
into the session model (design D6 → D9, tasks 2.3).

Per batch: every record is validated on its own (a malformed one goes to
``rejected`` instead of failing the batch), checked against
``HOOK_EVENTS_MAX_AGE_SECONDS`` (spooled data is relaxed, design D17), inserted
into ``hook_events_raw`` idempotently by ``hook_event_id`` (a replay is a
counted no-op), and projected through the order-tolerant upserts in
``projection``. Each record runs in its own savepoint so a projection failure
rejects that record alone and leaves the rest of the batch intact.

Content (prompt text, tool input/output, compaction summaries, session title)
is written only when the client's whitelist projection carries it — the
capture toggles live on the client (D6); the server never derives content.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from . import projection as pj
from .models import HookEvent, HookEventRejection, HookEventResult

log = logging.getLogger("ouroboros.hook_ingest")

INSERT_RAW = text(
    """
    INSERT INTO hook_events_raw (
        hook_event_id, session_id, prompt_id, agent_id, hook_event_name,
        client_ts, client_version, auth_oid, auth_upn, payload
    ) VALUES (
        :hook_event_id, :session_id, :prompt_id, :agent_id, :hook_event_name,
        :client_ts, :client_version, :auth_oid, :auth_upn, CAST(:payload AS JSONB)
    )
    ON CONFLICT (hook_event_id) DO NOTHING
    RETURNING hook_event_id
    """
)


def ingest_batch(
    engine: Engine,
    raw_events: list[Any],
    ident: pj.Identity,
    max_age_seconds: int,
    now: datetime | None = None,
) -> HookEventResult:
    now = now or datetime.now(timezone.utc)
    max_age = timedelta(seconds=max_age_seconds)
    accepted = duplicates = 0
    rejected: list[HookEventRejection] = []

    with engine.begin() as conn:
        for item in raw_events:
            event_id = item.get("hook_event_id") if isinstance(item, dict) else None
            event_id = str(event_id) if event_id is not None else None
            try:
                event = HookEvent.model_validate(item)
            except ValidationError as exc:
                first = exc.errors()[0]
                loc = ".".join(str(p) for p in first.get("loc", ()))
                rejected.append(
                    HookEventRejection(hook_event_id=event_id, reason=f"invalid {loc}: {first['msg']}")
                )
                continue

            if now - event.client_ts > max_age:
                rejected.append(
                    HookEventRejection(
                        hook_event_id=str(event.hook_event_id),
                        reason=f"stale: client_ts older than {max_age_seconds}s",
                    )
                )
                continue

            payload = dict(event.payload)
            if event.client_seq is not None:
                # hook_events_raw has no column for it; keep it with the payload
                # so projections (and replays) can order by it.
                payload["client_seq"] = event.client_seq
            try:
                with conn.begin_nested():
                    inserted = conn.execute(
                        INSERT_RAW,
                        {
                            "hook_event_id": str(event.hook_event_id),
                            "session_id": str(event.session_id),
                            "prompt_id": event.prompt_id,
                            "agent_id": event.agent_id,
                            "hook_event_name": event.hook_event_name,
                            "client_ts": event.client_ts,
                            "client_version": event.client_version,
                            "auth_oid": ident.oid,
                            "auth_upn": ident.upn,
                            "payload": pj.jsonb(payload),
                        },
                    ).scalar_one_or_none()
                    if inserted is None:
                        duplicates += 1
                        continue
                    project(conn, ident, event.model_copy(update={"payload": payload}))
                    accepted += 1
            except Exception as exc:  # the savepoint rolled back this record only
                log.warning(
                    "hook event %s (%s) rejected: %s", event.hook_event_id, event.hook_event_name, exc
                )
                rejected.append(
                    HookEventRejection(
                        hook_event_id=str(event.hook_event_id),
                        reason=f"projection failed: {type(exc).__name__}",
                    )
                )

    return HookEventResult(accepted=accepted, duplicates=duplicates, rejected=rejected)


# -------------------------------------------------------------- projection


def project(conn: Connection, ident: pj.Identity, ev: HookEvent) -> None:
    sid = str(ev.session_id)
    p = ev.payload
    handler = _HANDLERS.get(ev.hook_event_name)
    # Every event stubs the session so lifecycle rows have their FK target;
    # SessionStart/SessionEnd enrich it below.
    pj.ensure_session(
        conn,
        ident,
        sid,
        cwd_hash=ev.cwd_hash or pj.as_str(p.get("cwd_hash"), 128),
        project_key=pj.as_str(p.get("project_key"), 200),
        client_version=ev.client_version,
        # Spike 0.4: SessionStart(startup) carries only `source`; permission
        # mode and effort ride on later events, so any event may fill them.
        permission_mode=pj.as_str(p.get("permission_mode"), 64),
        effort=_effort(p.get("effort")),
    )
    if handler is not None:
        handler(conn, ident, ev, sid, p)
    # Unknown hook names are kept in hook_events_raw only; a later parser can replay them.


def _effort(v: Any) -> str | None:
    """Claude Code sends `effort` as {"level": ...} on hook payloads."""
    if isinstance(v, dict):
        v = v.get("level")
    return pj.as_str(v, 32)


def _session_start(conn, ident, ev, sid, p):
    # `source` is startup | resume | compact | clear; a compact/resume start
    # must not reset started_at (fill-nulls-only) and keeps its own raw key.
    source = pj.as_str(p.get("source"), 64)
    raw_key = "SessionStart" if source in (None, "startup") else f"SessionStart:{source}"
    pj.ensure_session(
        conn,
        ident,
        sid,
        started_at=ev.client_ts,
        source=source,
        model_initial=pj.as_str(p.get("model"), 128),
        agent_type=pj.as_str(p.get("agent_type"), 128),
        title_redacted=pj.as_str(p.get("session_title"), 512),
        context_tokens_at_start=pj.as_int32(p.get("context_tokens")),
        raw={raw_key: p},
    )


def _session_end(conn, ident, ev, sid, p):
    pj.ensure_session(
        conn,
        ident,
        sid,
        ended_at=ev.client_ts,
        end_reason=pj.as_str(p.get("reason"), 64),
        raw={"SessionEnd": p},
    )


def _user_prompt_submit(conn, ident, ev, sid, p):
    if not ev.prompt_id:
        return
    prompt_text = p.get("prompt") if isinstance(p.get("prompt"), str) else None
    pj.upsert_turn(
        conn,
        ident,
        sid,
        ev.prompt_id,
        seq=ev.client_seq,
        submitted_at=ev.client_ts,
        prompt_length=pj.first(pj.as_int32(p.get("prompt_length")), pj.text_length(prompt_text)),
        prompt_text=prompt_text,
        model=pj.as_str(p.get("model"), 128),
        raw={"UserPromptSubmit": p},
    )


def _user_prompt_expansion(conn, ident, ev, sid, p):
    if not ev.prompt_id:
        return
    pj.upsert_turn(
        conn,
        ident,
        sid,
        ev.prompt_id,
        seq=ev.client_seq,
        submitted_at=ev.client_ts,
        expansion_type=pj.as_str(p.get("expansion_type"), 64),
        command_name=pj.as_str(p.get("command_name"), 200),
        command_source=pj.as_str(p.get("command_source"), 64),
        raw={"UserPromptExpansion": p},
    )


def _stop(conn, ident, ev, sid, p):
    if not ev.prompt_id:
        return
    response_text = next(
        (p[k] for k in ("last_assistant_message", "response") if isinstance(p.get(k), str)), None
    )
    pj.upsert_turn(
        conn,
        ident,
        sid,
        ev.prompt_id,
        seq=ev.client_seq,
        stopped_at=ev.client_ts,
        response_length=pj.first(
            pj.as_int32(p.get("response_length")),
            pj.as_int32(p.get("last_assistant_message_length")),
            pj.text_length(response_text),
        ),
        response_text=response_text,
        model=pj.as_str(p.get("model"), 128),
        raw={"Stop": p},
    )


def _tool_call(conn, ident, ev, sid, tool: dict, failed: bool, name: str):
    tool_use_id = pj.as_str(tool.get("tool_use_id"), 200)
    if not tool_use_id:
        return
    duration = pj.as_int32(tool.get("duration_ms"))
    started_at = ev.client_ts - timedelta(milliseconds=duration) if duration and duration > 0 else ev.client_ts
    success = pj.as_bool(tool.get("success"))
    if success is None and failed:
        success = False
    tool_input = tool.get("tool_input")
    tool_response = tool.get("tool_response")
    pj.upsert_tool_call(
        conn,
        ident,
        sid,
        tool_use_id,
        prompt_id=ev.prompt_id,
        agent_id=ev.agent_id,
        tool_name=pj.as_str(tool.get("tool_name"), 200),
        mcp_server=pj.as_str(tool.get("mcp_server"), 200),
        started_at=started_at,
        duration_ms=duration,
        success=success,
        is_interrupt=pj.as_bool(tool.get("is_interrupt")),
        input_bytes=pj.as_int32(tool.get("input_bytes")),
        output_bytes=pj.as_int32(tool.get("output_bytes")),
        error_class=pj.as_str(tool.get("error_class"), 200) or pj.first_line(tool.get("error")),
        input_content=pj.as_str(tool_input) if tool_input is not None else None,
        output_content=pj.as_str(tool_response) if tool_response is not None else None,
        raw={name: tool},
    )
    # Spike 0.4: model-invoked skills surface as tool_name "Skill"; the same
    # (session, prompt, skill) key makes the OTel skill_activated row converge.
    skill = pj.as_str(tool.get("skill_name"), 200)
    if not skill and tool.get("tool_name") == "Skill" and isinstance(tool_input, dict):
        skill = pj.as_str(tool_input.get("skill"), 200)
    if skill and ev.prompt_id:
        pj.insert_skill_activation(
            conn,
            ident,
            pj.skill_activation_id(sid, ev.prompt_id, skill),
            session_id=sid,
            prompt_id=ev.prompt_id,
            artifact_id=pj.upsert_artifact(conn, ident, "skill", skill, seen_at=ev.client_ts),
            skill_name=skill,
            at=started_at,
            raw={name: {"tool_use_id": tool_use_id}},
        )


def _post_tool_use(conn, ident, ev, sid, p):
    failed = ev.hook_event_name == "PostToolUseFailure"
    if "tool_use_id" in p:
        _tool_call(conn, ident, ev, sid, p, failed, ev.hook_event_name)
        return
    # PostToolBatch: the client sends only `tool_use_ids` (the per-call facts
    # arrive on PostToolUse), which stays in hook_events_raw; a list of per-tool
    # projections under tool_calls / tools is projected call by call.
    batch = p.get("tool_calls") or p.get("tools") or []
    for tool in batch:
        if isinstance(tool, dict):
            _tool_call(conn, ident, ev, sid, tool, failed, ev.hook_event_name)


def _subagent(conn, ident, ev, sid, p):
    agent_id = ev.agent_id or pj.as_str(p.get("agent_id"), 200)
    if not agent_id:
        return
    is_stop = ev.hook_event_name == "SubagentStop"
    last = p.get("last_assistant_message")
    pj.upsert_subagent_run(
        conn,
        ident,
        sid,
        agent_id,
        prompt_id=ev.prompt_id,
        agent_type=pj.as_str(p.get("agent_type"), 128),
        started_at=None if is_stop else ev.client_ts,
        stopped_at=ev.client_ts if is_stop else None,
        transcript_hash=pj.as_str(
            p.get("agent_transcript_hash") or p.get("agent_transcript_path"), 128
        ),
        last_message_length=pj.first(
            pj.as_int32(p.get("last_assistant_message_length")), pj.text_length(last)
        ),
        raw={ev.hook_event_name: p},
    )


def _compact(conn, ident, ev, sid, p):
    is_post = ev.hook_event_name == "PostCompact"
    summary = p.get("compact_summary") if isinstance(p.get("compact_summary"), str) else None
    fields: dict[str, Any] = {
        "prompt_id": ev.prompt_id,
        "trigger": pj.as_str(p.get("trigger"), 32),
        "custom_instructions_length": pj.first(
            pj.as_int32(p.get("custom_instructions_length")), pj.text_length(p.get("custom_instructions"))
        ),
    }
    if is_post:
        fields["summary_length"] = pj.first(pj.as_int32(p.get("compact_summary_length")), pj.text_length(summary))
        fields["summary_text"] = summary
    pj.attach_compaction(
        conn,
        ident,
        sid,
        ev.client_ts,
        "hook_post" if is_post else "hook_pre",
        pj.derived_id("compaction", ev.hook_event_id),
        raw=p,
        **fields,
    )


def _instructions_loaded(conn, ident, ev, sid, p):
    file_path = pj.as_str(p.get("file_path"), 1024)
    if not file_path:
        return
    content_hash = pj.as_str(pj.first(p.get("content_sha256"), p.get("content_hash")), 128)
    kind = "rule" if "/rules/" in file_path or file_path.startswith("rules/") else "instructions"
    # Register the file (D10: InstructionsLoaded populates instructions/rules)
    # by (kind, path) and, when the client hashed it, the version it was seen
    # with; the row links to that version through (kind, path, content_hash).
    artifact_id = pj.upsert_artifact(
        conn,
        ident,
        kind,
        file_path.rsplit("/", 1)[-1],
        path=file_path,
        source=pj.source_from_claude(p.get("memory_type")),
        seen_at=ev.client_ts,
        raw={"InstructionsLoaded": {k: p.get(k) for k in ("memory_type", "load_reason")}},
    )
    if content_hash:
        pj.upsert_artifact_version(conn, ident, artifact_id, content_hash, seen_at=ev.client_ts)
    pj.insert_instructions_loaded(
        conn,
        ident,
        pj.derived_id("instructions_loaded", ev.hook_event_id),
        session_id=sid,
        prompt_id=ev.prompt_id,
        artifact_id=artifact_id,
        file_path=file_path,
        content_hash=content_hash,
        memory_type=pj.as_str(p.get("memory_type"), 64),
        load_reason=pj.as_str(p.get("load_reason"), 64),
        trigger_file_path=pj.as_str(p.get("trigger_file_path"), 1024),
        parent_file_path=pj.as_str(p.get("parent_file_path"), 1024),
        at=ev.client_ts,
        raw=p,
    )


def _model_switch(conn, ident, ev, sid, p):
    pj.insert_model_switch(
        conn,
        ident,
        pj.derived_id("model_switch", ev.hook_event_id),
        session_id=sid,
        prompt_id=ev.prompt_id,
        phase="post" if ev.hook_event_name == "PostModelSwitch" else "pre",
        from_model=pj.as_str(p.get("from_model"), 128),
        to_model=pj.as_str(p.get("to_model"), 128),
        context_tokens=pj.as_int32(p.get("context_tokens")),
        at=ev.client_ts,
        raw=p,
    )


_HANDLERS = {
    "SessionStart": _session_start,
    "SessionEnd": _session_end,
    "UserPromptSubmit": _user_prompt_submit,
    "UserPromptExpansion": _user_prompt_expansion,
    "Stop": _stop,
    "PostToolUse": _post_tool_use,
    "PostToolUseFailure": _post_tool_use,
    "PostToolBatch": _post_tool_use,
    "SubagentStart": _subagent,
    "SubagentStop": _subagent,
    "PreCompact": _compact,
    "PostCompact": _compact,
    "InstructionsLoaded": _instructions_loaded,
    "PreModelSwitch": _model_switch,
    "PostModelSwitch": _model_switch,
}
