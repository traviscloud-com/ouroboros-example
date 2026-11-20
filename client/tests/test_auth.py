import sys
from pathlib import Path
import uuid

from ouroboros_client import spool
from ouroboros_client.auth import azure_token_provider, dev_token_provider
from ouroboros_client.client import TelemetryClient

from .test_client import record_ok


def test_none_provider_means_silent_spool(server, ouro_home):
    client = TelemetryClient(server.url, scope="test", token_provider=lambda: None)
    record_ok(client)
    client.flush()
    assert len(spool.list_files()) == 1  # kept for later
    assert server.state.requests == []  # no request attempted


def test_provider_exception_swallowed(server, ouro_home):
    def exploding():
        raise RuntimeError("boom")

    client = TelemetryClient(server.url, scope="test", token_provider=exploding)
    record_ok(client)
    client.flush()
    assert len(spool.list_files()) == 1
    assert server.state.requests == []


def test_bearer_header_sent(server, ouro_home):
    client = TelemetryClient(server.url, scope="test", token_provider=lambda: "sekret")
    record_ok(client)
    client.flush()
    assert server.state.requests[0]["headers"]["Authorization"] == "Bearer sekret"


def test_azure_provider_without_azure_identity_returns_none(monkeypatch):
    # azure-identity is not installed in the test env; even if it were, block it
    monkeypatch.setitem(sys.modules, "azure.identity", None)
    assert azure_token_provider("api://x/.default")() is None


def test_dev_provider_mints_valid_token(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))
    try:
        from scripts.gen_jwks import generate
    finally:
        sys.path.pop(0)
    generate(tmp_path)
    token = dev_token_provider(tmp_path, upn="sdk@test")()
    assert token and token.count(".") == 2

    import jwt

    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["upn"] == "sdk@test"
    assert claims["scp"] == "Telemetry.Write"


def test_dev_provider_missing_keys_returns_none(tmp_path):
    assert dev_token_provider(tmp_path / "nope")() is None
