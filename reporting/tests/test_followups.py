"""Phase 4 follow-ups: paginated ``GET /v1/analytics/sessions``, the
``error_code`` filter on ``/v1/reports/events`` and tool success / failure
counts on ``/v1/analytics/outcomes``."""

from datetime import timedelta

from app.scheduler import refresh_matviews
from tests.conftest import seed_event
from tests.seeds import seed_compaction, seed_session, seed_tool_call, seed_turn, seed_usage, ts

DAY = ts(2026, 10, 5)
WINDOW = {"from": "2026-10-01", "to": "2026-10-10"}


def _sessions(engine):
    ids = []
    for i in range(5):
        sid = seed_session(engine, started_at=DAY + timedelta(hours=i), project_key=f"proj-{i % 2}",
                           title=f"secret title {i}")
        for seq in range(i + 1):
            seed_turn(engine, sid, seq=seq + 1, submitted_at=DAY + timedelta(hours=i, minutes=seq))
        seed_usage(engine, sid, at=DAY + timedelta(hours=i), cost_usd=0.01 * (i + 1))
        ids.append(sid)
    seed_tool_call(engine, ids[0], started_at=DAY, success=False)
    seed_compaction(engine, ids[0], at=DAY)
    seed_event(engine, session_id=ids[0], status="user_corrected", at=DAY)
    seed_session(engine, started_at=ts(2026, 9, 1))  # outside the window
    refresh_matviews(engine)
    return ids


def test_sessions_paginated_and_ordered(client, engine):
    ids = _sessions(engine)
    page1 = client.get("/v1/analytics/sessions", params={**WINDOW, "limit": 2}).json()
    assert page1["total"] == 5 and len(page1["sessions"]) == 2 and page1["limit"] == 2
    assert [s["session_id"] for s in page1["sessions"]] == [ids[4], ids[3]]  # newest first
    page3 = client.get("/v1/analytics/sessions", params={**WINDOW, "limit": 2, "offset": 4}).json()
    assert [s["session_id"] for s in page3["sessions"]] == [ids[0]]
    first = page3["sessions"][0]
    assert (first["turns"], first["tool_calls"], first["tool_failures"], first["compactions"]) == (1, 1, 1, 1)
    assert (first["outcomes"], first["user_corrected"], first["api_requests"]) == (1, 1, 1)
    assert first["title_redacted"] == "secret title 0"  # admin sees verbatim
    by_cost = client.get("/v1/analytics/sessions", params={**WINDOW, "order": "cost"}).json()
    assert by_cost["sessions"][0]["session_id"] == ids[4]
    assert by_cost["totals"]["sessions"] == 5 and abs(by_cost["totals"]["cost_usd"] - 0.15) < 1e-9
    assert client.get("/v1/analytics/sessions", params={**WINDOW, "project_key": "proj-1"}).json()["total"] == 2
    assert client.get("/v1/analytics/sessions", params={**WINDOW, "q": ids[2][:8]}).json()["total"] == 1
    assert client.get("/v1/analytics/sessions", params={**WINDOW, "order": "bogus"}).status_code == 422


def test_sessions_redacted_for_leadership(leadership_client, engine):
    _sessions(engine)
    row = leadership_client.get("/v1/analytics/sessions", params=WINDOW).json()["sessions"][0]
    assert row["title_redacted"]["redacted"] is True


def test_events_error_code_filter(client, engine):
    seed_event(engine, status="failure", error_code="TIMEOUT")
    seed_event(engine, status="failure", error_code="TIMEOUT")
    seed_event(engine, status="failure", error_code="AUTH")
    seed_event(engine, status="success")
    body = client.get("/v1/reports/events", params={"error_code": "TIMEOUT"}).json()
    assert body["total"] == 2 and {e["error_code"] for e in body["events"]} == {"TIMEOUT"}
    both = client.get("/v1/reports/events", params={"error_code": "AUTH", "status": "failure"}).json()
    assert both["total"] == 1
    assert client.get("/v1/reports/events", params={"error_code": "NONE"}).json()["total"] == 0


def test_tool_success_failure_counts(client, engine):
    sid = seed_session(engine, started_at=DAY)
    for ok in (True, True, False):
        seed_tool_call(engine, sid, tool_name="Bash", started_at=DAY, success=ok)
    seed_tool_call(engine, sid, tool_name="Read", started_at=DAY, success=True)
    refresh_matviews(engine)
    body = client.get("/v1/analytics/outcomes", params=WINDOW).json()["current"]
    tools = {t["tool_name"]: t for t in body["tools"]}
    assert (tools["Bash"]["calls"], tools["Bash"]["success"], tools["Bash"]["failure"]) == (3, 2, 1)
    assert abs(tools["Bash"]["failure_rate"] - 1 / 3) < 1e-9 and tools["Read"]["failure"] == 0
    assert body["totals"]["tool_calls"] == 4 and body["totals"]["tool_failures"] == 1
