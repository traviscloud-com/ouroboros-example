from .helpers import count_rows, fetch_row, make_batch, make_event, post_events


def test_replayed_batch_returns_202_without_duplicates(client, token, engine):
    events = [make_event(), make_event()]
    batch = make_batch(*events)

    assert post_events(client, token, batch).status_code == 202
    assert post_events(client, token, batch).status_code == 202

    for event in events:
        assert count_rows(engine, event["event_id"]) == 1


def test_mixed_new_and_duplicate_batch(client, token, engine):
    original = make_event(feedback={"notes": "original notes"})
    assert post_events(client, token, make_batch(original)).status_code == 202

    replay = dict(original, feedback={"notes": "attempted overwrite"})
    new = make_event()
    resp = post_events(client, token, make_batch(replay, new))
    assert resp.status_code == 202

    assert count_rows(engine, new["event_id"]) == 1
    row = fetch_row(engine, original["event_id"])
    assert row["feedback"]["notes"] == "original notes"
