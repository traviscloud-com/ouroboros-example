import json
import os
import uuid

import pytest

from ouroboros_client import config
from ouroboros_client.client import TelemetryClient, default_token_provider
from ouroboros_client.report import main as report_main


@pytest.fixture()
def clean_env(monkeypatch):
    for name in (
        "OUROBOROS_API_URL", "OUROBOROS_DEV_KEY_DIR", "OUROBOROS_UPN", "OUROBOROS_OID",
        "OUROBOROS_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_unconfigured_client_is_inert(clean_env, ouro_home):
    client = TelemetryClient(token_provider=lambda: "tok")
    assert not client.configured
    client.record(skill_name="S", skill_version="1", status="success",
                  session_id=str(uuid.uuid4()), feedback={"notes": "x"})
    assert not list(config.spool_dir().glob("*.json"))


def test_session_id_from_claude_code_env(clean_env, ouro_home, server):
    sid = str(uuid.uuid4())
    clean_env.setenv("OUROBOROS_API_URL", server.url)
    clean_env.setenv("CLAUDE_CODE_SESSION_ID", sid)
    client = TelemetryClient(token_provider=lambda: None)  # no token: stays spooled
    client.record(skill_name="S", skill_version="1", status="success", feedback={"notes": "x"})
    files = list(config.spool_dir().glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text())["session_id"] == sid


def test_dev_key_dir_selects_dev_tokens(clean_env, tmp_path):
    jwt = pytest.importorskip("jwt")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (tmp_path / "private_key.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (tmp_path / "jwks.json").write_text(json.dumps({"keys": [{"kid": "k1"}]}))
    clean_env.setenv("OUROBOROS_DEV_KEY_DIR", str(tmp_path))
    clean_env.setenv("OUROBOROS_UPN", "me@example.com")
    clean_env.setenv("OUROBOROS_OID", "oid-123")
    token = default_token_provider()()
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["upn"] == "me@example.com" and claims["oid"] == "oid-123"
    assert jwt.get_unverified_header(token)["kid"] == "k1"


def test_report_cli_spools_and_exits_zero(clean_env, ouro_home, server, capsys):
    clean_env.setenv("OUROBOROS_API_URL", server.url)
    clean_env.setenv("CLAUDE_CODE_SESSION_ID", str(uuid.uuid4()))
    rc = report_main(["--skill", "S", "--version", "1", "--status", "failure",
                      "--notes", "boom", "--error-code", "E1", "--no-flush", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["configured"] is True and out["spooled_after"] == 1
    event = json.loads(next(config.spool_dir().glob("*.json")).read_text())
    assert event["status"] == "failure" and event["error_code"] == "E1"


def test_report_cli_never_fails_without_config(clean_env, ouro_home):
    assert report_main(["--skill", "S", "--version", "1", "--status", "success", "--notes", "n"]) == 0
