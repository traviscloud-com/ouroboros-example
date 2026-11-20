"""Settings come from environment variables exactly as the Deployment sets them
(comma lists, plain strings), not JSON."""

from app.settings import Settings


def test_comma_lists_and_blank_token_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/x")
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "127.0.0.1/32,172.16.0.0/12, 10.42.0.0/16 ")
    monkeypatch.setenv("ADMIN_EMAILS", "Admin@Example.com, second@example.com")
    monkeypatch.setenv("INTERNAL_REFRESH_TOKEN", "")
    monkeypatch.setenv("AGGREGATE_REFRESH_SECONDS", "0")
    monkeypatch.setenv("TRUST_FORWARDED_FOR", "1")
    s = Settings()
    assert s.trusted_proxy_cidrs == ["127.0.0.1/32", "172.16.0.0/12", "10.42.0.0/16"]
    assert s.admin_emails == ["admin@example.com", "second@example.com"]
    assert s.internal_refresh_token is None
    assert s.aggregate_refresh_seconds == 0 and s.trust_forwarded_for is True


def test_empty_lists_and_defaults(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/x")
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "")
    monkeypatch.setenv("ADMIN_EMAILS", "")
    monkeypatch.delenv("INTERNAL_REFRESH_TOKEN", raising=False)
    s = Settings()
    assert s.trusted_proxy_cidrs == [] and s.admin_emails == []
    assert (s.aggregate_refresh_seconds, s.retention_sensitive_days, s.retention_internal_days) == (300, 30, 365)
    assert s.export_max_rows == 1_000_000 and s.trust_forwarded_for is False
    assert s.internal_refresh_token is None and s.themes_llm is False


def test_lists_accepted_programmatically():
    s = Settings(database_url="x", trusted_proxy_cidrs=["127.0.0.1/32"], admin_emails=["A@b.c"])
    assert s.trusted_proxy_cidrs == ["127.0.0.1/32"] and s.admin_emails == ["a@b.c"]
