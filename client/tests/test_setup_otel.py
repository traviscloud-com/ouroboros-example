"""python3 -m ouroboros_client.setup_otel against a temp settings file —
never the real ~/.claude/settings.json."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ouroboros_client import __version__, setup_otel

API = "https://ouroboros.example.test"
TOKEN = "fake-test-token-not-a-jwt"  # placeholder, never minted

EXPECTED_ENV = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_ENDPOINT": f"{API}/v1/otel",
    "OTEL_EXPORTER_OTLP_HEADERS": f"Authorization=Bearer {TOKEN}",
    "OTEL_LOG_TOOL_DETAILS": "1",
    "OTEL_LOG_USER_PROMPTS": "0",
    "OTEL_LOG_ASSISTANT_RESPONSES": "0",
    "OTEL_LOG_TOOL_CONTENT": "0",
    "OTEL_LOG_RAW_API_BODIES": "0",
    "OTEL_RESOURCE_ATTRIBUTES": f"ouroboros.client.version={__version__}",
}


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    monkeypatch.delenv("OUROBOROS_API_URL", raising=False)
    monkeypatch.delenv("OUROBOROS_DEV_KEY_DIR", raising=False)
    return tmp_path / "claude" / "settings.json"


def run(settings: Path, *extra: str) -> int:
    return setup_otel.main(["--settings", str(settings), "--api-url", API, "--token", TOKEN, *extra])


def read(settings: Path) -> dict:
    return json.loads(settings.read_text())


def test_fresh_write(settings, capsys):
    assert run(settings) == 0
    data = read(settings)
    assert data == {"env": EXPECTED_ENV}
    out = capsys.readouterr().out
    assert "Wrote OTel export settings" in out
    assert TOKEN not in out  # redacted in the summary
    assert "<redacted>" in out
    for key in setup_otel.OTEL_KEYS:
        assert key in out  # every variable set is printed
    assert "OTEL_LOG_USER_PROMPTS=0" in out and "OTEL_LOG_TOOL_DETAILS=1" in out
    assert "Bearer token expires" in out
    assert not list(settings.parent.glob("*.bak-*"))  # nothing to back up the first time


def test_merge_preserves_everything_else(settings):
    settings.parent.mkdir(parents=True)
    original = {
        "model": "opus",
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo"}]}]},
        "env": {"MY_VAR": "keep me", "OTEL_RESOURCE_ATTRIBUTES": "department=eng", "OTEL_LOG_USER_PROMPTS": "1"},
    }
    settings.write_text(json.dumps(original))
    assert run(settings) == 0
    data = read(settings)
    assert data["model"] == "opus" and data["permissions"] == original["permissions"] and data["hooks"] == original["hooks"]
    assert data["env"]["MY_VAR"] == "keep me"
    assert data["env"]["OTEL_LOG_USER_PROMPTS"] == "0"  # ours wins: content stays off
    assert data["env"]["OTEL_RESOURCE_ATTRIBUTES"] == f"department=eng,ouroboros.client.version={__version__}"
    assert set(data) == set(original)  # only `env` touched, no new top-level keys
    backups = list(settings.parent.glob("settings.json.bak-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == original


def test_idempotent_second_run(settings, capsys):
    assert run(settings) == 0
    first = settings.read_text()
    capsys.readouterr()
    time.sleep(1.1)  # a backup name would differ
    assert run(settings) == 0
    assert settings.read_text() == first
    assert "Already configured" in capsys.readouterr().out
    assert not list(settings.parent.glob("*.bak-*"))  # no write, no backup


def test_dry_run_writes_nothing(settings, capsys):
    assert run(settings, "--dry-run") == 0
    assert not settings.exists()
    out = capsys.readouterr().out
    assert "Would write" in out and "OTEL_EXPORTER_OTLP_ENDPOINT" in out and TOKEN not in out


def test_print_mode_emits_block_with_token_and_writes_nothing(settings, capsys):
    assert run(settings, "--print") == 0
    assert not settings.exists()
    block = json.loads(capsys.readouterr().out)
    assert block == {"env": EXPECTED_ENV}


def test_opt_out_does_nothing(settings, monkeypatch, capsys):
    monkeypatch.setenv("OUROBOROS_TELEMETRY", "off")
    assert run(settings) == 0
    assert not settings.exists()
    assert "OUROBOROS_TELEMETRY=off" in capsys.readouterr().out


def test_invalid_json_is_never_clobbered(settings, capsys):
    settings.parent.mkdir(parents=True)
    settings.write_text("{ not json")
    assert run(settings) == 1
    assert settings.read_text() == "{ not json"
    assert "not valid JSON" in capsys.readouterr().err


def test_env_not_an_object_aborts(settings, capsys):
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"env": ["weird"]}))
    assert run(settings) == 1
    assert read(settings) == {"env": ["weird"]}


def test_missing_api_url_and_token_source_fail_clearly(settings, capsys):
    assert setup_otel.main(["--settings", str(settings), "--token", TOKEN]) == 1
    assert "no API URL" in capsys.readouterr().err
    assert setup_otel.main(["--settings", str(settings), "--api-url", API]) == 1
    assert "OUROBOROS_DEV_KEY_DIR" in capsys.readouterr().err
    assert not settings.exists()


def test_remove_undoes_only_our_keys(settings):
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"model": "opus", "env": {"MY_VAR": "x", "OTEL_RESOURCE_ATTRIBUTES": "department=eng"}}))
    assert run(settings) == 0
    assert setup_otel.main(["--settings", str(settings), "--remove"]) == 0
    assert read(settings) == {"model": "opus", "env": {"MY_VAR": "x", "OTEL_RESOURCE_ATTRIBUTES": "department=eng"}}
    # a second remove is a no-op
    before = settings.read_text()
    assert setup_otel.main(["--settings", str(settings), "--remove"]) == 0
    assert settings.read_text() == before


def test_mints_90_day_dev_token(settings, tmp_path, monkeypatch, capsys):
    jwt = pytest.importorskip("jwt")
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))
    try:
        from scripts.gen_jwks import generate
    finally:
        sys.path.pop(0)
    generate(tmp_path / "keys")
    monkeypatch.setenv("OUROBOROS_DEV_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("OUROBOROS_API_URL", API)
    monkeypatch.setenv("OUROBOROS_UPN", "me@example.test")
    assert setup_otel.main(["--settings", str(settings)]) == 0
    header = read(settings)["env"]["OTEL_EXPORTER_OTLP_HEADERS"]
    assert header.startswith("Authorization=Bearer ")
    token = header.split(" ", 1)[1]
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["scp"] == "Telemetry.Write" and claims["upn"] == "me@example.test"
    assert abs((claims["exp"] - claims["iat"]) - 90 * 86400) < 5
    expiry = datetime.fromtimestamp(claims["exp"], tz=timezone.utc).strftime("%Y-%m-%d")
    out = capsys.readouterr().out
    assert expiry in out
    assert token not in out


def test_custom_ttl(settings, tmp_path, monkeypatch):
    jwt = pytest.importorskip("jwt")
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))
    try:
        from scripts.gen_jwks import generate
    finally:
        sys.path.pop(0)
    generate(tmp_path / "keys")
    monkeypatch.setenv("OUROBOROS_DEV_KEY_DIR", str(tmp_path / "keys"))
    assert setup_otel.main(["--settings", str(settings), "--api-url", API, "--ttl-days", "7"]) == 0
    token = read(settings)["env"]["OTEL_EXPORTER_OTLP_HEADERS"].split(" ", 1)[1]
    claims = jwt.decode(token, options={"verify_signature": False})
    assert abs((claims["exp"] - claims["iat"]) - 7 * 86400) < 5


def test_module_is_stdlib_plus_optional_jwt_only():
    import subprocess
    import sys

    code = ("import sys; import ouroboros_client.setup_otel; "
            "sys.exit(1 if 'httpx' in sys.modules or 'jwt' in sys.modules else 0)")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
