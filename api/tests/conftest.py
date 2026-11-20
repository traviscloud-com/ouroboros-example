import os
import random
import shutil
import socket
import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.main import create_app
from app.migrations import apply_migrations
from app.settings import Settings
from scripts import dev_token
from scripts.gen_jwks import generate

POSTGRES_IMAGE = "postgres:16-alpine"


@pytest.fixture(scope="session")
def key_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("jwks")
    generate(d)
    return d


def _free_port() -> int:
    # Stay below ~49152: Windows/Hyper-V reserves dynamic port ranges up there,
    # and Rancher Desktop cannot publish container ports into them on WSL2.
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
        yield url
        return

    docker = shutil.which("docker")
    if not docker:
        pytest.skip("No TEST_DATABASE_URL and no docker available")

    port = _free_port()
    name = f"ouroboros-test-pg-{port}"
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
        yield url
    finally:
        subprocess.run([docker, "rm", "-f", name], capture_output=True)


def make_settings(pg_url, key_dir, **overrides) -> Settings:
    kwargs = dict(
        database_url=pg_url,
        auth_mode="dev",
        jwt_audience=dev_token.DEFAULT_AUDIENCE,
        jwt_issuer=dev_token.DEFAULT_ISSUER,
        jwks_path=str(key_dir / "jwks.json"),
        freshness_window_seconds=300,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


@pytest.fixture(scope="session")
def migrated_db(pg_url) -> str:
    """The shared test database at the latest schema version (design D11)."""
    eng = create_engine(pg_url)
    try:
        apply_migrations(eng)
    finally:
        eng.dispose()
    return pg_url


@pytest.fixture()
def client(migrated_db, key_dir):
    with TestClient(create_app(make_settings(migrated_db, key_dir))) as c:
        yield c


@pytest.fixture(scope="session")
def token(key_dir) -> str:
    return dev_token.mint(key_dir)


@pytest.fixture()
def engine(migrated_db):
    eng = create_engine(migrated_db)
    yield eng
    eng.dispose()
