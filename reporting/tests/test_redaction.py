"""Server-side redaction by role (design D12, task 3.2): leadership gets
{"redacted": true, "length": n} in place of every sensitive text field, in
legacy reports, analytics timelines and exports; engineer/admin see verbatim."""

from app.redaction import SENSITIVE_KEYS, marker, redact

from .conftest import seed_event
from .seeds import seed_session, seed_tool_call, seed_turn, ts


def test_redact_marks_sensitive_keys_recursively():
    payload = {
        "event_id": "e1",
        "prompt_text": "hello world",
        "feedback": {"notes": "ok", "user_corrections": None, "score": 3},
        "interaction": {"user_prompt": "why?", "assistant_response": "because"},
        "turns": [{"response_text": "x" * 10, "model": "m"}],
        "payload": {"tool_input": {"path": "/etc/passwd"}},
        "length": 42,
    }
    out = redact(payload)
    assert out["event_id"] == "e1" and out["length"] == 42
    assert out["prompt_text"] == {"redacted": True, "length": 11}
    assert out["feedback"] == {"notes": {"redacted": True, "length": 2}, "user_corrections": None, "score": 3}
    assert out["interaction"]["user_prompt"] == {"redacted": True, "length": 4}
    assert out["turns"][0] == {"response_text": {"redacted": True, "length": 10}, "model": "m"}
    assert out["payload"]["tool_input"]["redacted"] is True
    # the input is not mutated
    assert payload["prompt_text"] == "hello world"


def test_marker_shapes():
    assert marker("abc") == {"redacted": True, "length": 3}
    assert marker(123) == {"redacted": True, "length": 0}
    assert {"prompt_text", "response_text", "summary_text", "input_content", "output_content",
            "title_redacted", "content", "notes", "user_corrections", "user_prompt",
            "assistant_response", "session_title"} <= SENSITIVE_KEYS


def test_reports_events_redacted_for_leadership_only(engine, leadership_client, engineer_client, client):
    seed_event(engine, notes="great job", user_corrections="use LEFT JOIN", user_prompt="join tables?")
    lead = leadership_client.get("/v1/reports/events").json()["events"][0]
    assert lead["feedback"]["notes"] == {"redacted": True, "length": 9}
    assert lead["feedback"]["user_corrections"] == {"redacted": True, "length": 13}
    assert lead["interaction"]["user_prompt"] == {"redacted": True, "length": 12}
    assert lead["skill_name"] == "SQL_Generator"  # non-sensitive fields untouched
    eng = engineer_client.get("/v1/reports/events").json()["events"][0]
    assert eng["feedback"]["notes"] == "great job"
    admin = client.get("/v1/reports/events").json()["events"][0]
    assert admin["interaction"]["user_prompt"] == "join tables?"


def test_timeline_redacted_for_leadership(engine, leadership_client, engineer_client):
    sid = seed_session(engine, started_at=ts(2026, 11, 3), title="Fix the billing bug")
    pid = seed_turn(engine, sid, submitted_at=ts(2026, 11, 3, 12, 1), prompt_text="please fix billing",
                    response_text="done")
    seed_tool_call(engine, sid, prompt_id=pid, started_at=ts(2026, 11, 3, 12, 2), input_content="cat secret.txt",
                   output_content="hunter2")
    lead = leadership_client.get(f"/v1/analytics/sessions/{sid}/timeline").json()
    assert lead["session"]["title_redacted"] == {"redacted": True, "length": 19}
    turn = lead["turns"][0]
    assert turn["prompt_text"] == {"redacted": True, "length": 18}
    assert turn["response_text"] == {"redacted": True, "length": 4}
    assert turn["prompt_length"] == 18
    assert turn["tool_calls"][0]["input_content"] == {"redacted": True, "length": 14}
    assert turn["tool_calls"][0]["output_content"] == {"redacted": True, "length": 7}
    eng = engineer_client.get(f"/v1/analytics/sessions/{sid}/timeline").json()
    assert eng["turns"][0]["prompt_text"] == "please fix billing"
    assert eng["turns"][0]["tool_calls"][0]["output_content"] == "hunter2"
    assert eng["session"]["title_redacted"] == "Fix the billing bug"


def test_sentiment_report_shape_unchanged_and_has_no_text(engine, leadership_client):
    seed_event(engine, notes="this is awful", status="failure")
    body = leadership_client.get("/v1/reports/sentiment").json()
    assert set(body) == {"overall", "skills", "sessions", "trend"}
    assert body["overall"]["event_count"] == 1
