"""Register the Claude Code artifacts of a plugin or repository (design D10).

    python3 -m ouroboros_client.sync_artifacts <path> [--repo owner/name] \
        [--source plugin|user|project|managed] [--dry-run] [--json]

Scans ``<path>`` for skills (``SKILL.md``), agents (``agents/*.md``), commands
(``commands/*.md``), hooks (``hooks/hooks.json`` entries), the plugin manifest
(``.claude-plugin/plugin.json``), instructions (``CLAUDE.md``,
``.claude/CLAUDE.md``, memory files) and rules (``.claude/rules/*.md``);
computes a sha256 per artifact, parses frontmatter and estimates the tokens an
artifact costs while merely present (always-on: its listing line, capped at
1,536 characters like ``claude plugin details``) and when invoked (body).
Uploads in batches of 100 to ``POST /v1/telemetry/artifacts`` with the same
bearer as outcome events. Artifact text is ``internal`` class (our own source,
already in Git) so bodies are uploaded; files over 256 KB, symlinks leaving the
root, ``.git``/``node_modules``/``.venv`` and anything that looks like a key or
env file are never read.

Explicit command, never a hook: unlike ``report`` it fails loudly (exit 1) when
the API is not configured or an upload is rejected. ``OUROBOROS_TELEMETRY=off``
still wins: nothing is uploaded and the exit code is 0 (``--dry-run`` only scans
locally and keeps working).
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

from . import config

ENDPOINT = "/v1/telemetry/artifacts"
BATCH_SIZE = 100
MAX_FILE_BYTES = 256 * 1024
LISTING_CAP_CHARS = 1536
EXCLUDED_DIRS = {".git", "node_modules", ".venv"}
NEVER_READ = ("*.pem", "*.key", ".env*", ".dev-keys")
KINDS = ("skill", "hook", "agent", "command", "instructions", "rule", "plugin")
SOURCES = ("plugin", "user", "project", "managed")
CLIENT_NAME = "ouroboros-client"

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_GITHUB_REMOTE = re.compile(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$")


# --------------------------------------------------------------------------- tokens


def _tiktoken_counter():
    try:
        import tiktoken  # type: ignore[import-not-found]

        enc = tiktoken.get_encoding("cl100k_base")
        return lambda text: len(enc.encode(text, disallowed_special=()))
    except Exception:
        return None


_COUNTER = _tiktoken_counter()


def estimate_tokens(text: str) -> int:
    """tiktoken when importable, else the chars/4 heuristic (rounded up)."""
    if not text:
        return 0
    if _COUNTER is not None:
        return _COUNTER(text)
    return math.ceil(len(text) / 4)


# ---------------------------------------------------------------------- frontmatter


def _scalar(raw: str):
    s = raw.strip()
    if not s:
        return ""
    if s[0] in "\"'" and s[-1] == s[0] and len(s) >= 2:
        return s[1:-1]
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~"):
        return None
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_scalar(x) for x in inner.split(",")] if inner else []
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split ``---`` YAML frontmatter from the body.

    Stdlib-only (D16) so it understands the subset Claude Code frontmatter uses:
    ``key: value``, quoted strings, ``>``/``|`` block scalars, ``- item`` lists
    and inline ``[a, b]`` lists. Anything else is kept as its raw string.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    body = text[match.end():]
    data: dict = {}
    lines = match.group(1).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#") or line.startswith((" ", "\t")):
            continue
        key, sep, rest = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        rest = rest.strip()
        if rest in (">", "|", ">-", "|-"):
            block: list[str] = []
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            joiner = " " if rest.startswith(">") else "\n"
            data[key] = joiner.join(b for b in block if b or joiner == "\n").strip()
        elif rest == "":
            items: list = []
            while i < len(lines) and lines[i].lstrip().startswith("- "):
                items.append(_scalar(lines[i].lstrip()[2:]))
                i += 1
            data[key] = items if items else None
        else:
            data[key] = _scalar(rest)
    return data, body


# ------------------------------------------------------------------------- scanning


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _never_read(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in NEVER_READ)


def _readable(path: Path, root: Path) -> bool:
    """Regular file, inside ``root`` after resolving symlinks, under the size cap,
    and not on the never-read list."""
    if _never_read(path.name):
        return False
    try:
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root):
                return False
        if not path.is_file():
            return False
        return path.stat().st_size <= MAX_FILE_BYTES
    except OSError:
        return False


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def _git(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def detect_repo(root: Path) -> str:
    """``owner/name`` from the ``origin`` remote (GitHub-style URL), else ''."""
    url = _git(root, "remote", "get-url", "origin")
    if not url:
        return ""
    m = _GITHUB_REMOTE.search(url)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def detect_source(root: Path) -> str:
    if (root / ".claude-plugin" / "plugin.json").is_file():
        return "plugin"
    try:
        if root.resolve() == (Path.home() / ".claude").resolve():
            return "user"
    except OSError:
        pass
    return "project"


def _listing(frontmatter: dict, fallback_name: str) -> tuple[str, bool]:
    """The always-on listing line of a skill: name + description + when_to_use,
    capped at 1,536 characters like the Claude Code skill listing."""
    parts = [str(frontmatter.get("name") or fallback_name)]
    for key in ("description", "when_to_use"):
        value = frontmatter.get(key)
        if value:
            parts.append(str(value))
    listing = ": ".join(parts)
    truncated = len(listing) > LISTING_CAP_CHARS
    return listing[:LISTING_CAP_CHARS], truncated


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_memory_file(rel_parts: tuple[str, ...]) -> bool:
    # ~/.claude/projects/<project>/memory/*.md
    return (
        len(rel_parts) >= 4
        and rel_parts[-2] == "memory"
        and rel_parts[-4] == "projects"
        and rel_parts[-1].endswith(".md")
    )


def _classify(path: Path, root: Path) -> str | None:
    """Which artifact kind a file is, or None when it is not an artifact."""
    rel = path.relative_to(root)
    parts = rel.parts
    name = path.name
    parent = parts[-2] if len(parts) >= 2 else ""
    grandparent = parts[-3] if len(parts) >= 3 else ""
    if name == "SKILL.md":
        return "skill"
    if name == "hooks.json" and parent == "hooks":
        return "hook"
    if name == "plugin.json" and parent == ".claude-plugin":
        return "plugin"
    if name.endswith(".md") and parent == "agents":
        return "agent"
    if name.endswith(".md") and parent == "commands":
        return "command"
    if name.endswith(".md") and parent == "rules" and grandparent == ".claude":
        return "rule"
    if name == "CLAUDE.md" or _is_memory_file(parts):
        return "instructions"
    return None


def _markdown_artifact(kind: str, path: Path, root: Path, base: dict) -> dict:
    text = _read(path)
    frontmatter, body = parse_frontmatter(text)
    fallback = path.parent.name if kind == "skill" else path.stem
    name = str(frontmatter.get("name") or fallback)
    record = dict(
        base,
        kind=kind,
        name=name,
        path=_rel(path, root),
        content_hash=sha256_text(text),
        frontmatter=frontmatter,
        content_text=text,
        estimated_tokens_body=estimate_tokens(body),
    )
    if kind == "skill":
        listing, truncated = _listing(frontmatter, fallback)
        record["estimated_tokens_always_on"] = estimate_tokens(listing)
        record["listing_truncated"] = truncated
    elif kind in ("agent", "command"):
        record["estimated_tokens_always_on"] = estimate_tokens(
            str(frontmatter.get("description") or "")
        )
    else:  # instructions, rule: the whole body is injected
        record["estimated_tokens_always_on"] = estimate_tokens(body)
        if kind == "rule":
            record["scoped_by_globs"] = bool(frontmatter.get("paths"))
    return record


def _hook_artifacts(path: Path, root: Path, base: dict) -> list[dict]:
    text = _read(path)
    try:
        doc = json.loads(text)
    except ValueError:
        return []
    table = doc.get("hooks", doc) if isinstance(doc, dict) else {}
    if not isinstance(table, dict):
        return []
    rel = _rel(path, root)
    records: list[dict] = []
    for event, matchers in table.items():
        if not isinstance(matchers, list):
            continue
        for index, matcher in enumerate(matchers):
            entry = json.dumps(matcher, sort_keys=True, separators=(",", ":"))
            fm = {"event": event, "index": index}
            if isinstance(matcher, dict):
                if matcher.get("matcher") is not None:
                    fm["matcher"] = matcher["matcher"]
                hooks = matcher.get("hooks") or []
                fm["hook_types"] = sorted(
                    {str(h.get("type")) for h in hooks if isinstance(h, dict)}
                )
            records.append(
                dict(
                    base,
                    kind="hook",
                    name=f"{event}#{index}",
                    path=rel,
                    content_hash=sha256_text(entry),
                    frontmatter=fm,
                    content_text=entry,
                    estimated_tokens_always_on=estimate_tokens(entry),
                    estimated_tokens_body=estimate_tokens(entry),
                )
            )
    return records


def _plugin_artifact(path: Path, root: Path, base: dict) -> dict | None:
    text = _read(path)
    try:
        manifest = json.loads(text)
    except ValueError:
        return None
    if not isinstance(manifest, dict):
        return None
    description = str(manifest.get("description") or "")
    return dict(
        base,
        kind="plugin",
        name=str(manifest.get("name") or root.name),
        path=_rel(path, root),
        content_hash=sha256_text(text),
        frontmatter=manifest,
        content_text=text,
        estimated_tokens_always_on=estimate_tokens(description),
        estimated_tokens_body=estimate_tokens(text),
    )


def scan(root: Path | str, *, source: str | None = None, owner_repo: str | None = None) -> list[dict]:
    """Every artifact under ``root`` as the records ``POST /v1/telemetry/artifacts`` takes."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    base = {
        "owner_repo": owner_repo if owner_repo is not None else detect_repo(root),
        "source": source or detect_source(root),
    }
    commit = _git(root, "rev-parse", "HEAD")
    if commit:
        base["commit_sha"] = commit

    records: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            kind = _classify(path, root)
            if kind is None or not _readable(path, root):
                continue
            if kind == "hook":
                records.extend(_hook_artifacts(path, root, base))
            elif kind == "plugin":
                record = _plugin_artifact(path, root, base)
                if record:
                    records.append(record)
            else:
                records.append(_markdown_artifact(kind, path, root, base))
    return records


# -------------------------------------------------------------------------- upload


def _client_version() -> str:
    try:
        from importlib.metadata import version

        return version(CLIENT_NAME)
    except Exception:
        return "0.0.0"


def batches(records: list[dict], size: int = BATCH_SIZE) -> list[list[dict]]:
    return [records[i : i + size] for i in range(0, len(records), size)]


def upload(records: list[dict], api_url: str, token_provider, timeout: float = 30.0) -> dict:
    """POST the records in batches; returns the summed server counters.

    Raises ``RuntimeError`` on a missing token or a non-2xx response so CI fails
    visibly (this is an explicit command, not a hook).
    """
    import httpx

    token = token_provider()
    if not token:
        raise RuntimeError("no bearer token available (OUROBOROS_DEV_KEY_DIR or Entra)")
    totals = {"accepted": 0, "unchanged": 0, "versions_created": 0, "batches": 0}
    client_info = {"name": CLIENT_NAME, "version": _client_version()}
    for batch in batches(records):
        resp = httpx.post(
            f"{api_url.rstrip('/')}{ENDPOINT}",
            json={"artifacts": batch, "client": client_info},
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        if not 200 <= resp.status_code < 300:
            raise RuntimeError(f"{ENDPOINT} returned {resp.status_code}: {resp.text[:500]}")
        body = resp.json() if resp.content else {}
        for key in ("accepted", "unchanged", "versions_created"):
            totals[key] += int(body.get(key, 0) or 0)
        totals["batches"] += 1
    return totals


# ----------------------------------------------------------------------------- CLI


def _public(record: dict) -> dict:
    """A record for ``--json`` output: content length instead of the text."""
    out = {k: v for k, v in record.items() if k != "content_text"}
    out["content_length"] = len(record.get("content_text") or "")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ouroboros-sync-artifacts", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("path", help="plugin or repository root to scan")
    parser.add_argument("--repo", help="owner/name of the owning repo (default: git origin)")
    parser.add_argument("--source", choices=SOURCES, help="default: plugin when a manifest exists, user for ~/.claude, else project")
    parser.add_argument("--dry-run", action="store_true", help="scan and print; upload nothing")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        records = scan(args.path, source=args.source, owner_repo=args.repo)
    except NotADirectoryError as exc:
        print(f"not a directory: {exc}", file=sys.stderr)
        return 2

    summary: dict = {
        "root": str(Path(args.path).expanduser().resolve()),
        "count": len(records),
        "kinds": _count_kinds(records),
        "dry_run": args.dry_run,
    }
    if records:
        summary["owner_repo"] = records[0]["owner_repo"]
        summary["source"] = records[0]["source"]
        summary["commit_sha"] = records[0].get("commit_sha")

    if args.dry_run:
        if args.json:
            summary["artifacts"] = [_public(r) for r in records]
            print(json.dumps(summary, indent=2))
        else:
            _print_table(records, summary)
        return 0

    if not config.telemetry_enabled():
        print("OUROBOROS_TELEMETRY=off: nothing uploaded", file=sys.stderr)
        return 0
    api_url = config.api_url()
    if not api_url:
        print("OUROBOROS_API_URL is not set", file=sys.stderr)
        return 1
    from .client import default_token_provider

    try:
        summary.update(upload(records, api_url, default_token_provider()))
    except Exception as exc:
        summary["error"] = str(exc)
        print(json.dumps(summary) if args.json else f"upload failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary))
    else:
        print(
            f"synced {summary['count']} artifacts in {summary['batches']} batch(es): "
            f"{summary['versions_created']} new version(s), {summary['unchanged']} unchanged"
        )
    return 0


def _count_kinds(records: list[dict]) -> dict:
    counts: dict = {}
    for r in records:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    return counts


def _print_table(records: list[dict], summary: dict) -> None:
    print(f"{summary['root']}  ({summary['count']} artifacts, dry run)")
    if records:
        print(f"source={summary['source']} repo={summary['owner_repo'] or '-'} commit={(summary.get('commit_sha') or '-')[:12]}")
    for r in records:
        flags = []
        if r.get("listing_truncated"):
            flags.append("listing_truncated")
        if r.get("scoped_by_globs"):
            flags.append("scoped_by_globs")
        print(
            f"  {r['kind']:<12} {r['name']:<32} always_on={r['estimated_tokens_always_on']:>6} "
            f"body={r['estimated_tokens_body']:>6}  {r['path']}"
            + (f"  [{', '.join(flags)}]" if flags else "")
        )


if __name__ == "__main__":
    sys.exit(main())
