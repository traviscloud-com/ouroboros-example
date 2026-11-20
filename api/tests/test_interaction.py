from sqlalchemy import text

from app.db import apply_schema

from .helpers import count_rows, fetch_row, make_batch, make_event, post_events


def test_v11_event_with_interaction_round_trips(client, token, engine):
    interaction = {
        "user_prompt": "generate a query joining orders to customers",
        "assistant_response": "SELECT * FROM orders JOIN customers USING (customer_id)",
    }
    event = make_event(interaction=interaction)
    resp = post_events(client, token, make_batch(event, schema_version="1.1"))
    assert resp.status_code == 202
    assert fetch_row(engine, event["event_id"])["interaction"] == interaction


def test_v11_event_without_interaction_accepted(client, token, engine):
    event = make_event()
    resp = post_events(client, token, make_batch(event, schema_version="1.1"))
    assert resp.status_code == 202
    assert fetch_row(engine, event["event_id"])["interaction"] is None


def test_v10_batch_unchanged(client, token, engine):
    event = make_event()
    resp = post_events(client, token, make_batch(event))
    assert resp.status_code == 202
    assert fetch_row(engine, event["event_id"])["interaction"] is None


def test_interaction_on_v10_rejected(client, token, engine):
    event = make_event(interaction={"user_prompt": "hello"})
    resp = post_events(client, token, make_batch(event, schema_version="1.0"))
    assert resp.status_code == 422
    assert count_rows(engine, event["event_id"]) == 0


def test_interaction_column_migration_idempotent(client, token, engine):
    event = make_event()
    assert post_events(client, token, make_batch(event)).status_code == 202

    # Simulate a database created before v1.1 whose baseline was never recorded
    # (the pre-migrations single-file schema), then converge it twice.
    with engine.begin() as conn:
        # CASCADE also drops the generated interaction_tsv column from 0010, so
        # un-record that version too and let the idempotent re-apply restore it.
        conn.execute(text("ALTER TABLE events DROP COLUMN IF EXISTS interaction CASCADE"))
        conn.execute(text("DELETE FROM schema_migrations WHERE version IN (1, 10)"))
    assert apply_schema(engine) == [1, 10]
    assert apply_schema(engine) == []
    with engine.connect() as conn:
        assert conn.execute(text("SELECT to_regclass('idx_events_interaction_tsv')")).scalar_one() is not None

    row = fetch_row(engine, event["event_id"])
    assert row is not None
    assert row["interaction"] is None
