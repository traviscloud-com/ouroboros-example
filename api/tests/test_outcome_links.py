"""Outcome events link to their turn and skill artifact (session-model spec)."""

import uuid
from datetime import datetime, timezone

from .helpers import fetch_row, make_batch, make_event, post_events
from .test_artifacts import make_artifact
from .test_artifacts import post as post_artifacts
from .test_hook_events import URL as HOOK_URL


def _prompt_submit(session_id: str, prompt_id: str) -> dict:
    return {
        "events": [
            {
                "hook_event_id": str(uuid.uuid4()),
                "session_id": session_id,
                "prompt_id": prompt_id,
                "hook_event_name": "UserPromptSubmit",
                "client_ts": datetime.now(timezone.utc).isoformat(),
                "client_seq": 1,
                "payload": {},
            }
        ]
    }


def _post_hook(client, token, body):
    resp = client.post(HOOK_URL, json=body, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 202, resp.text
    assert resp.json()["rejected"] == []


def _turn_id(engine, prompt_id):
    from sqlalchemy import text

    with engine.connect() as conn:
        return conn.execute(
            text("SELECT turn_id FROM turns WHERE prompt_id = :p"), {"p": prompt_id}
        ).scalar_one()


def test_outcome_links_to_existing_turn(client, token, engine):
    sid, pid = str(uuid.uuid4()), str(uuid.uuid4())
    _post_hook(client, token, _prompt_submit(sid, pid))
    event = make_event(session_id=sid, prompt_id=pid)
    assert post_events(client, token, make_batch(event)).status_code == 202
    row = fetch_row(engine, event["event_id"])
    assert row["prompt_id"] == pid
    assert row["turn_id"] == _turn_id(engine, pid)


def test_outcome_arriving_before_its_turn_is_linked_later(client, token, engine):
    sid, pid = str(uuid.uuid4()), str(uuid.uuid4())
    event = make_event(session_id=sid, prompt_id=pid)
    assert post_events(client, token, make_batch(event)).status_code == 202
    assert fetch_row(engine, event["event_id"])["turn_id"] is None
    _post_hook(client, token, _prompt_submit(sid, pid))
    assert fetch_row(engine, event["event_id"])["turn_id"] == _turn_id(engine, pid)


def test_outcome_without_prompt_id_has_no_turn_link(client, token, engine):
    event = make_event()
    assert post_events(client, token, make_batch(event)).status_code == 202
    row = fetch_row(engine, event["event_id"])
    assert row["prompt_id"] is None and row["turn_id"] is None


def test_outcome_links_to_registered_skill_artifact(client, token, engine):
    name = f"skill-{uuid.uuid4().hex[:8]}"
    assert post_artifacts(client, token, [make_artifact(name)]).status_code == 202
    known = make_event(skill_name=name)
    unknown = make_event(skill_name=f"unregistered-{uuid.uuid4().hex[:8]}")
    assert post_events(client, token, make_batch(known, unknown)).status_code == 202
    assert fetch_row(engine, known["event_id"])["artifact_id"] is not None
    assert fetch_row(engine, unknown["event_id"])["artifact_id"] is None
