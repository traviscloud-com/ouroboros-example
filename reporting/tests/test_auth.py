"""Reporting auth (design D12, task 3.1): trusted-peer + header identity,
roles from principals with ADMIN_EMAILS bootstrap, 401/403 semantics,
/v1/me, principal management, internal token."""

from sqlalchemy import text

from app.auth import parse_cidrs, peer_is_trusted

from .conftest import (
    ADMIN_EMAIL, EMAIL_HEADER, TRUSTED_PEER, UNTRUSTED_PEER, grant, make_client, make_test_settings,
)


def test_cidr_matching():
    nets = parse_cidrs(["127.0.0.1/32", "10.42.0.0/16", "fd00::/8"])
    assert peer_is_trusted("127.0.0.1", nets)
    assert peer_is_trusted("10.42.3.9", nets)
    assert peer_is_trusted("::ffff:10.42.3.9", nets)  # IPv4-mapped IPv6 peer
    assert not peer_is_trusted("10.43.0.1", nets)
    assert not peer_is_trusted("testclient", nets)
    assert not peer_is_trusted(None, nets)
    assert not peer_is_trusted("127.0.0.1", [])


def test_health_and_static_are_public(pg_url, engine, tmp_path):
    (tmp_path / "index.html").write_text("<html><body>spa</body></html>")
    with make_client(pg_url, email=None, peer=UNTRUSTED_PEER, static_dir=str(tmp_path)) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/readyz").status_code == 200
        assert c.get("/").status_code == 200 and "spa" in c.get("/").text
        assert c.get("/v1/me").status_code == 401


def test_untrusted_peer_is_401_even_with_header(pg_url, engine):
    with make_client(pg_url, peer=UNTRUSTED_PEER) as c:
        resp = c.get("/v1/reports/overview")
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == "X-Auth-Request-Email"
        assert c.get("/v1/me").status_code == 401
        assert c.get("/v1/analytics/adoption").status_code == 401
        assert c.post("/internal/refresh").status_code == 401


def test_missing_header_from_trusted_peer_is_401(pg_url, engine):
    with make_client(pg_url, email=None) as c:
        assert c.get("/v1/me").status_code == 401
        assert c.get("/v1/reports/events").status_code == 401


def test_empty_trusted_cidrs_denies_everything(pg_url, engine):
    with make_client(pg_url, trusted_proxy_cidrs=[]) as c:
        assert c.get("/v1/me").status_code == 401
        assert c.get("/healthz").status_code == 200


def test_unknown_email_is_403(pg_url, engine):
    with make_client(pg_url, email="stranger@example.com") as c:
        resp = c.get("/v1/me")
        assert resp.status_code == 403
        assert c.get("/v1/reports/overview").status_code == 403


def test_me_for_bootstrap_admin_and_landing_by_role(pg_url, engine):
    with make_client(pg_url, email=ADMIN_EMAIL.upper()) as c:  # header case-insensitive
        body = c.get("/v1/me").json()
        assert body == {"email": ADMIN_EMAIL, "role": "admin", "landing": "/engineering", "via": "bootstrap",
                        "features": {"analyst": "disabled", "pr_creation": "not_configured"}}
    grant(engine, "lead@example.com", "leadership")
    grant(engine, "eng@example.com", "engineer")
    with make_client(pg_url, email="lead@example.com") as c:
        body = c.get("/v1/me").json()
        assert (body["role"], body["landing"], body["via"]) == ("leadership", "/overview", "gateway")
    with make_client(pg_url, email="eng@example.com") as c:
        assert c.get("/v1/me").json()["landing"] == "/engineering"


def test_bootstrap_admins_upserted_at_startup(pg_url, engine):
    with make_client(pg_url, admin_emails=[ADMIN_EMAIL, "Second@Example.com"]) as c:
        assert c.get("/v1/me").status_code == 200
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT email, role, created_by FROM principals ORDER BY email")
        ).all()
    assert rows == [(ADMIN_EMAIL, "admin", "bootstrap"), ("second@example.com", "admin", "bootstrap")]


def test_admin_manages_principals_with_audit(pg_url, engine, client):
    assert client.get("/v1/settings/users").json()["users"] == [
        {"email": ADMIN_EMAIL, "role": "admin", "created_at": client.get("/v1/settings/users").json()["users"][0]["created_at"], "created_by": "bootstrap"}
    ]
    # the new engineer cannot get in yet
    with make_client(pg_url, email="dev@example.com") as dev:
        assert dev.get("/v1/me").status_code == 403

    resp = client.post("/v1/settings/users", json={"email": "Dev@Example.com", "role": "engineer"})
    assert resp.status_code == 201
    assert resp.json()["email"] == "dev@example.com" and resp.json()["created_by"] == ADMIN_EMAIL

    with make_client(pg_url, email="dev@example.com") as dev:
        assert dev.get("/v1/me").json()["role"] == "engineer"
        assert dev.get("/v1/settings/users").status_code == 403  # engineer is not admin
        assert dev.post("/v1/settings/users", json={"email": "x@example.com", "role": "admin"}).status_code == 403

    # role change via the spec alias
    assert client.post("/v1/principals", json={"email": "dev@example.com", "role": "leadership"}).status_code == 201
    with make_client(pg_url, email="dev@example.com") as dev:
        assert dev.get("/v1/me").json()["role"] == "leadership"

    assert client.delete("/v1/settings/users/dev@example.com").status_code == 204
    assert client.delete("/v1/settings/users/dev@example.com").status_code == 404
    assert client.delete(f"/v1/settings/users/{ADMIN_EMAIL}").status_code == 409
    assert client.post("/v1/settings/users", json={"email": ADMIN_EMAIL, "role": "leadership"}).status_code == 409
    assert client.post("/v1/settings/users", json={"email": "not-an-email", "role": "engineer"}).status_code == 422
    assert client.post("/v1/settings/users", json={"email": "a@example.com", "role": "root"}).status_code == 422

    with engine.connect() as conn:
        audit = conn.execute(
            text("SELECT actor_email, action, target_id, details FROM audit_log ORDER BY id")
        ).all()
    assert [(a.action, a.target_id) for a in audit] == [
        ("principal.upsert", "dev@example.com"),
        ("principal.upsert", "dev@example.com"),
        ("principal.delete", "dev@example.com"),
    ]
    assert audit[0].actor_email == ADMIN_EMAIL
    assert audit[1].details == {"role": "leadership", "previous_role": "engineer"}
    assert audit[2].details == {"previous_role": "leadership"}


def test_forwarded_for_ignored_unless_enabled(pg_url, engine):
    xff = {"X-Forwarded-For": "203.0.113.9, 127.0.0.1"}
    with make_client(pg_url, peer=UNTRUSTED_PEER) as c:
        assert c.get("/v1/me", headers=xff).status_code == 401
    with make_client(pg_url, peer=UNTRUSTED_PEER, trust_forwarded_for=True) as c:
        assert c.get("/v1/me", headers=xff).status_code == 200
        # right-most hop is what counts: a forged left-most entry does not help
        assert c.get("/v1/me", headers={"X-Forwarded-For": "127.0.0.1, 203.0.113.9"}).status_code == 401


def test_internal_refresh_token_and_roles(pg_url, engine):
    settings = make_test_settings(pg_url, internal_refresh_token="s3cret-token")
    with make_client(pg_url, email=None, peer=UNTRUSTED_PEER, settings=settings) as c:
        assert c.post("/internal/refresh").status_code == 401
        assert c.post("/internal/refresh", headers={"X-Internal-Token": "wrong"}).status_code == 401
        resp = c.post("/internal/refresh", headers={"X-Internal-Token": "s3cret-token"})
        assert resp.status_code == 200 and resp.json()["trigger"] == "manual"
        resp = c.post("/internal/refresh", headers={"Authorization": "Bearer s3cret-token"})
        assert resp.status_code == 200
        # the token is only good for /internal/*
        assert c.get("/v1/me", headers={"X-Internal-Token": "s3cret-token"}).status_code == 401
    grant(engine, "lead@example.com", "leadership")
    with make_client(pg_url, email="lead@example.com") as lead:
        assert lead.post("/internal/refresh").status_code == 403
    with make_client(pg_url) as admin:
        assert admin.post("/internal/refresh").status_code == 200
    with engine.connect() as conn:
        actors = conn.execute(
            text("SELECT actor_email FROM audit_log WHERE action = 'refresh.manual' ORDER BY id")
        ).scalars().all()
    assert actors == ["internal-token", "internal-token", ADMIN_EMAIL]


def test_read_only_surface_stays(client):
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/v1/reports/events").status_code in (404, 405)
