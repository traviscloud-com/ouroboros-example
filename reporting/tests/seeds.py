"""Seed helpers for the session-model tables (design D9/D10) used by the
analytics, export and scheduler tests. All timestamps are UTC."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

UPN = "dev@example.com"
OID = "oid-dev"


def ts(year, month, day, hour=12, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def seed_session(engine, *, session_id=None, started_at=None, ended_at=None, project_key="proj",
                 title=None, oid=OID, upn=UPN, raw=None) -> str:
    sid = session_id or str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sessions (session_id, started_at, ended_at, source, project_key, title_redacted, "
                "auth_oid, auth_upn, raw) VALUES (:sid, :started, :ended, 'startup', :project, :title, :oid, :upn, "
                "CAST(:raw AS JSONB)) ON CONFLICT (session_id) DO NOTHING"
            ),
            {"sid": sid, "started": started_at, "ended": ended_at, "project": project_key,
             "title": title, "oid": oid, "upn": upn, "raw": json.dumps(raw or {})},
        )
    return sid


def seed_turn(engine, session_id, *, prompt_id=None, seq=1, submitted_at=None, stopped_at=None,
              prompt_text=None, response_text=None, model="claude-sonnet-4-5", command_name=None,
              oid=OID, upn=UPN, raw=None) -> str:
    pid = prompt_id or str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO turns (session_id, prompt_id, seq, submitted_at, stopped_at, prompt_length, "
                "prompt_text, response_length, response_text, model, command_name, auth_oid, auth_upn, raw) "
                "VALUES (:sid, :pid, :seq, :sub, :stop, :plen, :ptext, :rlen, :rtext, :model, :cmd, :oid, :upn, "
                "CAST(:raw AS JSONB))"
            ),
            {"sid": session_id, "pid": pid, "seq": seq, "sub": submitted_at, "stop": stopped_at,
             "plen": len(prompt_text) if prompt_text else None, "ptext": prompt_text,
             "rlen": len(response_text) if response_text else None, "rtext": response_text,
             "model": model, "cmd": command_name, "oid": oid, "upn": upn, "raw": json.dumps(raw or {})},
        )
    return pid


def seed_tool_call(engine, session_id, *, prompt_id=None, tool_name="Read", started_at=None,
                   duration_ms=50, success=True, input_content=None, output_content=None,
                   oid=OID, upn=UPN) -> str:
    tid = "toolu_" + uuid.uuid4().hex[:12]
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tool_calls (tool_use_id, session_id, prompt_id, tool_name, started_at, duration_ms, "
                "success, input_content, output_content, auth_oid, auth_upn) VALUES (:tid, :sid, :pid, :tool, "
                ":started, :dur, :ok, :inp, :outp, :oid, :upn)"
            ),
            {"tid": tid, "sid": session_id, "pid": prompt_id, "tool": tool_name, "started": started_at,
             "dur": duration_ms, "ok": success, "inp": input_content, "outp": output_content,
             "oid": oid, "upn": upn},
        )
    return tid


def seed_usage(engine, session_id, *, prompt_id=None, at=None, model="claude-sonnet-4-5", skill_name=None,
               agent_name=None, cost_usd=0.01, input_tokens=1000, output_tokens=200, cache_read=0,
               cache_create=0, kind="api_request", oid=OID, upn=UPN) -> None:
    attrs = {"input_tokens": input_tokens, "output_tokens": output_tokens,
             "cache_read_tokens": cache_read, "cache_creation_tokens": cache_create}
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO usage_facts (session_id, prompt_id, at, kind, model, cost_usd, skill_name, agent_name, "
                "request_id, attrs, auth_oid, auth_upn) VALUES (:sid, :pid, :at, :kind, :model, :cost, :skill, "
                ":agent, :req, CAST(:attrs AS JSONB), :oid, :upn)"
            ),
            {"sid": session_id, "pid": prompt_id, "at": at or datetime.now(timezone.utc), "kind": kind,
             "model": model, "cost": cost_usd, "skill": skill_name, "agent": agent_name,
             "req": "req_" + uuid.uuid4().hex[:10] if kind == "api_request" else None,
             "attrs": json.dumps(attrs), "oid": oid, "upn": upn},
        )


def seed_compaction(engine, session_id, *, prompt_id=None, at=None, pre_tokens=150000, post_tokens=40000,
                    summary_text=None, oid=OID, upn=UPN) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO compactions (session_id, prompt_id, trigger, pre_tokens, post_tokens, at, summary_text, "
                "auth_oid, auth_upn) VALUES (:sid, :pid, 'auto', :pre, :post, :at, :summary, :oid, :upn)"
            ),
            {"sid": session_id, "pid": prompt_id, "pre": pre_tokens, "post": post_tokens,
             "at": at or datetime.now(timezone.utc), "summary": summary_text, "oid": oid, "upn": upn},
        )


def seed_skill_activation(engine, session_id, *, prompt_id=None, skill_name="SQL_Generator", at=None,
                          artifact_id=None, oid=OID, upn=UPN) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO skill_activations (session_id, prompt_id, artifact_id, skill_name, skill_source, "
                "invocation_trigger, at, auth_oid, auth_upn) VALUES (:sid, :pid, :aid, :skill, 'plugin', "
                "'model', :at, :oid, :upn)"
            ),
            {"sid": session_id, "pid": prompt_id, "aid": artifact_id, "skill": skill_name,
             "at": at or datetime.now(timezone.utc), "oid": oid, "upn": upn},
        )


def seed_subagent_run(engine, session_id, *, prompt_id=None, agent_type="Explore", started_at=None,
                      stopped_at=None, oid=OID, upn=UPN) -> str:
    agent_id = "agent_" + uuid.uuid4().hex[:10]
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO subagent_runs (agent_id, session_id, prompt_id, agent_type, started_at, stopped_at, "
                "auth_oid, auth_upn) VALUES (:aid, :sid, :pid, :type, :started, :stopped, :oid, :upn)"
            ),
            {"aid": agent_id, "sid": session_id, "pid": prompt_id, "type": agent_type,
             "started": started_at or datetime.now(timezone.utc), "stopped": stopped_at, "oid": oid, "upn": upn},
        )
    return agent_id


def seed_artifact(engine, *, kind="skill", name="SQL_Generator", path="", content_text="# skill body",
                  always_on=120, body_tokens=800, seen_from=None, seen_until=None,
                  oid=OID, upn=UPN) -> tuple[str, str]:
    content_hash = hashlib.sha256((content_text or name).encode()).hexdigest()
    with engine.begin() as conn:
        artifact_id = conn.execute(
            text(
                "INSERT INTO artifacts (kind, name, owner_repo, path, source, auth_oid, auth_upn) "
                "VALUES (:kind, :name, 'org/repo', :path, 'plugin', :oid, :upn) "
                "ON CONFLICT (kind, name, owner_repo, path) DO UPDATE SET last_seen = now() RETURNING artifact_id"
            ),
            {"kind": kind, "name": name, "path": path, "oid": oid, "upn": upn},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO artifact_versions (artifact_id, content_hash, content_text, estimated_tokens_always_on, "
                "estimated_tokens_body, seen_from, seen_until, auth_oid, auth_upn) VALUES (:aid, :hash, :txt, "
                ":always, :body, coalesce(:seen_from, now()), :seen_until, :oid, :upn) "
                "ON CONFLICT (artifact_id, content_hash) DO NOTHING"
            ),
            {"aid": artifact_id, "hash": content_hash, "txt": content_text, "always": always_on,
             "body": body_tokens, "seen_from": seen_from, "seen_until": seen_until, "oid": oid, "upn": upn},
        )
    return str(artifact_id), content_hash


def seed_instruction_load(engine, session_id, *, artifact_id, content_hash, at=None, file_path=".claude/CLAUDE.md",
                          oid=OID, upn=UPN) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO instructions_loaded (session_id, artifact_id, file_path, content_hash, memory_type, "
                "load_reason, at, auth_oid, auth_upn) VALUES (:sid, :aid, :path, :hash, 'Project', 'session_start', "
                ":at, :oid, :upn)"
            ),
            {"sid": session_id, "aid": artifact_id, "path": file_path, "hash": content_hash,
             "at": at or datetime.now(timezone.utc), "oid": oid, "upn": upn},
        )


def seed_hook_event(engine, session_id, *, client_ts=None, name="UserPromptSubmit", payload=None,
                    oid=OID, upn=UPN) -> str:
    hid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO hook_events_raw (hook_event_id, session_id, hook_event_name, client_ts, payload, "
                "auth_oid, auth_upn) VALUES (:hid, :sid, :name, :ts, CAST(:payload AS JSONB), :oid, :upn)"
            ),
            {"hid": hid, "sid": session_id, "name": name, "ts": client_ts or datetime.now(timezone.utc),
             "payload": json.dumps(payload or {}), "oid": oid, "upn": upn},
        )
    return hid


def seed_transcript_line(engine, session_id, *, timestamp=None, content=None, line_type="user",
                         oid=OID, upn=UPN) -> str:
    line_uuid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO transcript_lines (session_id, line_uuid, type, timestamp, content, content_chars, "
                "auth_oid, auth_upn) VALUES (:sid, :lu, :type, :ts, :content, :chars, :oid, :upn)"
            ),
            {"sid": session_id, "lu": line_uuid, "type": line_type, "ts": timestamp or datetime.now(timezone.utc),
             "content": content, "chars": len(content) if content else None, "oid": oid, "upn": upn},
        )
    return line_uuid


def seed_turn_error(engine, session_id, *, prompt_id=None, at=None, message="boom", oid=OID, upn=UPN) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO turn_errors (session_id, prompt_id, kind, message, at, auth_oid, auth_upn) "
                "VALUES (:sid, :pid, 'api_error', :msg, :at, :oid, :upn)"
            ),
            {"sid": session_id, "pid": prompt_id, "msg": message, "at": at or datetime.now(timezone.utc),
             "oid": oid, "upn": upn},
        )


SKILL_TEXT = (
    "---\n"
    "name: telemetry-reporting\n"
    "description: Use this skill whenever anything at all about telemetry, metrics, dashboards, logs or reports comes up\n"
    "---\n"
    "# Telemetry reporting\n"
    "\n"
    "Query the reporting API and summarize.\n"
)

LOW_RISK_DIFF = (
    "--- a/plugin/skills/telemetry-reporting/SKILL.md\n"
    "+++ b/plugin/skills/telemetry-reporting/SKILL.md\n"
    "@@ -1,4 +1,4 @@\n"
    " ---\n"
    " name: telemetry-reporting\n"
    "-description: Use this skill whenever anything at all about telemetry, metrics, dashboards, logs or reports comes up\n"
    "+description: Query Ouroboros telemetry reports\n"
    " ---\n"
)

SKILL_PATH = "plugin/skills/telemetry-reporting/SKILL.md"


def seed_skill_artifact(engine, *, content_text=SKILL_TEXT, path=SKILL_PATH, name="telemetry-reporting",
                        owner_repo="org/repo", kind="skill", always_on=400) -> tuple[str, str]:
    """A synced skill with owner_repo + path; returns (artifact_id, version_id)."""
    content_hash = hashlib.sha256(content_text.encode()).hexdigest()
    with engine.begin() as conn:
        aid = conn.execute(
            text(
                "INSERT INTO artifacts (kind, name, owner_repo, path, source, auth_oid, auth_upn) "
                "VALUES (:kind, :name, :repo, :path, 'plugin', :oid, :upn) RETURNING artifact_id"
            ),
            {"kind": kind, "name": name, "repo": owner_repo, "path": path, "oid": OID, "upn": UPN},
        ).scalar_one()
        vid = conn.execute(
            text(
                "INSERT INTO artifact_versions (artifact_id, content_hash, content_text, "
                "estimated_tokens_always_on, estimated_tokens_body, seen_from, auth_oid, auth_upn) "
                "VALUES (:aid, :hash, :txt, :always, 300, now() - interval '60 days', :oid, :upn) "
                "RETURNING version_id"
            ),
            {"aid": aid, "hash": content_hash, "txt": content_text, "always": always_on, "oid": OID, "upn": UPN},
        ).scalar_one()
    return str(aid), str(vid)


def seed_proposal(engine, artifact_id, *, version_id=None, risk_class="low", status="proposed",
                  diff=LOW_RISK_DIFF, path=SKILL_PATH, rationale="Trim the always-on description.",
                  evidence=None, evidence_ids=(), pr_repo=None, pr_number=None, applied_at=None) -> str:
    with engine.begin() as conn:
        return str(
            conn.execute(
                text(
                    "INSERT INTO improvement_proposals (target_artifact_id, base_version_id, rationale, "
                    "expected_effect, evidence_event_ids, diff, risk_class, status, created_by, target_path, "
                    "evidence, pr_repo, pr_number, applied_at) VALUES (:aid, :vid, :r, "
                    "CAST(:effect AS JSONB), CAST(:ids AS UUID[]), :diff, :risk, :status, 'loop', :path, "
                    "CAST(:evidence AS JSONB), :repo, :num, :applied) RETURNING proposal_id"
                ),
                {"aid": artifact_id, "vid": version_id, "r": rationale,
                 "effect": json.dumps({"tokens_saved": 20, "correction_rate_delta": None, "summary": "trim"}),
                 "ids": list(evidence_ids), "diff": diff, "risk": risk_class, "status": status, "path": path,
                 "evidence": json.dumps(evidence or {}), "repo": pr_repo, "num": pr_number,
                 "applied": applied_at},
            ).scalar_one()
        )
