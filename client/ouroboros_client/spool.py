"""Spool-first event store (design D1 / D6). Stdlib-only.

Two spools share one layout — each record is one JSON file named
<epoch_ns>-<id>.json so write order and freshness partitioning need only the
filename (older files from 0.1.x used <epoch_ms>; both parse):

- `spool/`        outcome events (`event_id`), cap 500, stale-partitioned
- `spool/hooks/`  hook-event records (`hook_event_id`), cap 5,000, never
                  stale-dropped client-side (the server accepts 7 days)
"""

import json
import os
import threading
import time
from pathlib import Path

from . import config


def _atomic_write(path: Path, data: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(data)
    os.replace(tmp, path)


_stamp_lock = threading.Lock()
_last_stamp = 0


def _stamp() -> int:
    """Nanosecond timestamp that never repeats within this process: file names
    sort in write order even when the clock is coarse (two writes can share a
    ``time_ns`` value on some hosts) or steps back."""
    global _last_stamp
    with _stamp_lock:
        _last_stamp = max(time.time_ns(), _last_stamp + 1)
        return _last_stamp


def _write(d: Path, record_id: str, record: dict, cap: int) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{_stamp():020d}-{record_id}.json"
    _atomic_write(path, json.dumps(record))
    _enforce_cap(d, cap)
    return path


def write_event(event: dict) -> Path:
    return _write(config.spool_dir(), event["event_id"], event, config.spool_cap())


def write_hook_event(record: dict) -> Path:
    return _write(
        config.hook_spool_dir(), record["hook_event_id"], record, config.hook_spool_cap()
    )


def _enforce_cap(d: Path, cap: int) -> None:
    files = sorted(p for p in d.glob("*.json") if p.is_file())
    excess = len(files) - cap
    for path in files[: max(0, excess)]:
        path.unlink(missing_ok=True)


def _list(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.json") if p.is_file())


def list_files() -> list[Path]:
    return _list(config.spool_dir())


def list_hook_files() -> list[Path]:
    return _list(config.hook_spool_dir())


def file_epoch_ms(path: Path) -> int:
    """Write time encoded in the filename (ns in 0.2.x, ms before)."""
    try:
        prefix = int(path.name.split("-", 1)[0])
    except ValueError:
        return 0
    return prefix // 1_000_000 if prefix > 10**15 else prefix


def partition_fresh_stale(files: list[Path]) -> tuple[list[Path], list[Path]]:
    """Split by the server freshness window so stale events get their own batch
    (design D2 — a stale rejection must not poison fresh events)."""
    cutoff_ms = (time.time() - config.freshness_window_seconds()) * 1000
    fresh, stale = [], []
    for path in files:
        (fresh if file_epoch_ms(path) >= cutoff_ms else stale).append(path)
    return fresh, stale


def load(files: list[Path]) -> list[dict]:
    events = []
    for path in files:
        try:
            events.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
    return events


def remove(files: list[Path]) -> None:
    for path in files:
        path.unlink(missing_ok=True)
