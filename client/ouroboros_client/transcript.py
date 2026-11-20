"""Claude Code transcript parser and opt-in uploader (design D8, task 2.6).

    python3 -m ouroboros_client.transcript upload <path> [--content]
    python3 -m ouroboros_client.transcript upload --session <id> [--content]
    python3 -m ouroboros_client.transcript backfill --days N [--content]
    python3 -m ouroboros_client.transcript consent
    python3 -m ouroboros_client.transcript watch        # stub, see below

Transcripts live in ``~/.claude/projects/<project>/<session>.jsonl`` with
subagent transcripts under ``<session>/subagents/agent-<id>.jsonl``. The
parser relies on this structure:

- Every line is a JSON object with a ``type`` discriminator; the schema is
  per ``type`` and every key beyond the envelope is optional. Unknown keys
  are ignored; unknown ``type`` values are counted and skipped.
- Only chain lines (``user``, ``assistant``, ``system``, ``attachment``) are
  uploaded. Side-channel lines (``mode``, ``last-prompt``, ``cost-state`` ...)
  are counted as skipped. The never-list (``api-request-blob`` and the other
  types in ``NEVER_TYPES``) is dropped before the JSON is decoded; the side
  directories ``memory/`` and ``tool-results/`` are never opened.
- One API response is written as several ``assistant`` lines with an identical
  ``usage`` object: usage is attached to the first line of each
  ``message.id`` only, so sums over lines do not over-count.
- Lines are emitted in ``parentUuid`` chain order (``seq``), never timestamp
  order; the host clock can step back. ``prompt_id`` lives on ``user`` lines
  only and is inherited along the chain by the assistant/system/attachment
  lines of the same turn (verified: it equals the hook ``prompt_id`` and the
  OTel ``prompt.id``).
- Default output is metadata only. ``--content`` adds the text of human
  prompts and assistant text/thinking blocks (sensitive class) and requires a
  consent record in ``~/.ouroboros/consent.json``. Tool inputs, tool results,
  attachments, thinking signatures and image bytes are never included.

Read point: the final ``assistant`` line of a turn is NOT on disk when that
turn's own ``Stop`` hook runs (task 2.10); it appears by the next
``UserPromptSubmit`` or at ``SessionEnd``. ``upload``/``backfill`` therefore
read finished sessions, and the future ``watch`` mode must read a turn at the
next ``UserPromptSubmit`` and the last turn at ``SessionEnd`` - never at the
turn's own ``Stop``. ``watch`` is a stub in this task.

This module never runs from a hook; it may use the SDK's httpx client and
token providers (design D16 keeps only the hook path stdlib-only).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

CHAIN_TYPES = frozenset({"user", "assistant", "system", "attachment"})

# Never uploaded in any mode and never decoded (spike 0.1 section 10).
NEVER_TYPES = frozenset(
    {
        "api-request-blob",
        "file-history-snapshot",
        "file-history-delta",
        "queue-operation",
        "frame-link",
    }
)

# Known non-chain line types (spike 0.1 section 2): counted, not uploaded.
SIDE_CHANNEL_TYPES = frozenset(
    {
        "api-request",
        "api-request-shape",
        "last-prompt",
        "ai-title",
        "mode",
        "permission-mode",
        "atis-latch",
        "session-owner",
        "pr-link",
        "tools-used",
        "cost-state",
        "summary",
    }
)

# Side directories under <project>/ that are never opened.
NEVER_DIRS = frozenset({"memory", "tool-results"})
NEVER_FILE_PREFIXES = ("api-request-blob",)

SESSION_FILE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jsonl$")
AGENT_FILE = re.compile(r"^agent-(?P<agent_id>[0-9a-zA-Z_-]+)\.jsonl$")
_NEVER_TYPE_RE = re.compile(
    r'"type"\s*:\s*"(' + "|".join(re.escape(t) for t in sorted(NEVER_TYPES)) + r')"'
)

BATCH_SIZE = 500
DEFAULT_RETENTION_DAYS = 30
CLIENT_NAME = "ouroboros-client"

CONSENT_TEXT = """\
Transcript content upload (--content) sends the text of your prompts and of the
assistant's replies and thinking to the Ouroboros ingestion API, classified
"sensitive" and retained per RETENTION_SENSITIVE_DAYS (default 30 days). Tool
inputs and outputs, attachments, thinking signatures and images are never
sent. Metadata-only upload (the default) needs no consent. Consent is recorded
in ~/.ouroboros/consent.json and can be withdrawn by deleting that file.
"""


class NeverListError(ValueError):
    """The path is on the never-list (memory/, tool-results/, api-request-blob*)."""


@dataclass
class ParseStats:
    total: int = 0
    emitted: int = 0
    skipped_never: int = 0
    skipped_side_channel: int = 0
    skipped_unknown: int = 0
    skipped_malformed: int = 0
    deduped_usage: int = 0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class ParsedTranscript:
    session_id: str | None
    agent_id: str | None
    lines: list[dict]
    stats: ParseStats
    transcript_hash: str | None = None
    versions: set = field(default_factory=set)

    def batches(self, size: int = BATCH_SIZE) -> Iterator[list[dict]]:
        for i in range(0, len(self.lines), size):
            yield self.lines[i : i + size]


# --- path guards -------------------------------------------------------------

def check_path_allowed(path: Path) -> None:
    """Refuse anything under memory/ or tool-results/ or named api-request-blob*."""
    for part in path.parts:
        if part in NEVER_DIRS:
            raise NeverListError(f"{path}: {part}/ is never read")
    if path.name.startswith(NEVER_FILE_PREFIXES):
        raise NeverListError(f"{path}: never-list file")


def agent_id_from_path(path: Path) -> str | None:
    match = AGENT_FILE.match(path.name)
    return match["agent_id"] if match else None


# --- per-line extraction -------------------------------------------------------

def _text_len(value) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        total = 0
        for block in value:
            if isinstance(block, str):
                total += len(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    total += len(block.get("text") or "")
                elif block.get("type") == "tool_result":
                    total += _text_len(block.get("content"))
        return total
    return 0


def _texts(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [b.get("text") or "" for b in value if isinstance(b, dict) and b.get("type") == "text"]
    return []


def _usage(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    details = raw.get("output_tokens_details")
    thinking = details.get("thinking_tokens") if isinstance(details, dict) else None

    def _int(key):
        v = raw.get(key)
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    usage = {
        "input_tokens": _int("input_tokens"),
        "output_tokens": _int("output_tokens"),
        "cache_read_tokens": _int("cache_read_input_tokens"),
        "cache_creation_tokens": _int("cache_creation_input_tokens"),
        "thinking_tokens": thinking if isinstance(thinking, int) else None,
    }
    return usage if any(v is not None for v in usage.values()) else None


def extract_line(obj: dict, *, with_content: bool, tool_names: dict[str, str]) -> dict | None:
    """Project one chain line to its metadata record; None when malformed.

    ``tool_names`` maps tool_use ids to tool names across the file so a
    tool_result line can be labelled with the tool it answers.
    """
    kind = obj.get("type")
    uuid = obj.get("uuid")
    timestamp = obj.get("timestamp")
    if not isinstance(uuid, str) or not isinstance(timestamp, str):
        return None

    record: dict = {
        "line_uuid": uuid,
        "parent_uuid": obj.get("parentUuid") if isinstance(obj.get("parentUuid"), str) else None,
        "type": kind,
        "subtype": None,
        "prompt_id": obj.get("promptId") if isinstance(obj.get("promptId"), str) else None,
        "role": None,
        "model": None,
        "message_id": None,
        "request_id": obj.get("requestId") if isinstance(obj.get("requestId"), str) else None,
        "stop_reason": None,
        "timestamp": timestamp,
        "usage": None,
        "tool_use_ids": [],
        "tool_names": [],
        "content_chars": None,
        "is_sidechain": bool(obj.get("isSidechain", False)),
        "is_error": None,
        "attribution_skill": (
            obj.get("attributionSkill") if isinstance(obj.get("attributionSkill"), str) else None
        ),
        "version": obj.get("version") if isinstance(obj.get("version"), str) else None,
    }
    message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    content = message.get("content")
    texts: list[str] = []

    if kind == "user":
        record["role"] = "user"
        if obj.get("isCompactSummary"):
            record["subtype"] = "compact_summary"
        elif obj.get("isMeta"):
            record["subtype"] = "meta"
        else:
            record["subtype"] = "prompt"
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    record["subtype"] = "tool_result"
                    tid = block.get("tool_use_id")
                    if isinstance(tid, str):
                        record["tool_use_ids"].append(tid)
                        if tid in tool_names:
                            record["tool_names"].append(tool_names[tid])
                    if isinstance(block.get("is_error"), bool):
                        record["is_error"] = block["is_error"]
        record["content_chars"] = _text_len(content)
        if record["subtype"] in ("prompt", "compact_summary"):
            texts = _texts(content)

    elif kind == "assistant":
        record["role"] = "assistant"
        model = message.get("model")
        record["model"] = model if isinstance(model, str) else None
        mid = message.get("id")
        record["message_id"] = mid if isinstance(mid, str) else None
        stop = message.get("stop_reason")
        record["stop_reason"] = stop if isinstance(stop, str) else None
        record["usage"] = _usage(message.get("usage"))
        if obj.get("isApiErrorMessage") or obj.get("isAbortedMidStream"):
            record["is_error"] = True
        chars = 0
        block_types = []
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            block_types.append(btype)
            if btype == "tool_use":
                tid, name = block.get("id"), block.get("name")
                if isinstance(tid, str):
                    record["tool_use_ids"].append(tid)
                    if isinstance(name, str):
                        record["tool_names"].append(name)
                        tool_names[tid] = name
                try:
                    chars += len(json.dumps(block.get("input"), separators=(",", ":")))
                except (TypeError, ValueError):
                    pass
            elif btype == "text":
                chars += len(block.get("text") or "")
                texts.append(block.get("text") or "")
            elif btype == "thinking":
                chars += len(block.get("thinking") or "")
                if block.get("thinking"):
                    texts.append(block["thinking"])
        record["subtype"] = block_types[0] if len(block_types) == 1 else (",".join(block_types) or None)
        record["content_chars"] = chars

    elif kind == "system":
        sub = obj.get("subtype")
        record["subtype"] = sub if isinstance(sub, str) else None
        record["content_chars"] = _text_len(obj.get("content"))
        # Never content: system lines carry hook command lines and summaries.

    elif kind == "attachment":
        att = obj.get("attachment") if isinstance(obj.get("attachment"), dict) else {}
        sub = att.get("type")
        record["subtype"] = sub if isinstance(sub, str) else None
        rendered = obj.get("rendered")
        record["content_chars"] = sum(
            _text_len(r.get("content")) for r in rendered if isinstance(r, dict)
        ) if isinstance(rendered, list) else 0

    if with_content and any(texts):
        record["content"] = "\n".join(texts)
    return record


# --- file parsing --------------------------------------------------------------

def iter_json_lines(path: Path, stats: ParseStats) -> Iterator[dict]:
    """Stream the file; drop never-list lines before decoding them."""
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            stats.total += 1
            if _NEVER_TYPE_RE.search(raw):
                stats.skipped_never += 1
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                stats.skipped_malformed += 1
                continue
            if not isinstance(obj, dict):
                stats.skipped_malformed += 1
                continue
            yield obj


def chain_order(records: list[dict]) -> list[dict]:
    """Order by the parentUuid chain, file order among siblings and roots.

    A record is emitted once its parent has been (or when its parent is
    missing from the file, which makes it a root like a compact_boundary).
    """
    by_uuid = {r["line_uuid"]: r for r in records}
    emitted: set[str] = set()
    pending: dict[str, list[dict]] = {}
    ordered: list[dict] = []

    def emit(rec: dict) -> None:
        stack = [rec]
        while stack:
            cur = stack.pop()
            if cur["line_uuid"] in emitted:
                continue
            emitted.add(cur["line_uuid"])
            ordered.append(cur)
            children = pending.pop(cur["line_uuid"], [])
            stack.extend(reversed(children))

    for rec in records:
        parent = rec["parent_uuid"]
        if parent is None or parent not in by_uuid or parent in emitted:
            emit(rec)
        else:
            pending.setdefault(parent, []).append(rec)
    # Cycles or orphans whose parent never emitted (should not happen): append in file order.
    for rec in records:
        if rec["line_uuid"] not in emitted:
            emit(rec)
    for i, rec in enumerate(ordered):
        rec["seq"] = i
    return ordered


def inherit_prompt_ids(ordered: list[dict]) -> None:
    """Fill prompt_id on chain lines from the nearest ancestor that carries one."""
    by_uuid = {r["line_uuid"]: r for r in ordered}
    for rec in ordered:  # parents precede children in chain order
        if rec["prompt_id"] is None:
            parent = by_uuid.get(rec["parent_uuid"]) if rec["parent_uuid"] else None
            if parent is not None:
                rec["prompt_id"] = parent["prompt_id"]


def dedupe_usage(ordered: list[dict], stats: ParseStats) -> None:
    """Keep usage on the first line of each assistant message.id only."""
    seen: set[str] = set()
    for rec in ordered:
        if rec["type"] != "assistant" or rec["usage"] is None:
            continue
        key = rec["message_id"] or rec["request_id"]
        if key is None:
            continue
        if key in seen:
            rec["usage"] = None
            stats.deduped_usage += 1
        else:
            seen.add(key)


def parse_transcript(path: Path, *, with_content: bool = False) -> ParsedTranscript:
    path = Path(path)
    check_path_allowed(path)
    stats = ParseStats()
    records: list[dict] = []
    tool_names: dict[str, str] = {}
    session_id: str | None = None
    agent_id = agent_id_from_path(path)
    versions: set = set()

    for obj in iter_json_lines(path, stats):
        kind = obj.get("type")
        if kind in NEVER_TYPES:  # belt and braces: the regex pass already dropped these
            stats.skipped_never += 1
            continue
        if kind in SIDE_CHANNEL_TYPES:
            stats.skipped_side_channel += 1
            continue
        if kind not in CHAIN_TYPES:
            stats.skipped_unknown += 1
            continue
        record = extract_line(obj, with_content=with_content, tool_names=tool_names)
        if record is None:
            stats.skipped_malformed += 1
            continue
        sid = obj.get("sessionId") or obj.get("session_id")
        if session_id is None and isinstance(sid, str):
            session_id = sid
        aid = obj.get("agentId")
        if agent_id is None and isinstance(aid, str):
            agent_id = aid
        if record["version"]:
            versions.add(record["version"])
        records.append(record)

    ordered = chain_order(records)
    inherit_prompt_ids(ordered)
    dedupe_usage(ordered, stats)
    stats.emitted = len(ordered)
    digest = hashlib.sha256("\n".join(r["line_uuid"] for r in ordered).encode()).hexdigest()
    return ParsedTranscript(
        session_id=session_id,
        agent_id=agent_id,
        lines=ordered,
        stats=stats,
        transcript_hash=digest if ordered else None,
        versions=versions,
    )


# --- discovery ---------------------------------------------------------------------

def projects_dir() -> Path:
    return Path("~/.claude/projects").expanduser()


def subagent_files(session_file: Path) -> list[Path]:
    side = session_file.with_suffix("") / "subagents"
    if not side.is_dir():
        return []
    return sorted(p for p in side.iterdir() if AGENT_FILE.match(p.name))


def discover_sessions(root: Path) -> list[Path]:
    """Session transcripts under ``root`` (``<project>/<uuid>.jsonl``); nothing
    under ``memory/`` or ``tool-results/`` is ever listed or opened."""
    found: list[Path] = []
    if not root.is_dir():
        return found
    for project in sorted(root.iterdir()):
        if not project.is_dir() or project.name in NEVER_DIRS:
            continue
        for entry in sorted(project.iterdir()):
            if entry.is_file() and SESSION_FILE.match(entry.name):
                found.append(entry)
    return found


def find_session(root: Path, session_id: str) -> Path | None:
    for project in sorted(root.iterdir()) if root.is_dir() else []:
        candidate = project / f"{session_id}.jsonl"
        if project.is_dir() and candidate.is_file():
            return candidate
    return None


def last_timestamp(parsed: ParsedTranscript) -> datetime | None:
    stamps = [_parse_ts(l["timestamp"]) for l in parsed.lines]
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# --- retention bound and consent ----------------------------------------------------

def local_retention_days(settings_path: Path | None = None) -> int:
    """``cleanupPeriodDays`` from ~/.claude/settings.json (the only key read); default 30."""
    path = settings_path or Path("~/.claude/settings.json").expanduser()
    try:
        value = json.loads(path.read_text()).get("cleanupPeriodDays")
    except (OSError, ValueError, AttributeError):
        return DEFAULT_RETENTION_DAYS
    return value if isinstance(value, int) and value > 0 else DEFAULT_RETENTION_DAYS


def bounded_days(requested: int, settings_path: Path | None = None) -> int:
    return max(1, min(requested, local_retention_days(settings_path)))


def consent_path() -> Path:
    return config.base_dir() / "consent.json"


def has_content_consent() -> bool:
    try:
        return json.loads(consent_path().read_text()).get("transcript_content") is True
    except (OSError, ValueError, AttributeError):
        return False


def record_content_consent() -> Path:
    path = consent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"transcript_content": True, "granted_at": datetime.now(timezone.utc).isoformat()},
            indent=2,
        )
        + "\n"
    )
    return path


# --- upload --------------------------------------------------------------------------

def client_info() -> dict:
    try:
        from importlib.metadata import version

        ver = version("ouroboros-client")
    except Exception:
        ver = "unknown"
    return {"name": CLIENT_NAME, "version": ver}


class Uploader:
    """Synchronous batched upload with the SDK's token provider; raises on failure."""

    def __init__(self, api_url: str | None = None, token_provider=None, timeout: float = 30.0):
        self.api_url = (api_url or config.api_url() or "").rstrip("/")
        if token_provider is None:
            from .client import default_token_provider

            token_provider = default_token_provider()
        self._token_provider = token_provider
        self._timeout = timeout

    def upload(self, parsed: ParsedTranscript, *, content_included: bool) -> dict:
        import httpx

        if not self.api_url:
            raise RuntimeError("OUROBOROS_API_URL is not set")
        if not parsed.session_id:
            raise RuntimeError("transcript carries no sessionId")
        token = self._token_provider()
        if not token:
            raise RuntimeError("no bearer token available (OUROBOROS_DEV_KEY_DIR or Entra)")
        totals = {"batches": 0, "lines": 0, "accepted": 0, "duplicates": 0}
        for batch in parsed.batches():
            body = {
                "session_id": parsed.session_id,
                "lines": batch,
                "content_included": content_included,
                "client": client_info(),
            }
            if parsed.agent_id:
                body["agent_id"] = parsed.agent_id
                body["transcript_hash"] = parsed.transcript_hash
            resp = httpx.post(
                f"{self.api_url}/v1/telemetry/transcripts",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout,
            )
            if resp.status_code != 202:
                raise RuntimeError(f"upload failed: HTTP {resp.status_code} {resp.text[:200]}")
            data = resp.json()
            totals["batches"] += 1
            totals["lines"] += len(batch)
            totals["accepted"] += int(data.get("accepted", 0))
            totals["duplicates"] += int(data.get("duplicates", 0))
        return totals


def upload_session(
    session_file: Path, uploader: Uploader, *, with_content: bool, include_subagents: bool = True
) -> list[dict]:
    """Upload a session transcript and its subagent transcripts; return per-file summaries."""
    results = []
    files = [session_file] + (subagent_files(session_file) if include_subagents else [])
    for path in files:
        parsed = parse_transcript(path, with_content=with_content)
        summary = {"path": str(path), "agent_id": parsed.agent_id, "stats": parsed.stats.as_dict()}
        if parsed.lines:
            summary["upload"] = uploader.upload(parsed, content_included=with_content)
        results.append(summary)
    return results


# --- CLI ---------------------------------------------------------------------------------

def _require_consent_for_content(args) -> bool:
    if not args.content:
        return True
    if has_content_consent():
        return True
    print(CONSENT_TEXT, file=sys.stderr)
    print(
        "No consent recorded: run `python3 -m ouroboros_client.transcript consent` first. "
        "Nothing was uploaded.",
        file=sys.stderr,
    )
    return False


def cmd_consent(args) -> int:
    print(CONSENT_TEXT)
    if has_content_consent():
        print(f"Consent already recorded in {consent_path()}")
        return 0
    answer = "yes" if args.yes else input("Type 'yes' to record consent: ").strip().lower()
    if answer != "yes":
        print("Consent not recorded.")
        return 2
    print(f"Consent recorded in {record_content_consent()}")
    return 0


def cmd_upload(args) -> int:
    if not config.telemetry_enabled():
        print("OUROBOROS_TELEMETRY=off: nothing uploaded", file=sys.stderr)
        return 0
    if not _require_consent_for_content(args):
        return 2
    root = Path(args.projects_dir).expanduser()
    if args.session:
        path = find_session(root, args.session)
        if path is None:
            print(f"session {args.session} not found under {root}", file=sys.stderr)
            return 1
    else:
        path = Path(args.path).expanduser()
    try:
        check_path_allowed(path)
        results = upload_session(path, Uploader(), with_content=args.content)
    except (NeverListError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(results, indent=2))
    return 0


def cmd_backfill(args) -> int:
    if not config.telemetry_enabled():
        print("OUROBOROS_TELEMETRY=off: nothing uploaded", file=sys.stderr)
        return 0
    if not _require_consent_for_content(args):
        return 2
    root = Path(args.projects_dir).expanduser()
    days = bounded_days(args.days)
    if days != args.days:
        print(
            f"--days {args.days} bounded to {days} by the local cleanupPeriodDays retention",
            file=sys.stderr,
        )
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    uploader = Uploader()
    results = []
    for session_file in discover_sessions(root):
        head = parse_transcript(session_file)
        last = last_timestamp(head)
        if last is None or last < cutoff:
            continue
        try:
            results.extend(upload_session(session_file, uploader, with_content=args.content))
        except (RuntimeError, OSError) as exc:
            print(f"error: {session_file}: {exc}", file=sys.stderr)
            return 1
    print(json.dumps({"days": days, "sessions": len({r['path'] for r in results if not r['agent_id']}), "files": results}, indent=2))
    return 0


def cmd_watch(args) -> int:
    print(
        "watch is not implemented yet. Planned read point (task 2.10): read a turn at the "
        "next UserPromptSubmit and the last turn at SessionEnd, never at the turn's own Stop "
        "(the final assistant line is written after the stop hooks).",
        file=sys.stderr,
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python3 -m ouroboros_client.transcript", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--content", action="store_true", help="include prompt/assistant text (needs consent)")
        sp.add_argument("--projects-dir", default=str(projects_dir()), help=argparse.SUPPRESS)

    up = sub.add_parser("upload", help="upload one session transcript (and its subagents)")
    up.add_argument("path", nargs="?", help="path to <session>.jsonl or agent-<id>.jsonl")
    up.add_argument("--session", help="session id to look up under ~/.claude/projects")
    common(up)
    up.set_defaults(func=cmd_upload)

    bf = sub.add_parser("backfill", help="upload every session active in the last N days")
    bf.add_argument("--days", type=int, required=True)
    common(bf)
    bf.set_defaults(func=cmd_backfill)

    co = sub.add_parser("consent", help="print the disclosure and record consent for --content")
    co.add_argument("--yes", action="store_true", help="record consent without prompting")
    co.set_defaults(func=cmd_consent)

    w = sub.add_parser("watch", help="(stub) follow the current session")
    w.set_defaults(func=cmd_watch)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "upload" and not args.path and not args.session:
        print("upload needs a path or --session", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
