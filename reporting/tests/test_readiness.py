"""Reporting readiness gate (design D11): /readyz is 503 until the API has
migrated the database to REQUIRED_SCHEMA_VERSION."""

from fastapi.testclient import TestClient
from sqlalchemy import text

from app import db
from app.main import create_app

from .conftest import make_test_settings


def test_readyz_reports_schema_version(client):
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "schema_version": db.REQUIRED_SCHEMA_VERSION}


def test_check_ready_rejects_older_schema(engine):
    try:
        db.check_ready(engine, required_version=db.REQUIRED_SCHEMA_VERSION + 1)
    except db.SchemaNotReady as exc:
        assert f"required {db.REQUIRED_SCHEMA_VERSION + 1}" in str(exc)
    else:
        raise AssertionError("expected SchemaNotReady")


def test_readyz_503_until_required_version(pg_url, engine, monkeypatch):
    # Pretend the API has only reached the previous version: hide the last history row.
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE schema_migrations RENAME TO schema_migrations_live"))
        conn.execute(
            text(
                "CREATE TABLE schema_migrations AS "
                "SELECT * FROM schema_migrations_live WHERE version < :v"
            ),
            {"v": db.REQUIRED_SCHEMA_VERSION},
        )
    try:
        with TestClient(create_app(make_test_settings(pg_url))) as client:
            resp = client.get("/readyz")
            assert resp.status_code == 503
            assert (
                f"schema version {db.REQUIRED_SCHEMA_VERSION - 1} < required {db.REQUIRED_SCHEMA_VERSION}"
                in resp.json()["detail"]
            )
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE schema_migrations"))
            conn.execute(text("ALTER TABLE schema_migrations_live RENAME TO schema_migrations"))


def test_readyz_503_before_any_migration(pg_url, engine):
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE schema_migrations RENAME TO schema_migrations_live"))
    try:
        with TestClient(create_app(make_test_settings(pg_url))) as client:
            resp = client.get("/readyz")
            assert resp.status_code == 503
            assert "schema version none" in resp.json()["detail"]
    finally:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE schema_migrations_live RENAME TO schema_migrations"))


def test_readyz_503_when_database_unreachable():
    settings = make_test_settings("postgresql+psycopg://nobody:nothing@127.0.0.1:1/none")
    with TestClient(create_app(settings)) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Database unavailable"
