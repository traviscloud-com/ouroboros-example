from sqlalchemy import text
from sqlalchemy.engine import Engine, create_engine

# Highest api/migrations version this build of the reporting service was
# written against (design D11). The service never migrates; /readyz stays 503
# until the API has applied at least this version.
REQUIRED_SCHEMA_VERSION = 11


def normalize_database_url(database_url: str) -> str:
    """Accept a plain ``postgresql://`` URL (what CloudNativePG writes into its app
    secret) and pin it to the psycopg 3 driver this service ships with; SQLAlchemy
    would otherwise look for psycopg2."""
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://"):]
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url[len("postgres://"):]
    return database_url


def make_engine(database_url: str) -> Engine:
    return create_engine(normalize_database_url(database_url), pool_pre_ping=True)


class SchemaNotReady(RuntimeError):
    """The database answers but has not reached REQUIRED_SCHEMA_VERSION."""


def schema_version(engine: Engine) -> int | None:
    """Highest applied migration, or ``None`` before the API has ever migrated."""
    with engine.connect() as conn:
        if conn.execute(text("SELECT to_regclass('schema_migrations')")).scalar_one() is None:
            return None
        return conn.execute(text("SELECT max(version) FROM schema_migrations")).scalar_one()


def check_ready(engine: Engine, required_version: int = REQUIRED_SCHEMA_VERSION) -> int:
    """Raise unless the database is reachable and migrated to ``required_version``."""
    version = schema_version(engine)
    if version is None or version < required_version:
        raise SchemaNotReady(
            f"schema version {version if version is not None else 'none'} "
            f"< required {required_version}; waiting for the API to migrate"
        )
    return version
