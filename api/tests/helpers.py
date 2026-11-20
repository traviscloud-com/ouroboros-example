import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


def make_event(age_seconds: int = 0, **overrides) -> dict:
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    event = {
        "event_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "client_ts": ts.isoformat(),
        "skill_name": "SQL_Generator",
        "skill_version": "2.1.0",
        "status": "success",
        "latency_ms": 123,
        "feedback": {"notes": "worked fine"},
    }
    event.update(overrides)
    return event


def make_batch(*events, schema_version: str = "1.0") -> dict:
    return {"schema_version": schema_version, "events": list(events) or [make_event()]}


def post_events(client, token: str, body: dict):
    return client.post(
        "/v1/telemetry/events",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


def count_rows(engine, event_id: str) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT count(*) FROM events WHERE event_id = :id"), {"id": event_id}
        ).scalar_one()


def fetch_row(engine, event_id: str):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT * FROM events WHERE event_id = :id"), {"id": event_id}
        ).mappings().one_or_none()
