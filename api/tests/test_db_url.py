from app.db import normalize_database_url


def test_plain_postgresql_url_gets_psycopg_driver():
    assert (
        normalize_database_url("postgresql://u:p@db-rw:5432/ouroboros")
        == "postgresql+psycopg://u:p@db-rw:5432/ouroboros"
    )


def test_postgres_scheme_alias():
    assert normalize_database_url("postgres://u:p@h/d") == "postgresql+psycopg://u:p@h/d"


def test_explicit_driver_untouched():
    url = "postgresql+psycopg://u:p@h/d"
    assert normalize_database_url(url) == url
