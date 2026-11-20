from .helpers import count_rows, make_batch, make_event, post_events


def test_valid_single_event_batch_accepted(client, token, engine):
    event = make_event(feedback={
        "notes": "did the thing",
        "user_corrections": "actually use LEFT JOIN",
        "suggested_patch": "prefer LEFT JOIN in examples",
    })
    resp = post_events(client, token, make_batch(event))
    assert resp.status_code == 202
    assert count_rows(engine, event["event_id"]) == 1


def test_valid_multi_event_batch_accepted(client, token, engine):
    events = [make_event(), make_event(status="failure", error_code="E42"),
              make_event(status="user_corrected")]
    resp = post_events(client, token, make_batch(*events))
    assert resp.status_code == 202
    for event in events:
        assert count_rows(engine, event["event_id"]) == 1


def test_feedback_blob_round_trips(client, token, engine):
    feedback = {
        "notes": "n",
        "user_corrections": "verbatim user text",
        "suggested_patch": "patch text",
    }
    event = make_event(feedback=feedback)
    assert post_events(client, token, make_batch(event)).status_code == 202
    from .helpers import fetch_row
    assert fetch_row(engine, event["event_id"])["feedback"] == feedback


def test_empty_events_array_rejected(client, token):
    resp = post_events(client, token, {"schema_version": "1.0", "events": []})
    assert resp.status_code == 422


def test_bad_status_rejects_whole_batch(client, token, engine):
    good = make_event()
    bad = make_event(status="sorta_worked")
    resp = post_events(client, token, make_batch(good, bad))
    assert resp.status_code == 422
    assert count_rows(engine, good["event_id"]) == 0


def test_malformed_uuid_rejected(client, token, engine):
    event = make_event(event_id="not-a-uuid")
    assert post_events(client, token, make_batch(event)).status_code == 422

    event = make_event(session_id="also-not-a-uuid")
    resp = post_events(client, token, make_batch(event))
    assert resp.status_code == 422
    assert count_rows(engine, event["event_id"]) == 0


def test_unknown_schema_version_rejected(client, token):
    body = make_batch()
    body["schema_version"] = "2.0"
    assert post_events(client, token, body).status_code == 422


def test_missing_feedback_notes_rejected(client, token):
    event = make_event(feedback={"suggested_patch": "no notes"})
    assert post_events(client, token, make_batch(event)).status_code == 422
