from enum import Enum
from functools import lru_cache

from pydantic_settings import BaseSettings


class AuthMode(str, Enum):
    dev = "dev"
    entra = "entra"


class Settings(BaseSettings):
    """Runtime configuration, environment variables only.

    Instantiation fails (and the service refuses to start) when AUTH_MODE is
    missing or not a recognized mode — auth is never silently skipped.
    """

    database_url: str
    auth_mode: AuthMode
    jwt_audience: str
    jwt_issuer: str
    jwks_path: str | None = None
    freshness_window_seconds: int = 300
    # Hook events are spooled offline and replayed later (design D17), so their
    # client_ts may be days old; stale records are rejected per event.
    hook_events_max_age_seconds: int = 7 * 24 * 3600
    # Directory of NNNN_*.sql migrations; defaults to the copy shipped with the
    # package (api/migrations in the source tree).
    migrations_dir: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
