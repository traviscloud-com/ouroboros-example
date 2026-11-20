from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from scripts import dev_token

from .conftest import make_settings
from .helpers import count_rows, make_batch, make_event, post_events


def test_stale_event_rejects_batch_listing_ids(client, token, engine):
    fresh = make_event()
    stale = make_event(age_seconds=400)
    resp = post_events(client, token, make_batch(fresh, stale))
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["stale_event_ids"] == [stale["event_id"]]
    assert count_rows(engine, fresh["event_id"]) == 0


def test_event_within_window_accepted(client, token):
    resp = post_events(client, token, make_batch(make_event(age_seconds=250)))
    assert resp.status_code == 202


def test_window_configurable_via_env(pg_url, key_dir, token, monkeypatch):
    monkeypatch.setenv("FRESHNESS_WINDOW_SECONDS", "5")
    settings = Settings(
        database_url=pg_url,
        auth_mode="dev",
        jwt_audience=dev_token.DEFAULT_AUDIENCE,
        jwt_issuer=dev_token.DEFAULT_ISSUER,
        jwks_path=str(key_dir / "jwks.json"),
    )
    assert settings.freshness_window_seconds == 5
    with TestClient(create_app(settings)) as client:
        # 60s-old event: fine under the 300s default, stale under the 5s window
        resp = post_events(client, token, make_batch(make_event(age_seconds=60)))
        assert resp.status_code == 400


def test_default_window_is_300(pg_url, key_dir):
    settings = make_settings(pg_url, key_dir)
    assert Settings(
        database_url=pg_url,
        auth_mode="dev",
        jwt_audience=settings.jwt_audience,
        jwt_issuer=settings.jwt_issuer,
        jwks_path=settings.jwks_path,
    ).freshness_window_seconds == 300
