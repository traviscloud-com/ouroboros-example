"""Per-session interaction cache (design D3). Stdlib-only — shared by the hook
entrypoint (writer) and the SDK attach step (consumer). Atomic writes because
the two can race."""

import json
import os
import time
from pathlib import Path

from . import config


def _session_file(session_id: str) -> Path:
    # session_id is validated as a UUID string by callers; still, never allow
    # path separators into the filename
    safe = session_id.replace("/", "_")
    return config.interactions_dir() / f"{safe}.json"


def _load(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries))
    os.replace(tmp, path)


def record_prompt(session_id: str, prompt_id: str | None, text: str) -> None:
    path = _session_file(session_id)
    entries = _load(path)
    entries.append({"prompt_id": prompt_id, "user_prompt": text, "ts": time.time()})
    _save(path, entries[-config.ring_size():])


def record_response(session_id: str, prompt_id: str | None, text: str) -> None:
    path = _session_file(session_id)
    entries = _load(path)
    target = None
    if prompt_id is not None:
        for entry in reversed(entries):
            if entry.get("prompt_id") == prompt_id:
                target = entry
                break
    if target is None:
        for entry in reversed(entries):
            if "assistant_response" not in entry:
                target = entry
                break
    if target is None:
        return
    target["assistant_response"] = text
    _save(path, entries)


def take_latest_complete(session_id: str) -> dict | None:
    """Newest complete unconsumed pair; marks it consumed (spec: attached once)."""
    path = _session_file(session_id)
    entries = _load(path)
    for entry in reversed(entries):
        if (
            "user_prompt" in entry
            and "assistant_response" in entry
            and not entry.get("consumed")
        ):
            entry["consumed"] = True
            _save(path, entries)
            return {
                "user_prompt": entry["user_prompt"],
                "assistant_response": entry["assistant_response"],
            }
    return None


def take_latest_complete_with_prompt_id(session_id: str) -> tuple[dict | None, str | None]:
    """Like ``take_latest_complete`` plus the ``prompt_id`` the pair was captured
    under (None when the hook did not see one), so the outcome can be linked to
    its turn server-side."""
    path = _session_file(session_id)
    entries = _load(path)
    for entry in reversed(entries):
        if (
            "user_prompt" in entry
            and "assistant_response" in entry
            and not entry.get("consumed")
        ):
            prompt_id = entry.get("prompt_id")
            break
    else:
        return None, None
    pair = take_latest_complete(session_id)
    return pair, prompt_id if isinstance(prompt_id, str) and prompt_id else None


def cleanup_expired(now: float | None = None) -> None:
    d = config.interactions_dir()
    if not d.is_dir():
        return
    cutoff = (now or time.time()) - config.interaction_ttl_seconds()
    for path in d.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            pass
