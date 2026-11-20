"""Modeling export (design D12, task 3.5)."""

import io
import json

import pytest
from sqlalchemy import text

from app import export

from .conftest import ADMIN_EMAIL, make_client, seed_event
from .seeds import seed_session, seed_turn, ts


def _ndjson(resp) -> list[dict]:
    return [json.loads(line) for line in resp.text.splitlines() if line]


@pytest.fixture()
def turns(engine):
    sid = seed_session(engine, started_at=ts(2026, 11, 3))
    for i in range(3):
        seed_turn(engine, sid, seq=i + 1, submitted_at=ts(2026, 11, 3, 9 + i), prompt_text=f"prompt {i}",
                  response_text=f"response {i}")
    seed_turn(engine, sid, seq=9, submitted_at=ts(2026, 10, 1), prompt_text="old")
    return sid


def test_engineer_exports_verbatim_ndjson_with_audit(engine, engineer_client, turns):
    resp = engineer_client.get("/v1/export/turns", params={"from": "2026-11-01", "to": "2026-11-30"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert resp.headers["x-export-total-rows"] == "3" and resp.headers["x-export-truncated"] == "false"
    assert resp.headers["x-export-redacted"] == "false"
    assert 'filename="turns_2026-11-01_2026-11-30.ndjson"' in resp.headers["content-disposition"]
    rows = _ndjson(resp)
    assert [r["prompt_text"] for r in rows] == ["prompt 0", "prompt 1", "prompt 2"]
    assert rows[0]["session_id"] == turns and "raw" not in rows[0] and "prompt_tsv" not in rows[0]
    with engine.connect() as conn:
        audit = conn.execute(text("SELECT actor_email, actor_role, action, target_id, details FROM audit_log")).one()
    assert (audit.actor_email, audit.actor_role, audit.action, audit.target_id) == (
        "eng@example.com", "engineer", "export", "turns")
    assert audit.details["rows"] == 3 and audit.details["from"] == "2026-11-01" and audit.details["redacted"] is False


def test_redact_flag_and_leadership_forbidden(engineer_client, leadership_client, turns):
    rows = _ndjson(engineer_client.get("/v1/export/turns", params={"from": "2026-11-01", "to": "2026-11-30",
                                                                  "redact": "1"}))
    assert rows[0]["prompt_text"] == {"redacted": True, "length": 8}
    assert rows[0]["prompt_length"] == 8
    assert leadership_client.get("/v1/export/turns").status_code == 403


def test_unknown_dataset_and_bad_format(engineer_client):
    assert engineer_client.get("/v1/export/audit_log").status_code == 404
    assert engineer_client.get("/v1/export/turns", params={"format": "csv"}).status_code == 422


def test_row_cap_truncates_and_says_so(pg_url, engine, turns):
    with make_client(pg_url, export_max_rows=2) as c:
        resp = c.get("/v1/export/turns", params={"from": "2026-11-01", "to": "2026-11-30"})
    assert resp.headers["x-export-truncated"] == "true" and resp.headers["x-export-row-cap"] == "2"
    assert len(_ndjson(resp)) == 2
    with engine.connect() as conn:
        details = conn.execute(text("SELECT details FROM audit_log WHERE action = 'export'")).scalar_one()
    assert details["truncated"] is True and details["rows"] == 2 and details["total_rows"] == 3


def test_every_dataset_streams(engine, client, turns):
    seed_event(engine, at=ts(2026, 11, 3))
    for dataset in export.DATASETS:
        resp = client.get(f"/v1/export/{dataset}", params={"from": "2026-11-01", "to": "2026-11-30"})
        assert resp.status_code == 200, dataset
        assert all(isinstance(r, dict) for r in _ndjson(resp))
    assert _ndjson(client.get("/v1/export/events", params={"from": "2026-11-01", "to": "2026-11-30"}))[0]["feedback"] == {"notes": "ok"}


def test_parquet_roundtrip(engine, client, turns):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    seed_event(engine, at=ts(2026, 11, 3), notes="parquet row")
    resp = client.get("/v1/export/turns", params={"from": "2026-11-01", "to": "2026-11-30", "format": "parquet"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/vnd.apache.parquet")
    table = pq.read_table(io.BytesIO(resp.content))
    assert table.num_rows == 3
    assert table.column("prompt_text").to_pylist() == ["prompt 0", "prompt 1", "prompt 2"]
    assert pa.types.is_timestamp(table.schema.field("submitted_at").type)
    assert pa.types.is_int64(table.schema.field("seq").type)
    events = pq.read_table(io.BytesIO(client.get(
        "/v1/export/events", params={"from": "2026-11-01", "to": "2026-11-30", "format": "parquet"}).content))
    assert json.loads(events.column("feedback").to_pylist()[0]) == {"notes": "parquet row"}
    assert pa.types.is_floating(events.schema.field("latency_ms").type) or pa.types.is_integer(
        events.schema.field("latency_ms").type)
    empty = pq.read_table(io.BytesIO(client.get(
        "/v1/export/sessions", params={"from": "2020-01-01", "to": "2020-01-02", "format": "parquet"}).content))
    assert empty.num_rows == 0 and "session_id" in empty.schema.names


def test_parquet_unavailable_is_501(client, turns, monkeypatch):
    monkeypatch.setattr(export, "parquet_available", lambda: False)
    assert client.get("/v1/export/turns", params={"format": "parquet"}).status_code == 501
