import importlib.util
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.main import create_app
from app.settings import Settings

POSTGRES_IMAGE = "postgres:16-alpine"
API_DIR = Path(__file__).resolve().parents[2] / "api"


def _load_migration_runner():
    """The API owns the schema (design D11); load its runner by path so this
    suite prepares the database exactly the way the API does on start."""
    spec = importlib.util.spec_from_file_location(
        "api_migrations", API_DIR / "app" / "migrations.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def migrate(url: str) -> None:
    runner = _load_migration_runner()
    eng = create_engine(url)
    try:
        runner.apply_migrations(eng, API_DIR / "migrations")
    finally:
        eng.dispose()


def _free_port() -> int:
    # Below ~49152: Windows/Hyper-V reserved ranges break port publishing on WSL2
    for port in random.sample(range(20000, 45000), 50):
        try:
            with socket.socket() as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError("No free port found in 20000-45000")


@pytest.fixture(scope="session")
def pg_url():
    if url := os.environ.get("TEST_DATABASE_URL"):
        migrate(url)
        yield url
        return

    docker = shutil.which("docker")
    if not docker:
        pytest.skip("No TEST_DATABASE_URL and no docker available")

    port = _free_port()
    name = f"ouroboros-reporting-test-pg-{port}"
    subprocess.run(
        [
            docker, "run", "-d", "--rm", "--name", name,
            "-e", "POSTGRES_USER=test",
            "-e", "POSTGRES_PASSWORD=test",
            "-e", "POSTGRES_DB=ouroboros_test",
            "-p", f"127.0.0.1:{port}:5432",
            POSTGRES_IMAGE,
        ],
        check=True,
        capture_output=True,
    )
    url = f"postgresql+psycopg://test:test@127.0.0.1:{port}/ouroboros_test"
    try:
        probe = create_engine(url)
        deadline = time.time() + 90
        while True:
            try:
                with probe.connect() as conn:
                    conn.execute(text("SELECT 1"))
                break
            except Exception:
                if time.time() > deadline:
                    raise
                time.sleep(1)
        probe.dispose()
        migrate(url)
        yield url
    finally:
        subprocess.run([docker, "rm", "-f", name], capture_output=True)


TRUNCATE_ALL = text(
    "TRUNCATE events, hook_events_raw, sessions, artifacts, principals, audit_log, analyst_runs, "
    "analytics.sentiment_daily, analytics.correction_themes CASCADE"
)


@pytest.fixture()
def engine(pg_url):
    eng = create_engine(pg_url)
    with eng.begin() as conn:
        conn.execute(TRUNCATE_ALL)
    yield eng
    eng.dispose()


# Auth defaults for tests (design D12): the TestClient's socket peer is set
# through the ASGI scope `client` tuple; the trusted proxy CIDR covers it and
# ADMIN_EMAILS bootstraps the default identity as admin (verbatim text).
TRUSTED_PEER = ("127.0.0.1", 40000)
UNTRUSTED_PEER = ("10.42.0.7", 40000)
ADMIN_EMAIL = "admin@example.com"
EMAIL_HEADER = "X-Auth-Request-Email"


def make_test_settings(pg_url, **overrides) -> Settings:
    kwargs = dict(
        database_url=pg_url,
        sentiment_cache_ttl_seconds=0,
        trusted_proxy_cidrs=["127.0.0.1/32"],
        admin_emails=[ADMIN_EMAIL],
        aggregate_refresh_seconds=0,  # tests drive the scheduler explicitly
        # Never pick up a developer's real credentials from the environment:
        # the improvement loop is off unless a test turns it on with fakes.
        anthropic_api_key=None,
        github_token=None,
        github_app_id=None,
        github_app_private_key=None,
        github_app_installation_id=None,
        github_webhook_secret=None,
        analyst_schedule="",
        public_base_url=None,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


def make_client(pg_url, *, email=ADMIN_EMAIL, peer=TRUSTED_PEER, settings=None, **overrides) -> TestClient:
    """A TestClient whose requests arrive from ``peer`` carrying ``email`` (None = no header)."""
    settings = settings or make_test_settings(pg_url, **overrides)
    headers = {EMAIL_HEADER: email} if email else {}
    return TestClient(create_app(settings), client=peer, headers=headers)


def grant(engine, email: str, role: str, created_by: str = "test") -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO principals (email, role, created_by) VALUES (:e, :r, :b) "
                "ON CONFLICT (email) DO UPDATE SET role = EXCLUDED.role"
            ),
            {"e": email.lower(), "r": role, "b": created_by},
        )


@pytest.fixture()
def client(pg_url, engine):
    with make_client(pg_url) as c:
        yield c


@pytest.fixture()
def engineer_client(pg_url, engine):
    grant(engine, "eng@example.com", "engineer")
    with make_client(pg_url, email="eng@example.com") as c:
        yield c


@pytest.fixture()
def leadership_client(pg_url, engine):
    grant(engine, "lead@example.com", "leadership")
    with make_client(pg_url, email="lead@example.com") as c:
        yield c


INSERT = text(
    """
    INSERT INTO events (event_id, session_id, client_ts, received_at, skill_name,
        skill_version, status, latency_ms, error_code, prompt_id, auth_oid, auth_upn,
        feedback, interaction)
    VALUES (:event_id, :session_id, :client_ts, :received_at, :skill_name,
        :skill_version, :status, :latency_ms, :error_code, :prompt_id, :auth_oid, :auth_upn,
        CAST(:feedback AS JSONB), CAST(:interaction AS JSONB))
    """
)


def seed_event(
    engine,
    *,
    skill_name="SQL_Generator",
    status="success",
    session_id=None,
    upn="dev@ouroboros.local",
    notes="ok",
    user_corrections=None,
    user_prompt=None,
    assistant_response=None,
    age_seconds=0,
    at: datetime | None = None,
    received_at: datetime | None = None,
    latency_ms=100,
    error_code=None,
    prompt_id=None,
    oid=None,
) -> str:
    event_id = str(uuid.uuid4())
    ts = at or (datetime.now(timezone.utc) - timedelta(seconds=age_seconds))
    feedback = {"notes": notes}
    if user_corrections is not None:
        feedback["user_corrections"] = user_corrections
    interaction = None
    if user_prompt is not None or assistant_response is not None:
        interaction = {}
        if user_prompt is not None:
            interaction["user_prompt"] = user_prompt
        if assistant_response is not None:
            interaction["assistant_response"] = assistant_response
    with engine.begin() as conn:
        conn.execute(
            INSERT,
            {
                "event_id": event_id,
                "session_id": session_id or str(uuid.uuid4()),
                "client_ts": ts,
                "received_at": received_at or ts,
                "skill_name": skill_name,
                "skill_version": "1.0.0",
                "status": status,
                "latency_ms": latency_ms,
                "error_code": error_code,
                "prompt_id": prompt_id,
                "auth_oid": oid or ("oid-" + upn),
                "auth_upn": upn,
                "feedback": json.dumps(feedback),
                "interaction": json.dumps(interaction) if interaction else None,
            },
        )
    return event_id
