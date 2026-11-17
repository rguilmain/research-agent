import json

import pytest
from auth_fixtures import origin_headers
from fastapi.testclient import TestClient
from pydantic import ValidationError

from research_agent.config import Settings
from research_agent.main import create_app


@pytest.fixture
def client():
    # Unsafe routes require a same-origin proof even in demo mode without a database.
    with TestClient(create_app(Settings(app_env="test")), headers=origin_headers()) as client:
        yield client


def test_health_and_default_model(client):
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/readyz").json()["mode"] == "demo"
    assert client.get("/api/v1/models").json()["default"] == "gpt-6-luna"
    assert client.get("/api/v1/config").json()["login_enabled"] is False


@pytest.mark.parametrize("query", ["", "   ", "x" * 2001])
def test_invalid_query(client, query):
    assert client.post("/api/v1/research", json={"query": query}).status_code == 422


@pytest.mark.parametrize("model", ["untrusted-model", "gpt-6.1-sol"])
def test_model_allowlist(client, model):
    response = client.post("/api/v1/research", json={"query": "hello", "model_id": model})
    assert response.status_code == 403
    assert response.json()["code"] == "model_not_allowed"


def test_demo_stream(client):
    response = client.post("/api/v1/research", json={"query": "test question"})
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data:")
    ]
    assert events[0]["type"] == "run_started"
    assert events[0]["demo"] is True
    assert events[-1]["type"] == "done"
    assert "Demo mode" in "".join(event.get("text", "") for event in events)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-request-id"]


def test_production_fails_closed():
    with pytest.raises(ValidationError, match="security launch gates"):
        Settings(app_env="production")


def test_missing_keys_and_bad_default():
    with pytest.raises(ValidationError, match="requires"):
        Settings(demo_mode=False)
    with pytest.raises(ValueError, match="enabled catalog"):
        create_app(Settings(default_model="not-allowed"))


def test_stream_error_is_redacted():
    class BrokenResearch:
        async def events(self, query, model_id):
            yield {"type": "run_started", "model_id": model_id, "demo": False}
            raise RuntimeError("https://provider.test/?apikey=NEVER-EXPOSE")

    with TestClient(
        create_app(Settings(app_env="test"), BrokenResearch()), headers=origin_headers()
    ) as client:
        response = client.post("/api/v1/research", json={"query": "test"})
        assert '"type": "error"' in response.text
        assert "NEVER-EXPOSE" not in response.text


def test_csrf_origin_is_required_for_unsafe_routes(client):
    blocked = client.post(
        "/api/v1/research", json={"query": "q"}, headers={"origin": "http://evil.example"}
    )
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "csrf_failed"
    assert blocked.headers["content-type"] == "application/problem+json"


def test_me_reports_anonymous_identity_without_database(client):
    response = client.get("/api/v1/me")
    assert response.status_code == 200
    assert response.json() == {
        "kind": "none",
        "user": None,
        "csrf_token": None,
        "login_enabled": False,
        "turnstile_required": False,
        "human_verified_until": None,
    }
