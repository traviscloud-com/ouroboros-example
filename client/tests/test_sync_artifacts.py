"""sync_artifacts: scan a synthesized plugin tree, estimate tokens, upload in batches."""

import hashlib
import json
import os

import pytest

from ouroboros_client import sync_artifacts as sa

SKILL = """---
name: demo-skill
description: Demo skill that does a thing.
when_to_use: >
  Use when the user asks for the thing,
  or mentions things.
allowed-tools: [Read, Bash]
---

# Demo skill

Body paragraph one.

Body paragraph two.
"""

AGENT = """---
name: reviewer
description: Reviews diffs for correctness.
model: sonnet
---
You are a reviewer.
"""

COMMAND = """---
description: Run the deploy checklist.
---
Deploy steps here.
"""

HOOKS = {
    "hooks": {
        "UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": "python3 x.py", "async": True, "timeout": 10}]}
        ],
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 y.py"}]},
            {"matcher": "Write", "hooks": [{"type": "command", "command": "python3 z.py"}]},
        ],
    }
}

MANIFEST = {"name": "demo-plugin", "description": "A demo plugin.", "version": "0.1.0"}

RULE_SCOPED = """---
paths:
  - "src/**/*.py"
---
Scoped rule body.
"""

RULE_GLOBAL = "Always-on rule body.\n"


@pytest.fixture()
def plugin_tree(tmp_path):
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(MANIFEST))
    (root / "skills" / "demo").mkdir(parents=True)
    (root / "skills" / "demo" / "SKILL.md").write_text(SKILL)
    (root / "agents").mkdir()
    (root / "agents" / "reviewer.md").write_text(AGENT)
    (root / "commands").mkdir()
    (root / "commands" / "deploy.md").write_text(COMMAND)
    (root / "hooks").mkdir()
    (root / "hooks" / "hooks.json").write_text(json.dumps(HOOKS))
    (root / "CLAUDE.md").write_text("# Project\n\nInstructions body.\n")
    (root / ".claude" / "rules").mkdir(parents=True)
    (root / ".claude" / "rules" / "scoped.md").write_text(RULE_SCOPED)
    (root / ".claude" / "rules" / "global.md").write_text(RULE_GLOBAL)
    (root / "README.md").write_text("not an artifact\n")
    return root


def by_kind(records, kind):
    return [r for r in records if r["kind"] == kind]


def test_scan_finds_every_kind(plugin_tree):
    records = sa.scan(plugin_tree, owner_repo="acme/demo", source="plugin")
    kinds = sorted(r["kind"] for r in records)
    assert kinds == sorted(
        ["plugin", "skill", "agent", "command", "hook", "hook", "hook", "instructions", "rule", "rule"]
    )
    assert all(r["owner_repo"] == "acme/demo" and r["source"] == "plugin" for r in records)
    assert all(not os.path.isabs(r["path"]) for r in records)
    skill = by_kind(records, "skill")[0]
    assert skill["name"] == "demo-skill"
    assert skill["path"] == "skills/demo/SKILL.md"
    assert "commit_sha" not in skill  # tmp_path is not a git repo


def test_frontmatter_parse_and_body_split(plugin_tree):
    fm, body = sa.parse_frontmatter(SKILL)
    assert fm["name"] == "demo-skill"
    assert fm["description"] == "Demo skill that does a thing."
    assert fm["when_to_use"] == "Use when the user asks for the thing, or mentions things."
    assert fm["allowed-tools"] == ["Read", "Bash"]
    assert body.lstrip().startswith("# Demo skill")
    assert "---" not in body

    rule_fm, _ = sa.parse_frontmatter(RULE_SCOPED)
    assert rule_fm["paths"] == ["src/**/*.py"]
    assert sa.parse_frontmatter("no frontmatter here\n") == ({}, "no frontmatter here\n")


def test_hashes_are_sha256_of_content_and_stable(plugin_tree):
    first = sa.scan(plugin_tree, owner_repo="", source="plugin")
    second = sa.scan(plugin_tree, owner_repo="", source="plugin")
    assert [r["content_hash"] for r in first] == [r["content_hash"] for r in second]
    skill = by_kind(first, "skill")[0]
    assert skill["content_hash"] == hashlib.sha256(SKILL.encode()).hexdigest()
    assert skill["content_text"] == SKILL
    # Changing the body changes the hash; the record identity (name/path) stays.
    (plugin_tree / "skills" / "demo" / "SKILL.md").write_text(SKILL + "\nMore.\n")
    changed = by_kind(sa.scan(plugin_tree, owner_repo="", source="plugin"), "skill")[0]
    assert changed["content_hash"] != skill["content_hash"]
    assert (changed["name"], changed["path"]) == (skill["name"], skill["path"])


def test_token_estimates_heuristic(monkeypatch, plugin_tree):
    monkeypatch.setattr(sa, "_COUNTER", None)  # force the chars/4 path
    assert sa.estimate_tokens("") == 0
    assert sa.estimate_tokens("abcd") == 1
    assert sa.estimate_tokens("abcde") == 2

    records = sa.scan(plugin_tree, owner_repo="", source="plugin")
    skill = by_kind(records, "skill")[0]
    listing = "demo-skill: Demo skill that does a thing.: " + skill["frontmatter"]["when_to_use"]
    assert skill["estimated_tokens_always_on"] == -(-len(listing) // 4)
    _, body = sa.parse_frontmatter(SKILL)
    assert skill["estimated_tokens_body"] == -(-len(body) // 4)
    assert skill["listing_truncated"] is False

    agent = by_kind(records, "agent")[0]
    assert agent["estimated_tokens_always_on"] == -(-len("Reviews diffs for correctness.") // 4)
    assert agent["estimated_tokens_body"] < sa.estimate_tokens(AGENT)

    instructions = by_kind(records, "instructions")[0]
    assert instructions["estimated_tokens_always_on"] == instructions["estimated_tokens_body"]

    rules = {r["path"]: r for r in by_kind(records, "rule")}
    assert rules[".claude/rules/scoped.md"]["scoped_by_globs"] is True
    assert rules[".claude/rules/global.md"]["scoped_by_globs"] is False

    hook = by_kind(records, "hook")[0]
    assert hook["estimated_tokens_always_on"] == sa.estimate_tokens(hook["content_text"])


def test_listing_cap_flags_truncation(monkeypatch, plugin_tree):
    monkeypatch.setattr(sa, "_COUNTER", None)
    long_desc = "x" * 2000
    (plugin_tree / "skills" / "demo" / "SKILL.md").write_text(
        f"---\nname: long\ndescription: {long_desc}\n---\nbody\n"
    )
    skill = by_kind(sa.scan(plugin_tree, owner_repo="", source="plugin"), "skill")[0]
    assert skill["listing_truncated"] is True
    assert skill["estimated_tokens_always_on"] == sa.LISTING_CAP_CHARS // 4


def test_hooks_are_one_artifact_per_entry(plugin_tree):
    hooks = {r["name"]: r for r in by_kind(sa.scan(plugin_tree, source="plugin", owner_repo=""), "hook")}
    assert set(hooks) == {"UserPromptSubmit#0", "PreToolUse#0", "PreToolUse#1"}
    assert hooks["PreToolUse#0"]["frontmatter"]["matcher"] == "Bash"
    assert hooks["PreToolUse#0"]["content_hash"] != hooks["PreToolUse#1"]["content_hash"]
    assert all(h["path"] == "hooks/hooks.json" for h in hooks.values())
    assert json.loads(hooks["PreToolUse#1"]["content_text"])["matcher"] == "Write"


def test_excluded_dirs_never_read_list_size_cap_and_symlinks(plugin_tree, tmp_path):
    for d in (".git", "node_modules", ".venv"):
        (plugin_tree / d / "skills" / "x").mkdir(parents=True)
        (plugin_tree / d / "skills" / "x" / "SKILL.md").write_text("---\nname: hidden\n---\n")
    for name in ("secret.pem", "id.key", ".env", ".env.local", ".dev-keys"):
        (plugin_tree / "agents" / name).write_text("---\nname: leak\n---\n")
    # Oversized skill is skipped.
    (plugin_tree / "skills" / "big").mkdir()
    (plugin_tree / "skills" / "big" / "SKILL.md").write_text("x" * (sa.MAX_FILE_BYTES + 1))
    # Symlink pointing outside the root is never followed.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\nname: outside\n---\n")
    (plugin_tree / "skills" / "linked").mkdir()
    os.symlink(outside / "SKILL.md", plugin_tree / "skills" / "linked" / "SKILL.md")
    os.symlink(outside, plugin_tree / "skills" / "linked-dir")
    # Symlink inside the root is fine.
    (plugin_tree / "skills" / "alias").mkdir()
    os.symlink(plugin_tree / "skills" / "demo" / "SKILL.md", plugin_tree / "skills" / "alias" / "SKILL.md")

    records = sa.scan(plugin_tree, owner_repo="", source="plugin")
    names = {r["name"] for r in records}
    assert "hidden" not in names and "leak" not in names and "outside" not in names
    assert "big" not in names
    paths = {r["path"] for r in records}
    assert "skills/alias/SKILL.md" in paths
    assert not any(p.startswith(("skills/linked", ".git", "node_modules", ".venv")) for p in paths)
    texts = " ".join(r["content_text"] for r in records)
    assert "leak" not in texts


def test_dry_run_uploads_nothing_and_prints_json(plugin_tree, capsys, monkeypatch):
    monkeypatch.setenv("OUROBOROS_API_URL", "http://127.0.0.1:9")  # would fail if used

    def boom(*a, **k):
        raise AssertionError("upload must not run in --dry-run")

    monkeypatch.setattr(sa, "upload", boom)
    assert sa.main([str(plugin_tree), "--dry-run", "--json", "--repo", "acme/demo"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["count"] == 10
    assert out["kinds"]["hook"] == 3 and out["owner_repo"] == "acme/demo"
    assert all("content_text" not in a and "content_length" in a for a in out["artifacts"])


def test_batches_split_at_100():
    records = [{"i": i} for i in range(250)]
    chunks = sa.batches(records)
    assert [len(c) for c in chunks] == [100, 100, 50]
    assert sa.batches([]) == []


def test_upload_batches_and_sums_counters(server, plugin_tree):
    records = [{"kind": "skill", "name": f"s{i}", "content_hash": "0" * 64} for i in range(150)]
    server.state.responder = lambda body: 202
    # The capture server replies {} so the counters stay 0; we check batching + auth.
    totals = sa.upload(records, server.url, lambda: "tok")
    assert totals["batches"] == 2
    bodies = [r["body"] for r in server.state.requests]
    assert [len(b["artifacts"]) for b in bodies] == [100, 50]
    assert bodies[0]["client"]["name"] == sa.CLIENT_NAME
    assert all(r["headers"]["Authorization"] == "Bearer tok" for r in server.state.requests)


def test_upload_fails_loudly(server, plugin_tree):
    with pytest.raises(RuntimeError):
        sa.upload([{"kind": "skill"}], server.url, lambda: None)
    server.state.status = 400
    with pytest.raises(RuntimeError, match="400"):
        sa.upload([{"kind": "skill"}], server.url, lambda: "tok")


def test_main_requires_api_url(plugin_tree, monkeypatch):
    monkeypatch.delenv("OUROBOROS_API_URL", raising=False)
    assert sa.main([str(plugin_tree)]) == 1
    assert sa.main([str(plugin_tree / "does-not-exist"), "--dry-run"]) == 2


def test_telemetry_off_uploads_nothing(plugin_tree, monkeypatch, capsys):
    monkeypatch.setenv("OUROBOROS_TELEMETRY", "off")
    monkeypatch.setenv("OUROBOROS_API_URL", "http://127.0.0.1:9")

    def boom(*a, **k):
        raise AssertionError("upload must not run when telemetry is off")

    monkeypatch.setattr(sa, "upload", boom)
    assert sa.main([str(plugin_tree)]) == 0
    assert "nothing uploaded" in capsys.readouterr().err
    assert sa.main([str(plugin_tree), "--dry-run"]) == 0  # local scan still works


def test_source_detection(plugin_tree, tmp_path):
    assert sa.detect_source(plugin_tree) == "plugin"
    assert sa.detect_source(tmp_path) == "project"
    assert by_kind(sa.scan(plugin_tree, owner_repo="", source="managed"), "skill")[0]["source"] == "managed"


def test_memory_files_are_user_instructions(tmp_path):
    root = tmp_path / "claude-home"
    mem = root / "projects" / "-home-me-repo" / "memory"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("- remembered thing\n")
    (root / "CLAUDE.md").write_text("global instructions\n")
    records = sa.scan(root, owner_repo="", source="user")
    paths = sorted(r["path"] for r in records)
    assert paths == ["CLAUDE.md", "projects/-home-me-repo/memory/MEMORY.md"]
    assert {r["kind"] for r in records} == {"instructions"}
