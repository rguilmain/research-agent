"""Same-origin static serving, catch-all safety and browser security headers."""

from fastapi.testclient import TestClient
from pydantic import ValidationError

from research_agent.config import Settings
from research_agent.main import create_app
from research_agent.web import CSP


def build_static(root, index='<div id="root"></div>'):
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(index, encoding="utf-8")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (root / "assets" / "app-abc123.js").write_text("export default 1", encoding="utf-8")
    return root


def client_for(tmp_path, **overrides):
    build_static(tmp_path)
    return TestClient(create_app(Settings(app_env="test", static_dir=str(tmp_path), **overrides)))


def test_index_and_spa_fallback(tmp_path):
    with client_for(tmp_path) as client:
        for path in ("/", "/threads/some-id"):
            response = client.get(path)
            assert response.status_code == 200
            assert 'id="root"' in response.text
            assert response.headers["Content-Security-Policy"] == CSP
            assert response.headers["Permissions-Policy"].startswith("camera=()")
            assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
            assert response.headers["Cache-Control"] == "no-store"
            assert "challenges.cloudflare.com" in CSP


def test_hashed_assets_are_immutable_and_top_level_files_are_served(tmp_path):
    with client_for(tmp_path) as client:
        asset = client.get("/assets/app-abc123.js")
        assert asset.status_code == 200
        assert asset.headers["Cache-Control"] == "public, max-age=31536000, immutable"
        favicon = client.get("/favicon.svg")
        assert favicon.status_code == 200
        assert favicon.headers["Cache-Control"] == "no-store"


def test_unknown_paths_never_fall_back_to_the_shell(tmp_path):
    with client_for(tmp_path) as client:
        for path in ("/api/v1/nope", "/auth/nope", "/assets/missing.js", "/docs", "/openapi.json"):
            response = client.get(path)
            assert response.status_code == 404, path
            assert 'id="root"' not in response.text, path


def test_path_traversal_cannot_escape_the_static_directory(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("NEVER-EXPOSE", encoding="utf-8")
    static = tmp_path / "static"
    with client_for(static) as client:
        for path in ("/%2e%2e/secret.txt", "/..%2fsecret.txt", "/../secret.txt"):
            # Some clients normalize or collapse these before routing, so the assertion is
            # that the outside file is never served, not that the status is always 404.
            response = client.get(path)
            assert "NEVER-EXPOSE" not in response.text, path
        missing = client.get("/secret.txt")
        assert 'id="root"' in missing.text and "NEVER-EXPOSE" not in missing.text


def test_api_routes_still_work_and_keep_headers(tmp_path):
    with client_for(tmp_path) as client:
        health = client.get("/healthz")
        assert health.status_code == 200 and health.json()["status"] == "ok"
        assert health.headers["Content-Security-Policy"] == CSP
        assert health.headers["Cache-Control"] == "no-store"
        assert client.get("/api/v1/models").status_code in (200, 503)


def test_hsts_only_with_an_https_public_url(tmp_path):
    with client_for(tmp_path, public_base_url="https://example.org") as client:
        assert client.get("/healthz").headers["Strict-Transport-Security"]
    with client_for(tmp_path) as client:
        assert "Strict-Transport-Security" not in client.get("/healthz").headers


def test_api_only_process_keeps_docs_and_has_no_catch_all():
    with TestClient(create_app(Settings(app_env="test"))) as client:
        assert client.get("/docs").status_code == 200
        missing = client.get("/definitely-not-a-route")
        assert missing.status_code == 404


def test_static_dir_must_contain_index_html(tmp_path):
    (tmp_path / "assets").mkdir()
    try:
        Settings(app_env="test", static_dir=str(tmp_path))
    except ValidationError as error:
        assert "STATIC_DIR" in str(error)
    else:
        raise AssertionError("A STATIC_DIR without index.html must be rejected")
