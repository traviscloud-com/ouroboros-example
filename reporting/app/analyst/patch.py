"""Strict unified-diff parsing and application (stdlib only).

The analyst's ``unified_diff`` must touch exactly one file - the target
artifact's path - and apply cleanly to the stored ``content_text``; GitHub PR
creation applies the same diff to the file on the base branch. "Cleanly" means
every hunk's context and removed lines match exactly at the stated position
(no fuzz, no offset search): a diff that only applies approximately is
rejected rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(ValueError):
    """The diff is malformed, touches the wrong path, or does not apply."""


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    lines: list[tuple[str, str]] = field(default_factory=list)  # (" "|"-"|"+", text)


@dataclass
class FilePatch:
    old_path: str | None
    new_path: str | None
    hunks: list[Hunk]
    # "\ No newline at end of file" after the last new-side line
    new_missing_newline: bool = False

    @property
    def path(self) -> str | None:
        return self.new_path or self.old_path


def _strip_prefix(raw: str) -> str | None:
    path = raw.split("\t", 1)[0].strip()
    if path == "/dev/null":
        return None
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return path.lstrip("./") if path.startswith("./") else path


def parse(diff: str) -> list[FilePatch]:
    """Parse a unified diff into per-file patches; raise PatchError when malformed."""
    if not diff or not diff.strip():
        raise PatchError("empty diff")
    lines = diff.splitlines()
    patches: list[FilePatch] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            if i + 1 >= len(lines) or not lines[i + 1].startswith("+++ "):
                raise PatchError("'---' header without '+++'")
            patch = FilePatch(_strip_prefix(line[4:]), _strip_prefix(lines[i + 1][4:]), [])
            patches.append(patch)
            i += 2
            continue
        m = _HUNK.match(line)
        if m:
            if not patches:
                raise PatchError("hunk before any file header")
            hunk = Hunk(
                int(m.group(1)), int(m.group(2) or 1), int(m.group(3)), int(m.group(4) or 1)
            )
            patches[-1].hunks.append(hunk)
            i += 1
            old_seen = new_seen = 0
            while i < len(lines) and (old_seen < hunk.old_len or new_seen < hunk.new_len):
                body = lines[i]
                tag, text = (body[:1], body[1:]) if body else (" ", "")
                if tag == "\\":
                    i += 1
                    continue
                if tag not in (" ", "-", "+"):
                    raise PatchError(f"unexpected line in hunk: {body[:40]!r}")
                hunk.lines.append((tag, text))
                if tag in (" ", "-"):
                    old_seen += 1
                if tag in (" ", "+"):
                    new_seen += 1
                i += 1
            if old_seen != hunk.old_len or new_seen != hunk.new_len:
                raise PatchError("hunk line counts do not match its header")
            if i < len(lines) and lines[i].startswith("\\"):
                # marker applies to the line just consumed
                if hunk.lines and hunk.lines[-1][0] in (" ", "+"):
                    patches[-1].new_missing_newline = True
                i += 1
            continue
        if line.startswith(("diff --git", "index ", "new file mode", "deleted file mode",
                            "similarity index", "rename from", "rename to")) or not line.strip():
            i += 1
            continue
        raise PatchError(f"unexpected line outside a hunk: {line[:40]!r}")
    if not patches:
        raise PatchError("no file headers ('---'/'+++') found")
    for p in patches:
        if not p.hunks:
            raise PatchError(f"no hunks for {p.path}")
    return patches


def single_file_patch(diff: str, expected_path: str) -> FilePatch:
    """The diff must modify exactly ``expected_path`` (no create, delete or rename)."""
    patches = parse(diff)
    expected = _strip_prefix(expected_path)
    paths = {p.old_path for p in patches} | {p.new_path for p in patches}
    if len(patches) != 1 or paths != {expected}:
        shown = sorted(str(p) for p in paths)
        raise PatchError(f"diff touches {shown}; only {expected!r} may change")
    return patches[0]


def apply(content: str, patch: FilePatch) -> str:
    """Apply ``patch`` to ``content`` exactly; raise PatchError on any mismatch."""
    trailing_newline = content.endswith("\n")
    old = content.split("\n")
    if trailing_newline:
        old = old[:-1]
    out: list[str] = []
    cursor = 0  # index into old
    for hunk in patch.hunks:
        start = hunk.old_start - 1 if hunk.old_len else hunk.old_start
        if start < cursor or start > len(old):
            raise PatchError(f"hunk at line {hunk.old_start} is out of order or out of range")
        out.extend(old[cursor:start])
        pos = start
        for tag, text in hunk.lines:
            if tag in (" ", "-"):
                if pos >= len(old) or old[pos] != text:
                    raise PatchError(f"context mismatch at line {pos + 1}")
                if tag == " ":
                    out.append(text)
                pos += 1
            else:
                out.append(text)
        cursor = pos
    out.extend(old[cursor:])
    result = "\n".join(out)
    reaches_end = cursor >= len(old)
    if out and not patch.new_missing_newline and (trailing_newline or reaches_end):
        result += "\n"
    return result


def changed_old_lines(patch: FilePatch) -> set[int]:
    """1-based line numbers of the original touched by removals or insertions."""
    touched: set[int] = set()
    for hunk in patch.hunks:
        pos = hunk.old_start if hunk.old_len else hunk.old_start + 1
        for tag, _ in hunk.lines:
            if tag == " ":
                pos += 1
            elif tag == "-":
                touched.add(pos)
                pos += 1
            else:  # insertion lands before line `pos`
                touched.add(max(pos - 1, 1))
                touched.add(pos)
    return touched


def frontmatter_lines(content: str) -> set[int]:
    """1-based line numbers of a leading ``---`` YAML frontmatter block (incl. fences)."""
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return set()
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return set(range(1, idx + 2))
    return set()
