import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine, create_engine

from .migrations import apply_migrations
from .models import TelemetryEvent

INSERT_EVENT = text(
    """
    INSERT INTO events (
        event_id, session_id, client_ts, skill_name, skill_version,
        status, model, latency_ms, error_code, auth_oid, auth_upn, feedback,
        interaction, prompt_id, turn_id, artifact_id
    ) VALUES (
        :event_id, :session_id, :client_ts, :skill_name, :skill_version,
        :status, :model, :latency_ms, :error_code, :auth_oid, :auth_upn,
        CAST(:feedback AS JSONB), CAST(:interaction AS JSONB),
        CAST(:prompt_id AS TEXT),
        -- links: the turn if it is already ingested (projection.link_outcomes_to_turn
        -- fills it later otherwise), and the registered skill artifact of that name
        (SELECT turn_id FROM turns WHERE prompt_id = CAST(:prompt_id AS TEXT)),
        (SELECT artifact_id FROM artifacts
          WHERE kind = 'skill' AND name = :skill_name
          ORDER BY last_seen DESC, artifact_id LIMIT 1)
    )
    ON CONFLICT (event_id) DO NOTHING
    """
)

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


def apply_schema(engine: Engine, migrations_dir: str | Path | None = None) -> list[int]:
    """Bring the database to the latest schema version (design D11).

    Returns the migration versions applied by this call; an up-to-date database
    yields an empty list.
    """
    return apply_migrations(engine, migrations_dir)


def ensure_schema(state) -> None:
    """Apply pending migrations once per process; shared by every endpoint.

    Deferred and retried (via /readyz) so the pod reports NotReady rather than
    crash-looping while Postgres is still coming up.
    """
    if not getattr(state, "schema_applied", False):
        apply_schema(state.engine, state.settings.migrations_dir)
        state.schema_applied = True


def check_ready(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def insert_events(
    engine: Engine, events: list[TelemetryEvent], auth_oid: str, auth_upn: str
) -> None:
    rows = [
        {
            "event_id": str(e.event_id),
            "session_id": str(e.session_id),
            "client_ts": e.client_ts,
            "skill_name": e.skill_name,
            "skill_version": e.skill_version,
            "status": e.status.value,
            "model": e.model,
            "latency_ms": e.latency_ms,
            "error_code": e.error_code,
            "prompt_id": e.prompt_id,
            "auth_oid": auth_oid,
            "auth_upn": auth_upn,
            "feedback": json.dumps(e.feedback.model_dump(exclude_none=True)),
            "interaction": (
                json.dumps(e.interaction.model_dump(exclude_none=True))
                if e.interaction is not None
                else None
            ),
        }
        for e in events
    ]
    with engine.begin() as conn:
        conn.execute(INSERT_EVENT, rows)
