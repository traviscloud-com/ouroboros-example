"""Tests for plugin/scripts/ouroboros_hook_launcher.py (change: add-claude-plugin).

The launcher must behave identically to ouroboros-hook when the SDK is
importable, and be completely inert (exit 0, no output, no files) when it
is not."""

import json
import subprocess
import sys
import uuid
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[2] / "plugin" / "scripts" / "ouroboros_hook_launcher.py"


def run_launcher(payload, env_home, with_sdk=True):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    env = {"OUROBOROS_HOME": str(env_home), "PATH": "/usr/bin:/bin"}
    # with_sdk: the venv interpreter (SDK installed, as after pip install).
    # without: the bare system interpreter — the SDK must not be importable.
    # -S skips site-packages, so the test holds on hosts where the SDK happens
    # to be installed for the system interpreter too.
    command = [sys.executable] if with_sdk else ["/usr/bin/python3", "-S"]
    return subprocess.run(
        [*command, str(LAUNCHER)],
        input=raw.encode(),
        env=env,
        capture_output=True,
    )


def prompt_event(session_id):
    return {
        "hook_event_name": "UserPromptSubmit",
        "session_id": session_id,
        "prompt_id": "p1",
        "prompt_text": "captured via the plugin launcher",
    }


def test_launcher_parity_with_sdk(tmp_path):
    sid = str(uuid.uuid4())
    result = run_launcher(prompt_event(sid), tmp_path, with_sdk=True)
    assert result.returncode == 0
    assert result.stdout == b"" and result.stderr == b""
    cache = tmp_path / "interactions" / f"{sid}.json"
    assert cache.exists()
    entries = json.loads(cache.read_text())
    assert entries[0]["user_prompt"] == "captured via the plugin launcher"


def test_launcher_inert_without_sdk(tmp_path):
    sid = str(uuid.uuid4())
    result = run_launcher(prompt_event(sid), tmp_path, with_sdk=False)
    assert result.returncode == 0
    assert result.stdout == b"" and result.stderr == b""
    assert not (tmp_path / "interactions").exists()


def test_launcher_malformed_input_exits_zero(tmp_path):
    for with_sdk in (True, False):
        result = run_launcher("not json {", tmp_path, with_sdk=with_sdk)
        assert result.returncode == 0
        assert result.stdout == b"" and result.stderr == b""


def test_launcher_is_stdlib_only_without_sdk(tmp_path):
    # Sanity: launcher runs under a bare interpreter (no PYTHONPATH, no venv)
    result = run_launcher({"hook_event_name": "Stop"}, tmp_path, with_sdk=False)
    assert result.returncode == 0
