"""SPA serving (task 4.12): fallback to index.html for router paths, 404 for
missing hashed assets and unknown API paths, font MIME types."""

from .conftest import ADMIN_EMAIL, EMAIL_HEADER, UNTRUSTED_PEER, make_client


def _build(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><html><body><div id=root>spa</div></body></html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log('ok')")
    (tmp_path / "assets" / "geist-latin.woff2").write_bytes(b"wOF2" + b"\0" * 16)
    (tmp_path / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    return str(tmp_path)


def test_router_paths_fall_back_to_index(pg_url, tmp_path):
    with make_client(pg_url, email=None, peer=UNTRUSTED_PEER, static_dir=_build(tmp_path)) as c:
        for path in ("/", "/overview", "/engineering/sessions", "/engineering/sessions/abc?from=2026-01-01",
                     "/settings/users", "/proposals/123"):
            r = c.get(path)
            assert r.status_code == 200, path
            assert r.headers["content-type"].startswith("text/html")
            assert r.headers["cache-control"] == "no-cache"
            assert "spa" in r.text


def test_static_files_and_mime(pg_url, tmp_path):
    with make_client(pg_url, email=None, peer=UNTRUSTED_PEER, static_dir=_build(tmp_path)) as c:
        js = c.get("/assets/index-abc123.js")
        assert js.status_code == 200 and "javascript" in js.headers["content-type"]
        font = c.get("/assets/geist-latin.woff2")
        assert font.status_code == 200 and font.headers["content-type"] == "font/woff2"
        ico = c.get("/favicon.svg")
        assert ico.status_code == 200 and ico.headers["content-type"].startswith("image/svg+xml")


def test_missing_asset_is_404_not_index(pg_url, tmp_path):
    with make_client(pg_url, email=None, peer=UNTRUSTED_PEER, static_dir=_build(tmp_path)) as c:
        r = c.get("/assets/missing.js")
        assert r.status_code == 404
        assert "spa" not in r.text


def test_fallback_never_shadows_api_or_health(pg_url, tmp_path):
    static = _build(tmp_path)
    with make_client(pg_url, static_dir=static) as c:
        assert c.get("/healthz").json() == {"status": "alive"}
        ready = c.get("/readyz")
        assert ready.status_code == 200 and ready.json()["status"] == "ready"
        me = c.get("/v1/me", headers={EMAIL_HEADER: ADMIN_EMAIL})
        assert me.status_code == 200 and me.json()["role"] == "admin"
        unknown = c.get("/v1/does-not-exist", headers={EMAIL_HEADER: ADMIN_EMAIL})
        assert unknown.status_code == 404
        assert "spa" not in unknown.text
        # Phase 5 routes are served by the API, never shadowed by the SPA
        proposals = c.get("/v1/proposals", headers={EMAIL_HEADER: ADMIN_EMAIL})
        assert proposals.status_code == 200 and proposals.json()["proposals"] == []
        assert c.get("/internal/nothing", headers={EMAIL_HEADER: ADMIN_EMAIL}).status_code in (404, 405)
    with make_client(pg_url, email=None, peer=UNTRUSTED_PEER, static_dir=static) as c:
        # still gated by the auth middleware, not served as HTML
        assert c.get("/v1/me").status_code == 401
        assert c.get("/v1/does-not-exist").status_code == 401


def test_path_traversal_is_rejected(pg_url, tmp_path):
    static = _build(tmp_path)
    (tmp_path.parent / "secret.txt").write_text("nope")
    with make_client(pg_url, email=None, peer=UNTRUSTED_PEER, static_dir=static) as c:
        r = c.get("/../secret.txt")
        assert "nope" not in r.text
        r = c.get("/%2e%2e/secret.txt")
        assert "nope" not in r.text
