"""Versioned SQL migrations applied by the API on start (design D11).

Files ``migrations/NNNN_<name>.sql`` are applied in version order, each inside
its own transaction, and recorded in ``schema_migrations`` with the sha256 of
the file. A recorded version whose checksum no longer matches the file is a
hard error: a migration that has shipped is immutable; write a new one.
A session-level advisory lock serialises replicas that start together, so the
second one simply observes the rows the first wrote.

This module imports nothing from the ``app`` package on purpose: the reporting
test suite loads it by path to prepare its database.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

log = logging.getLogger("ouroboros.migrations")

FILENAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")

# Arbitrary, fixed key for pg_advisory_lock; shared by every API replica.
ADVISORY_LOCK_KEY = 0x6F75726F  # "ouro"

_PACKAGE_DIR = Path(__file__).resolve().parent
# Installed wheel (hatch force-includes migrations under app/) first, then the
# source tree layout api/migrations next to api/app/.
DEFAULT_MIGRATIONS_DIRS = (_PACKAGE_DIR / "migrations", _PACKAGE_DIR.parent / "migrations")

CREATE_SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER     PRIMARY KEY,
    name       TEXT        NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum   TEXT        NOT NULL
)
"""


class MigrationError(RuntimeError):
    """A migration file set or recorded history is inconsistent."""


class MigrationChecksumError(MigrationError):
    """A recorded migration's file content changed after it was applied."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str
    checksum: str


def default_migrations_dir() -> Path:
    for candidate in DEFAULT_MIGRATIONS_DIRS:
        if candidate.is_dir():
            return candidate
    raise MigrationError(
        "No migrations directory found; looked in "
        + ", ".join(str(c) for c in DEFAULT_MIGRATIONS_DIRS)
    )


def discover(migrations_dir: str | Path | None = None) -> list[Migration]:
    """Return the migrations in ``migrations_dir`` ordered by version.

    Versions must be unique and contiguous from 1: a gap or a duplicate is a
    sign of a bad merge and is refused before anything touches the database.
    """
    directory = Path(migrations_dir) if migrations_dir else default_migrations_dir()
    if not directory.is_dir():
        raise MigrationError(f"Migrations directory does not exist: {directory}")

    found: list[Migration] = []
    for path in sorted(directory.iterdir()):
        if path.suffix != ".sql":
            continue
        match = FILENAME.match(path.name)
        if not match:
            raise MigrationError(
                f"Migration filename {path.name!r} must look like NNNN_snake_name.sql"
            )
        raw = path.read_bytes()
        found.append(
            Migration(
                version=int(match["version"]),
                name=match["name"],
                path=path,
                sql=raw.decode("utf-8"),
                checksum=hashlib.sha256(raw).hexdigest(),
            )
        )

    found.sort(key=lambda m: m.version)
    for expected, migration in enumerate(found, start=1):
        if migration.version != expected:
            raise MigrationError(
                f"Migration versions must be contiguous from 0001; "
                f"found {migration.path.name} where version {expected:04d} was expected"
            )
    return found


def applied_versions(conn: Connection) -> dict[int, str]:
    """``{version: checksum}`` already recorded; empty when the table is missing."""
    exists = conn.execute(text("SELECT to_regclass('schema_migrations')")).scalar_one()
    if exists is None:
        return {}
    rows = conn.execute(text("SELECT version, checksum FROM schema_migrations"))
    return {row.version: row.checksum for row in rows}


def current_version(engine: Engine) -> int | None:
    """Highest applied version, or ``None`` when nothing has been applied."""
    with engine.connect() as conn:
        versions = applied_versions(conn)
    return max(versions) if versions else None


def apply_migrations(engine: Engine, migrations_dir: str | Path | None = None) -> list[int]:
    """Apply every pending migration; return the versions applied in this call.

    Each file runs in its own transaction together with its ``schema_migrations``
    row, so a crash leaves the database at a recorded version boundary and the
    next start resumes from there.
    """
    migrations = discover(migrations_dir)
    applied: list[int] = []

    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY})
        conn.commit()
        try:
            with conn.begin():
                conn.exec_driver_sql(CREATE_SCHEMA_MIGRATIONS)

            recorded = applied_versions(conn)
            conn.commit()

            for migration in migrations:
                recorded_checksum = recorded.get(migration.version)
                if recorded_checksum is not None:
                    if recorded_checksum != migration.checksum:
                        raise MigrationChecksumError(
                            f"Migration {migration.path.name} was applied with checksum "
                            f"{recorded_checksum} but the file now hashes to "
                            f"{migration.checksum}; applied migrations are immutable"
                        )
                    continue

                log.info("applying migration %04d_%s", migration.version, migration.name)
                with conn.begin():
                    conn.exec_driver_sql(migration.sql)
                    conn.execute(
                        text(
                            "INSERT INTO schema_migrations (version, name, checksum) "
                            "VALUES (:version, :name, :checksum)"
                        ),
                        {
                            "version": migration.version,
                            "name": migration.name,
                            "checksum": migration.checksum,
                        },
                    )
                applied.append(migration.version)
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})
            conn.commit()

    return applied
