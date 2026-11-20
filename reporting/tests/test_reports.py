from .conftest import seed_event


def test_filtered_search(client, engine):
    seed_event(engine, skill_name="SQL_Generator", status="failure")
    seed_event(engine, skill_name="SQL_Generator", status="success")
    seed_event(engine, skill_name="Doc_Writer", status="failure")

    resp = client.get(
        "/v1/reports/events", params={"skill_name": "SQL_Generator", "status": "failure"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["events"][0]["skill_name"] == "SQL_Generator"
    assert body["events"][0]["status"] == "failure"


def test_search_by_session_and_upn(client, engine):
    session = "11111111-1111-1111-1111-111111111111"
    seed_event(engine, session_id=session, upn="alice@example.com")
    seed_event(engine, upn="bob@example.com")

    body = client.get("/v1/reports/events", params={"session_id": session}).json()
    assert body["total"] == 1
    body = client.get("/v1/reports/events", params={"upn": "bob@example.com"}).json()
    assert body["total"] == 1
    assert body["events"][0]["auth_upn"] == "bob@example.com"


def test_free_text_matches_feedback_and_interaction(client, engine):
    hit_corrections = seed_event(engine, user_corrections="please use a LEFT JOIN here")
    hit_prompt = seed_event(engine, user_prompt="how do I join two tables?")
    seed_event(engine, notes="unrelated")

    body = client.get("/v1/reports/events", params={"q": "join"}).json()
    found = {e["event_id"] for e in body["events"]}
    assert found == {hit_corrections, hit_prompt}


def test_pagination_bounds_results(client, engine):
    for _ in range(25):
        seed_event(engine)
    body = client.get("/v1/reports/events", params={"limit": 10, "offset": 10}).json()
    assert len(body["events"]) == 10
    assert body["total"] == 25


def test_events_newest_first(client, engine):
    older = seed_event(engine, age_seconds=100)
    newer = seed_event(engine, age_seconds=10)
    body = client.get("/v1/reports/events").json()
    assert [e["event_id"] for e in body["events"]] == [newer, older]


def test_skill_rates_math(client, engine):
    for _ in range(8):
        seed_event(engine, skill_name="SQL_Generator", status="success")
    seed_event(engine, skill_name="SQL_Generator", status="failure")
    seed_event(engine, skill_name="SQL_Generator", status="user_corrected")
    seed_event(engine, skill_name="Doc_Writer", status="success")

    body = client.get("/v1/reports/skills").json()
    assert [s["skill_name"] for s in body["skills"]] == ["SQL_Generator", "Doc_Writer"]
    sql = body["skills"][0]
    assert sql["total"] == 10
    assert sql["success_rate"] == 0.8
    assert sql["failure_rate"] == 0.1
    assert sql["user_corrected_rate"] == 0.1


def test_overview_totals(client, engine):
    session = "22222222-2222-2222-2222-222222222222"
    seed_event(engine, skill_name="A", upn="alice@example.com", session_id=session)
    seed_event(engine, skill_name="A", upn="alice@example.com", status="failure")
    seed_event(engine, skill_name="B", upn="bob@example.com")

    body = client.get("/v1/reports/overview").json()
    assert body["totals"] == {"events": 3, "sessions": 3, "users": 2, "skills": 2}
    assert body["status_breakdown"] == {"success": 2, "failure": 1}
    assert sum(a["count"] for a in body["activity"]) == 3


def test_read_only_surface(client, engine):
    event_id = seed_event(engine)
    for method in ("post", "put", "patch", "delete"):
        resp = getattr(client, method)("/v1/reports/events")
        assert resp.status_code in (404, 405)
    assert client.get("/v1/reports/events").json()["total"] == 1
    assert client.get("/v1/reports/events").json()["events"][0]["event_id"] == event_id


def test_health_probes(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200
