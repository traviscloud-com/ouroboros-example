"""Claude Code hook entrypoint (`ouroboros-hook`).

Reads one hook event as JSON on stdin, dispatches on `hook_event_name`, and
does two local things (design D6):

1. keeps the per-session turn cache (`UserPromptSubmit` / `Stop`) exactly as
   before, so the SDK can attach the latest turn to a reported outcome;
2. writes one structured hook-event record to `~/.ouroboros/spool/hooks/`
   whose `payload` is a per-event *whitelist projection* — never the raw stdin.

Verbatim content is included only under the named toggles
(`OUROBOROS_CAPTURE_PROMPTS`, default on; `OUROBOROS_CAPTURE_TOOL_CONTENT`,
default off). `OUROBOROS_TELEMETRY=off` disables everything.

Stdlib-only, zero network, always exits 0 — a capture failure must never
disrupt the user's session. Upload is nudged by spawning the SDK sender as a
separate detached process (design D16); nothing network-related happens here.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, config, interactions, spool

# Longest metadata string kept (names, paths, reasons). Content fields have
# their own bounds below.
_META_MAX = 1024
_ERROR_CLASS_MAX = 200
# Verbatim tool content (opt-in) is bounded so one giant tool result cannot
# fill the spool; prompts keep the existing unbounded behaviour.
_TOOL_CONTENT_MAX = 64 * 1024


# --- value helpers -----------------------------------------------------------


def _str(value, max_len: int = _META_MAX) -> str | None:
    if isinstance(value, str) and value:
        return value[:max_len]
    return None


def _int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return None


def _text(value) -> str | None:
    """Content as text: strings verbatim, structured values as compact JSON."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return None


def _length(value) -> int | None:
    text = _text(value)
    return len(text) if text is not None else None


def _nbytes(value) -> int | None:
    text = _text(value)
    return len(text.encode("utf-8", "replace")) if text is not None else None


def _str_list(value) -> list[str] | None:
    if isinstance(value, list):
        items = [v[:_META_MAX] for v in value if isinstance(v, str)]
        return items[:64]
    if isinstance(value, str) and value:
        return [value[:_META_MAX]]
    return None


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def _first_line(value, max_len: int = _ERROR_CLASS_MAX) -> str | None:
    text = _text(value)
    if not text:
        return None
    return text.strip().splitlines()[0][:max_len] if text.strip() else None


def _relative_path(path: str | None, cwd: str | None) -> str | None:
    """Make an instruction-file path relative to cwd; outside cwd, fold the
    home directory to `~` so the record never carries the username."""
    if not path:
        return None
    try:
        p = Path(path)
        if not p.is_absolute():
            return str(p)[:_META_MAX]
        if cwd:
            try:
                return str(p.relative_to(Path(cwd)))[:_META_MAX]
            except ValueError:
                pass
        home = Path.home()
        try:
            return ("~/" + str(p.relative_to(home)))[:_META_MAX]
        except ValueError:
            return str(p)[:_META_MAX]
    except (TypeError, ValueError, RuntimeError):
        return None


def _file_sha256(path: str | None, cwd: str | None) -> str | None:
    """Content hash of an instruction file when readable and not huge."""
    if not path:
        return None
    try:
        p = Path(path)
        if not p.is_absolute() and cwd:
            p = Path(cwd) / p
        if not p.is_file() or p.stat().st_size > config.INSTRUCTIONS_HASH_MAX_BYTES:
            return None
        digest = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, ValueError):
        return None


def _mcp_server(data: dict) -> str | None:
    explicit = _str(data.get("mcp_server"))
    if explicit:
        return explicit
    name = data.get("tool_name")
    if isinstance(name, str) and name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 3 and parts[1]:
            return parts[1][:_META_MAX]
    return None


# --- projections (design D6, amended by spike 0.4) ---------------------------
#
# Every whitelisted key is always present — `null` when the payload lacks it
# — so the server can tell "absent" from "not captured" (toggle-gated content
# keys are omitted entirely while their toggle is off). Nothing outside the
# whitelist is ever copied; raw stdin never leaves the process.


def _effort(d: dict) -> str | None:
    """`effort` arrives as {"level": "medium"}; older/other shapes as a string."""
    value = d.get("effort")
    if isinstance(value, dict):
        return _str(value.get("level"))
    return _str(value)


def _session_start(d: dict) -> dict:
    # At `startup` only `source` arrives; `model` with source=compact and
    # `context_tokens` with source=resume (spike 0.4).
    p: dict = {
        "source": _str(d.get("source")),
        "model": _str(d.get("model")),
        "agent_type": _str(d.get("agent_type")),
        "context_tokens": _int(d.get("context_tokens")),
    }
    if config.capture_prompts():
        p["session_title"] = _str(d.get("session_title"))
    return p


def _session_end(d: dict) -> dict:
    return {"reason": _str(d.get("reason"))}


def _prompt_text(d: dict) -> str | None:
    text = d.get("prompt_text")
    if not isinstance(text, str) or not text:
        text = d.get("prompt")
    return text if isinstance(text, str) and text else None


def _user_prompt_submit(d: dict) -> dict:
    text = _prompt_text(d)
    p: dict = {"prompt_length": len(text) if text is not None else None}
    if config.capture_prompts():
        p["prompt"] = text
    return p


def _user_prompt_expansion(d: dict) -> dict:
    args = d.get("command_args")
    p: dict = {
        "expansion_type": _str(d.get("expansion_type")),
        "command_name": _str(d.get("command_name")),
        "command_source": _str(d.get("command_source")),
        "command_args_length": _length(args),
    }
    if config.capture_tool_content():
        p["command_args"] = _text(args)
    return p


def _tool_event(d: dict, *, failure: bool) -> dict:
    # PostToolUse carries no success/is_interrupt/mcp_server/byte counts:
    # success is the event name, mcp_server is parsed from `mcp__<server>__`,
    # sizes are computed from the serialized input/response (spike 0.4).
    tool_name = _str(d.get("tool_name"))
    tool_input = d.get("tool_input")
    p: dict = {
        "tool_name": tool_name,
        "tool_use_id": _str(d.get("tool_use_id")),
        "mcp_server": _mcp_server(d),
        "duration_ms": _int(d.get("duration_ms")),
        "success": not failure,
        "is_interrupt": _bool(d.get("is_interrupt")),
        "input_bytes": _nbytes(tool_input),
        "output_bytes": _nbytes(d.get("tool_response")),
        "error_class": _first_line(d.get("error")),
        # model-invoked skills surface as tool_name=Skill, tool_input.skill
        "skill_name": _str(tool_input.get("skill")) if tool_name == "Skill" and isinstance(tool_input, dict) else None,
    }
    if config.capture_tool_content():
        for key in ("tool_input", "tool_response"):
            text = _text(d.get(key))
            if text is not None and len(text) > _TOOL_CONTENT_MAX:
                text = text[:_TOOL_CONTENT_MAX]
                p[f"{key}_truncated"] = True
            p[key] = text
    return p


def _post_tool_use(d: dict) -> dict:
    return _tool_event(d, failure=False)


def _post_tool_use_failure(d: dict) -> dict:
    return _tool_event(d, failure=True)


def _post_tool_batch(d: dict) -> dict:
    # The batch adds nothing per call beyond grouping (spike 0.4): keep the
    # ids so the server can link it to the PostToolUse records.
    calls = d.get("tool_calls")
    ids = None
    if isinstance(calls, list):
        ids = [c["tool_use_id"][:_META_MAX] for c in calls
               if isinstance(c, dict) and isinstance(c.get("tool_use_id"), str)][:500]
    return {"tool_use_ids": ids}


def _subagent(d: dict) -> dict:
    # SubagentStart has no transcript path; the compaction summarizer fires a
    # SubagentStop without agent_id — both are null here, never required.
    path = _str(d.get("agent_transcript_path"))
    msg = d.get("last_assistant_message")
    p: dict = {
        "agent_id": _str(d.get("agent_id")),
        "agent_type": _str(d.get("agent_type")),
        "agent_transcript_hash": _sha256_text(path) if path else None,
        "last_assistant_message_length": _length(msg),
    }
    if config.capture_prompts():
        p["last_assistant_message"] = _str(msg, max_len=1 << 31)
    return p


def _compact(d: dict) -> dict:
    summary = d.get("compact_summary")
    p: dict = {
        "trigger": _str(d.get("trigger")),
        "custom_instructions_length": _length(d.get("custom_instructions")),
        "compact_summary_length": _length(summary),
    }
    if config.capture_prompts():
        p["compact_summary"] = _str(summary, max_len=1 << 31)
    return p


def _instructions_loaded(d: dict) -> dict:
    cwd = d.get("cwd") if isinstance(d.get("cwd"), str) else None
    raw_path = d.get("file_path") if isinstance(d.get("file_path"), str) else None

    def rel(key: str) -> str | None:
        value = d.get(key)
        return _relative_path(value if isinstance(value, str) else None, cwd)

    return {
        "file_path": _relative_path(raw_path, cwd),
        "content_sha256": _file_sha256(raw_path, cwd),
        "memory_type": _str(d.get("memory_type")),
        "load_reason": _str(d.get("load_reason")),
        "globs": _str_list(d.get("globs")),
        "trigger_file_path": rel("trigger_file_path"),
        "parent_file_path": rel("parent_file_path"),
    }


def _model_switch(d: dict) -> dict:
    return {
        "from_model": _str(d.get("from_model")),
        "to_model": _str(d.get("to_model")),
        "requested_model": _str(d.get("requested_model")),
        "source": _str(d.get("source")),
        "context_tokens": _int(d.get("context_tokens")),
    }


def _stop(d: dict) -> dict:
    return {
        "stop_hook_active": _bool(d.get("stop_hook_active")),
        "last_assistant_message_length": _length(d.get("last_assistant_message")),
    }


PROJECTIONS = {
    "SessionStart": _session_start,
    "SessionEnd": _session_end,
    "UserPromptSubmit": _user_prompt_submit,
    "UserPromptExpansion": _user_prompt_expansion,
    "PostToolUse": _post_tool_use,
    "PostToolUseFailure": _post_tool_use_failure,
    "PostToolBatch": _post_tool_batch,
    "SubagentStart": _subagent,
    "SubagentStop": _subagent,
    "PreCompact": _compact,
    "PostCompact": _compact,
    "InstructionsLoaded": _instructions_loaded,
    "PreModelSwitch": _model_switch,
    "PostModelSwitch": _model_switch,
    "Stop": _stop,
}

SUPPORTED_EVENTS = tuple(PROJECTIONS)

# Present on most turn-scoped events; copied into every payload when the
# event carries them (the server takes a session's permission_mode / effort
# from the first event that has them — spike 0.4).
COMMON_KEYS = ("permission_mode", "effort")


def _common(d: dict, payload: dict) -> dict:
    if "permission_mode" in d:
        payload["permission_mode"] = _str(d.get("permission_mode"))
    if "effort" in d:
        payload["effort"] = _effort(d)
    return payload


def _client_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_record(data: dict) -> dict | None:
    """The hook-event record for one stdin payload, or None when the event is
    unsupported or lacks a usable session_id. Local I/O only: the
    InstructionsLoaded file hash and the per-session sequence counter."""
    name = data.get("hook_event_name")
    session_id = data.get("session_id")
    if name not in PROJECTIONS or not isinstance(session_id, str) or not session_id:
        return None
    record: dict = {
        "hook_event_id": str(uuid.uuid4()),
        "session_id": session_id[:_META_MAX],
        "hook_event_name": name,
        "client_ts": _client_ts(),
        "client_version": __version__,
        "payload": _common(data, PROJECTIONS[name](data)),
    }
    prompt_id = _str(data.get("prompt_id"))
    if prompt_id:
        record["prompt_id"] = prompt_id
    agent_id = _str(data.get("agent_id"))
    if agent_id:
        record["agent_id"] = agent_id
    cwd = data.get("cwd")
    if isinstance(cwd, str) and cwd:
        record["cwd_hash"] = _sha256_text(cwd)
    seq = next_client_seq(session_id)
    if seq is not None:
        record["client_seq"] = seq
    return record


def next_client_seq(session_id: str) -> int | None:
    """Per-session counter kept in the hook spool (`.seq-<session>`), so the
    server can order a session's events without trusting the host clock,
    which can step backwards (spike 0.4). Best effort: None when the counter
    cannot be read or written; concurrent async hooks may rarely share a
    number, hence "monotonic-ish"."""
    try:
        d = config.hook_spool_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f".seq-{session_id.replace('/', '_')[:128]}"
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            raw = os.read(fd, 32)
            try:
                current = int(raw.decode().strip() or "0")
            except ValueError:
                current = 0
            nxt = current + 1
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, str(nxt).encode())
            return nxt
        finally:
            os.close(fd)
    except Exception:
        return None


def _update_turn_cache(data: dict) -> None:
    """UserPromptSubmit / Stop keep the interaction cache as before, but only
    while prompt capture is on (the cache holds verbatim text)."""
    if not config.capture_prompts():
        return
    name = data.get("hook_event_name")
    session_id = data.get("session_id")
    prompt_id = data.get("prompt_id")
    if not isinstance(prompt_id, str):
        prompt_id = None
    if name == "UserPromptSubmit":
        text = _prompt_text(data)
        if text:
            interactions.record_prompt(session_id, prompt_id, text)
    elif name == "Stop":
        text = data.get("last_assistant_message")
        if text and isinstance(text, str):
            interactions.record_response(session_id, prompt_id, text)


def _handle(data: dict) -> None:
    record = build_record(data)
    if record is None:
        return
    _update_turn_cache(data)
    spool.write_hook_event(record)


def _nudge_sender() -> None:
    """Spawn the SDK sender as a detached process so the upload happens
    outside the hook (never inline — design D6/D16). Rate-limited through a
    marker file so a burst of tool events spawns one sender, not dozens."""
    if not config.hook_nudge_enabled() or not config.api_url():
        return
    marker = config.hook_spool_dir() / ".last_nudge"
    try:
        if time.time() - marker.stat().st_mtime < config.HOOK_NUDGE_MIN_INTERVAL_SECONDS:
            return
    except OSError:
        pass
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    subprocess.Popen(
        [sys.executable, "-m", "ouroboros_client.sender"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=os.environ.copy(),
    )


def main() -> int:
    try:
        if not config.telemetry_enabled():
            return 0
        data = json.load(sys.stdin)
        if isinstance(data, dict):
            _handle(data)
        interactions.cleanup_expired()
    except Exception:
        pass
    try:
        if config.telemetry_enabled() and spool.list_hook_files():
            _nudge_sender()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
