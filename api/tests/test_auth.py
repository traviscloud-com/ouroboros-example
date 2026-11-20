import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import create_app
from app.settings import Settings
from scripts import dev_token
from scripts.gen_jwks import generate

from .conftest import make_settings
from .helpers import count_rows, fetch_row, make_batch, make_event, post_events


def test_missing_token_rejected(client):
    assert client.post("/v1/telemetry/events", json=make_batch()).status_code == 401


def test_non_bearer_scheme_rejected(client):
    resp = client.post(
        "/v1/telemetry/events", json=make_batch(),
        headers={"Authorization": "Basic abc"},
    )
    assert resp.status_code == 401


def test_invalid_signature_rejected(client, key_dir, tmp_path):
    # Same kid, different keypair: signature check itself must fail
    kid = json.loads((key_dir / "jwks.json").read_text())["keys"][0]["kid"]
    imposter = tmp_path / "imposter"
    generate(imposter, kid=kid)
    forged = dev_token.mint(imposter)
    assert post_events(client, forged, make_batch()).status_code == 401


def test_unknown_kid_rejected(client, tmp_path):
    other = tmp_path / "other-keys"
    generate(other)
    assert post_events(client, dev_token.mint(other), make_batch()).status_code == 401


def test_expired_token_rejected(client, key_dir):
    expired = dev_token.mint(key_dir, expires_in=-60)
    assert post_events(client, expired, make_batch()).status_code == 401


def test_wrong_audience_rejected(client, key_dir, engine):
    tok = dev_token.mint(key_dir, audience="api://someone-else")
    event = make_event()
    assert post_events(client, tok, make_batch(event)).status_code == 403
    assert count_rows(engine, event["event_id"]) == 0


def test_wrong_issuer_rejected(client, key_dir):
    tok = dev_token.mint(key_dir, issuer="https://evil.example")
    assert post_events(client, tok, make_batch()).status_code == 403


def test_missing_scope_rejected(client, key_dir):
    assert post_events(
        client, dev_token.mint(key_dir, scope="Other.Scope"), make_batch()
    ).status_code == 403
    assert post_events(
        client, dev_token.mint(key_dir, scope=None), make_batch()
    ).status_code == 403


def test_body_cannot_spoof_identity(client, key_dir, engine):
    tok = dev_token.mint(key_dir, oid="real-oid", upn="real@ouroboros.local")
    event = make_event(oid="evil-oid", upn="evil@example.com")
    assert post_events(client, tok, make_batch(event)).status_code == 202
    row = fetch_row(engine, event["event_id"])
    assert row["auth_oid"] == "real-oid"
    assert row["auth_upn"] == "real@ouroboros.local"


def test_health_endpoints_unauthenticated(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_entra_mode_fails_closed(pg_url, key_dir, token):
    settings = make_settings(pg_url, key_dir, auth_mode="entra")
    with TestClient(create_app(settings)) as client:
        # Even a token the dev JWKS would accept must be rejected
        resp = post_events(client, token, make_batch())
        assert resp.status_code == 501


def test_unknown_auth_mode_fails_startup(pg_url, key_dir):
    with pytest.raises(ValidationError):
        make_settings(pg_url, key_dir, auth_mode="none")


def test_missing_auth_mode_fails_startup(pg_url, key_dir, monkeypatch):
    monkeypatch.delenv("AUTH_MODE", raising=False)
    with pytest.raises(ValidationError):
        Settings(
            database_url=pg_url,
            jwt_audience=dev_token.DEFAULT_AUDIENCE,
            jwt_issuer=dev_token.DEFAULT_ISSUER,
            jwks_path=str(key_dir / "jwks.json"),
        )
