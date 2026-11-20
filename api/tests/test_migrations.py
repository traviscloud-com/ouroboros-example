"""Versioned migrations (design D11): fresh apply, idempotent rerun, convergence
from the legacy single-file schema, checksum enforcement, and replica races.

Database-backed tests create a scratch database per test on the server behind
``pg_url`` so each scenario starts empty; the unit tests need no database.
"""

import hashlib
import json
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.migrations import (
    Migration,
    MigrationChecksumError,
    MigrationError,
    apply_migrations,
    current_version,
    discover,
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
LATEST_VERSION = 11

EXPECTED_TABLES = {
    "schema_migrations",
    "events",
    "hook_events_raw", "sessions", "turns", "tool_calls", "subagent_runs",
    "compactions", "instructions_loaded", "model_switches", "turn_errors", "hook_runs",
    "usage_facts", "skill_activations",
    "artifacts", "artifact_versions",
    "improvement_proposals", "improvement_outcomes", "audit_log",
    "principals",
    "transcript_lines",
    "analyst_runs",
}
EXPECTED_VIEWS = {
    "adoption_daily", "adoption_weekly", "outcomes_daily", "error_codes_daily",
    "latency_daily", "cost_daily", "cost_per_session", "context_bloat_daily",
}
# Scheduler-written analytics tables (0010): the reporting service fills these in Python.
EXPECTED_ANALYTICS_TABLES = {"sentiment_daily", "correction_themes"}


# --- unit: discovery -------------------------------------------------------------

def test_discover_orders_contiguous_versions():
    found = discover(MIGRATIONS_DIR)
    assert [m.version for m in found] == list(range(1, LATEST_VERSION + 1))
    assert found[0].name == "baseline"
    assert found[-1].name == "loop_states"
    for m in found:
        assert m.checksum == hashlib.sha256(m.path.read_bytes()).hexdigest()


def test_discover_rejects_gaps(tmp_path):
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "0003_c.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="contiguous"):
        discover(tmp_path)


def test_discover_rejects_duplicate_versions(tmp_path):
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "0001_b.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="contiguous"):
        discover(tmp_path)


def test_discover_rejects_bad_filenames(tmp_path):
    (tmp_path / "1_bad.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="NNNN_snake_name"):
        discover(tmp_path)


def test_baseline_is_the_legacy_schema_verbatim():
    sql = (MIGRATIONS_DIR / "0001_baseline.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS events" in sql
    assert "ALTER TABLE events ADD COLUMN IF NOT EXISTS interaction JSONB" in sql


# --- database-backed ----------------------------------------------------------------

@pytest.fixture()
def fresh_db_url(pg_url):
    """A new empty database on the test server, dropped afterwards."""
    url = make_url(pg_url)
    name = "ouroboros_mig_" + uuid.uuid4().hex[:12]
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield url.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture()
def fresh_engine(fresh_db_url):
    eng = create_engine(fresh_db_url)
    yield eng
    eng.dispose()


def table_names(engine) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                )
            ).scalars()
        )


def analytics_table_names(engine) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'analytics' AND table_type = 'BASE TABLE'"
                )
            ).scalars()
        )


def matview_names(engine) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text("SELECT matviewname FROM pg_matviews WHERE schemaname = 'analytics'")
            ).scalars()
        )


def history(engine) -> list[tuple]:
    with engine.connect() as conn:
        return [
            tuple(r)
            for r in conn.execute(
                text("SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version")
            )
        ]


def test_fresh_database_applies_everything(fresh_engine):
    assert current_version(fresh_engine) is None
    assert apply_migrations(fresh_engine) == list(range(1, LATEST_VERSION + 1))
    assert current_version(fresh_engine) == LATEST_VERSION
    assert table_names(fresh_engine) == EXPECTED_TABLES
    assert matview_names(fresh_engine) == EXPECTED_VIEWS
    assert analytics_table_names(fresh_engine) == EXPECTED_ANALYTICS_TABLES
    assert [h[0] for h in history(fresh_engine)] == list(range(1, LATEST_VERSION + 1))


def test_rerun_is_a_noop(fresh_engine):
    apply_migrations(fresh_engine)
    before = history(fresh_engine)
    assert apply_migrations(fresh_engine) == []
    assert apply_migrations(fresh_engine) == []
    assert history(fresh_engine) == before


def test_every_file_is_idempotent_when_replayed(fresh_engine):
    """Forgetting the history and re-running every file must succeed (IF NOT EXISTS)."""
    apply_migrations(fresh_engine)
    with fresh_engine.begin() as conn:
        conn.execute(text("DELETE FROM schema_migrations"))
    assert apply_migrations(fresh_engine) == list(range(1, LATEST_VERSION + 1))
    assert table_names(fresh_engine) == EXPECTED_TABLES


def test_baseline_converges_on_legacy_events_only_database(fresh_engine):
    legacy_sql = (MIGRATIONS_DIR / "0001_baseline.sql").read_text()
    event_id = str(uuid.uuid4())
    with fresh_engine.begin() as conn:
        conn.exec_driver_sql(legacy_sql)  # how api/schema.sql used to be applied
        conn.execute(
            text(
                "INSERT INTO events (event_id, session_id, client_ts, skill_name, skill_version, "
                "status, auth_oid, auth_upn, feedback) VALUES (:id, :sid, now(), 'SQL_Generator', "
                "'1.0', 'user_corrected', 'oid', 'dev@ouroboros.local', CAST(:fb AS JSONB))"
            ),
            {
                "id": event_id,
                "sid": str(uuid.uuid4()),
                "fb": json.dumps({"notes": "ok", "user_corrections": "use the index please"}),
            },
        )
    assert table_names(fresh_engine) == {"events"}

    assert apply_migrations(fresh_engine) == list(range(1, LATEST_VERSION + 1))
    assert table_names(fresh_engine) == EXPECTED_TABLES
    with fresh_engine.connect() as conn:
        row = conn.execute(
            text("SELECT prompt_id, turn_id, artifact_id, feedback_tsv FROM events WHERE event_id = :id"),
            {"id": event_id},
        ).one()
    assert row.prompt_id is None and row.turn_id is None and row.artifact_id is None
    assert "index" in str(row.feedback_tsv)


def test_checksum_mismatch_is_a_hard_error(fresh_engine, tmp_path):
    apply_migrations(fresh_engine)
    tampered = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, tampered)
    with (tampered / "0002_sessions.sql").open("a") as f:
        f.write("\n-- edited after it shipped\n")
    with pytest.raises(MigrationChecksumError, match="0002_sessions.sql"):
        apply_migrations(fresh_engine, tampered)
    assert current_version(fresh_engine) == LATEST_VERSION


def test_tampered_history_row_is_a_hard_error(fresh_engine):
    apply_migrations(fresh_engine)
    with fresh_engine.begin() as conn:
        conn.execute(text("UPDATE schema_migrations SET checksum = 'deadbeef' WHERE version = 3"))
    with pytest.raises(MigrationChecksumError, match="0003_usage.sql"):
        apply_migrations(fresh_engine)


def test_failed_migration_leaves_prior_versions_recorded(fresh_engine, tmp_path):
    broken = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, broken)
    (broken / f"{LATEST_VERSION + 1:04d}_broken.sql").write_text("CREATE TABLE IF NOT EXISTS ok (x int);\nSELECT * FROM no_such_table;\n")
    with pytest.raises(Exception):
        apply_migrations(fresh_engine, broken)
    assert current_version(fresh_engine) == LATEST_VERSION
    assert "ok" not in table_names(fresh_engine)  # the whole file rolled back


def test_concurrent_replicas_do_not_race(fresh_db_url):
    results: dict[int, object] = {}

    def run(i: int) -> None:
        eng = create_engine(fresh_db_url)
        try:
            results[i] = apply_migrations(eng)
        except Exception as exc:  # pragma: no cover - surfaced by the assertions
            results[i] = exc
        finally:
            eng.dispose()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    assert all(isinstance(r, list) for r in results.values()), results
    assert sorted(len(r) for r in results.values()) == [0, 0, LATEST_VERSION]
    eng = create_engine(fresh_db_url)
    try:
        assert [h[0] for h in history(eng)] == list(range(1, LATEST_VERSION + 1))
    finally:
        eng.dispose()


def test_analytics_views_compute_and_refresh_concurrently(fresh_engine):
    apply_migrations(fresh_engine)
    now = datetime.now(timezone.utc)
    sid = str(uuid.uuid4())
    with fresh_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sessions (session_id, started_at, project_key, auth_oid, auth_upn) "
                "VALUES (:sid, :now, 'proj', 'oid', 'dev@ouroboros.local')"
            ),
            {"sid": sid, "now": now},
        )
        conn.execute(
            text(
                "INSERT INTO turns (session_id, prompt_id, seq, submitted_at, prompt_text, auth_oid, auth_upn) "
                "VALUES (:sid, 'p1', 1, :now, 'summarise the quarterly numbers', 'oid', 'dev@ouroboros.local')"
            ),
            {"sid": sid, "now": now},
        )
        conn.execute(
            text(
                "INSERT INTO events (event_id, session_id, client_ts, skill_name, skill_version, status, "
                "latency_ms, auth_oid, auth_upn, feedback, prompt_id) VALUES "
                "(:e1, :sid, :now, 'SQL_Generator', '1.0', 'success', 120, 'oid', 'u', '{\"notes\": \"ok\"}', 'p1'), "
                "(:e2, :sid, :now, 'SQL_Generator', '1.0', 'user_corrected', 300, 'oid', 'u', "
                "'{\"notes\": \"\", \"user_corrections\": \"use the primary key index not a scan\"}', 'p1')"
            ),
            {"e1": str(uuid.uuid4()), "e2": str(uuid.uuid4()), "sid": sid, "now": now},
        )
        conn.execute(
            text(
                "INSERT INTO usage_facts (session_id, prompt_id, at, kind, model, token_type, value, cost_usd, "
                "skill_name, request_id, attrs, auth_oid, auth_upn) VALUES "
                # reconciliation-only delta counters: excluded from the cost views
                "(:sid, NULL, :now, 'token', 'claude-sonnet-4-5', 'input', 1000, NULL, 'SQL_Generator', NULL, '{}', 'oid', 'u'), "
                "(:sid, NULL, :now, 'cost', 'claude-sonnet-4-5', NULL, 0.0123, 0.0123, 'SQL_Generator', NULL, '{}', 'oid', 'u'), "
                # the primary usage fact
                "(:sid, 'p1', :now, 'api_request', 'claude-sonnet-4-5', NULL, NULL, 0.0123, 'SQL_Generator', "
                "'req_1', CAST(:attrs AS JSONB), 'oid', 'u')"
            ),
            {
                "sid": sid,
                "now": now,
                "attrs": json.dumps(
                    {"input_tokens": 1000, "output_tokens": 200, "cache_read_tokens": 5000,
                     "cache_creation_tokens": 0, "duration_ms": 1800, "ttft_ms": 400}
                ),
            },
        )
        conn.execute(
            text(
                "INSERT INTO compactions (session_id, prompt_id, trigger, pre_tokens, post_tokens, at, auth_oid, auth_upn) "
                "VALUES (:sid, 'p1', 'auto', 150000, 40000, :now, 'oid', 'u')"
            ),
            {"sid": sid, "now": now},
        )

    with fresh_engine.connect() as conn:
        for view in sorted(EXPECTED_VIEWS):
            conn.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY analytics.{view}"))
        conn.commit()

        outcomes = conn.execute(text("SELECT * FROM analytics.outcomes_daily")).mappings().one()
        assert (outcomes["success"], outcomes["user_corrected"], outcomes["total"]) == (1, 1, 2)
        assert float(outcomes["correction_rate"]) == 0.5

        latency = conn.execute(
            text("SELECT * FROM analytics.latency_daily WHERE kind = 'skill'")
        ).mappings().one()
        assert latency["samples"] == 2 and latency["p50"] == 210

        cost = conn.execute(text("SELECT * FROM analytics.cost_daily")).mappings().one()
        assert (float(cost["input_tokens"]), float(cost["output_tokens"])) == (1000, 200)
        assert float(cost["cache_read"]) == 5000
        assert float(cost["cost_usd"]) == pytest.approx(0.0123)
        assert (cost["api_requests"], cost["sessions"]) == (1, 1)
        per_session = conn.execute(text("SELECT * FROM analytics.cost_per_session")).mappings().one()
        assert float(per_session["cost_usd"]) == pytest.approx(0.0123) and per_session["api_requests"] == 1

        bloat = conn.execute(text("SELECT * FROM analytics.context_bloat_daily")).mappings().one()
        assert bloat["project_key"] == "proj" and bloat["compactions"] == 1 and bloat["turns"] == 1
        assert float(bloat["mean_pre_tokens"]) == 150000
        assert float(bloat["bloat_index"]) > 0

        adoption = conn.execute(
            text("SELECT * FROM analytics.adoption_daily WHERE skill_or_agent = 'SQL_Generator'")
        ).mappings().one()
        assert (adoption["dau"], adoption["sessions"], adoption["turns"], adoption["invocations"]) == (1, 1, 1, 2)

        # 0010: sentiment_daily / correction_themes are tables the reporting
        # scheduler writes; the migration leaves them empty.
        assert conn.execute(text("SELECT count(*) FROM analytics.correction_themes")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM analytics.sentiment_daily")).scalar_one() == 0
        assert conn.execute(
            text("SELECT count(*) FROM events WHERE interaction_tsv @@ websearch_to_tsquery('english', 'scan')")
        ).scalar_one() == 0

        hit = conn.execute(
            text("SELECT count(*) FROM turns WHERE prompt_tsv @@ websearch_to_tsquery('english', 'quarterly')")
        ).scalar_one()
        assert hit == 1
        hit = conn.execute(
            text("SELECT count(*) FROM events WHERE feedback_tsv @@ websearch_to_tsquery('english', 'index')")
        ).scalar_one()
        assert hit == 1


def test_loop_states_extend_the_proposal_state_machine(fresh_engine):
    """0011: pr_opened / failed are valid states, unknown ones are not, and the
    PR bookkeeping columns plus analyst_runs exist."""
    apply_migrations(fresh_engine)
    with fresh_engine.begin() as conn:
        aid = conn.execute(
            text(
                "INSERT INTO artifacts (kind, name, auth_oid, auth_upn) "
                "VALUES ('skill', 's', 'o', 'u') RETURNING artifact_id"
            )
        ).scalar_one()
        pid = conn.execute(
            text(
                "INSERT INTO improvement_proposals (target_artifact_id, rationale, diff, risk_class, "
                "created_by, status, pr_repo, pr_number, pr_branch) "
                "VALUES (:a, 'r', 'd', 'low', 'loop', 'pr_opened', 'org/repo', 7, 'ouroboros/x') "
                "RETURNING proposal_id"
            ),
            {"a": aid},
        ).scalar_one()
        conn.execute(text("UPDATE improvement_proposals SET status = 'failed' WHERE proposal_id = :p"), {"p": pid})
        conn.execute(
            text("INSERT INTO analyst_runs (trigger, model) VALUES ('manual', 'm')")
        )
    with pytest.raises(Exception):
        with fresh_engine.begin() as conn:
            conn.execute(text("UPDATE improvement_proposals SET status = 'bogus' WHERE proposal_id = :p"), {"p": pid})
