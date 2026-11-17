import pytest
from auth_fixtures import origin_headers
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from research_agent.config import Settings
from research_agent.db import RedactedPoolMessages, selector_loop_factory
from research_agent.main import create_app


@pytest.mark.parametrize("url", ["sqlite:///file.db", "not-a-url", "postgresql://host/"])
def test_database_url_validation(url):
    with pytest.raises(ValidationError, match="DATABASE_URL must"):
        Settings(database_url=SecretStr(url))


def test_live_app_requires_database_and_production_stays_blocked():
    settings = Settings(
        demo_mode=False, openai_api_key=SecretStr("test"), tavily_api_key=SecretStr("test")
    )
    with pytest.raises(ValueError, match="requires DATABASE_URL"):
        create_app(settings)
    with pytest.raises(ValidationError, match="security launch gates"):
        Settings(app_env="production", database_url=SecretStr("postgresql://user:secret@host/db"))


def test_secret_database_url_is_redacted():
    settings = Settings(database_url=SecretStr("postgresql://user:SECRET@host/db"))
    assert "SECRET" not in repr(settings)
    with pytest.raises(ValidationError) as exc:
        Settings.model_validate({"database_url": "sqlite://user:SECRET@host/db"})
    assert "SECRET" not in str(exc.value)


@pytest.mark.parametrize("field", ["thread_retention_days", "anon_thread_ttl_hours"])
def test_retention_positive(field):
    with pytest.raises(ValidationError, match="Retention"):
        Settings.model_validate({field: 0})


def test_server_loop_factory_returns_instance():
    loop = selector_loop_factory()
    try:
        assert not loop.is_running()
    finally:
        loop.close()


def test_pool_logs_discard_exception_details():
    import logging

    record = logging.LogRecord(
        "psycopg.pool", logging.ERROR, "", 1, "failed %s", ("SECRET-DSN",), None
    )
    assert RedactedPoolMessages().filter(record)
    assert "SECRET" not in record.getMessage()


def test_no_database_apis_fail_closed_and_owner_is_not_client_controlled():
    with TestClient(create_app(Settings()), headers=origin_headers()) as client:
        assert client.get("/api/v1/threads").status_code == 503
        assert (
            client.post(
                "/api/v1/research",
                json={
                    "query": "q",
                    "thread_id": "00000000-0000-4000-8000-000000000001",
                },
            ).status_code
            == 503
        )
        assert (
            client.post(
                "/api/v1/research",
                json={
                    "query": "q",
                    "owner_id": "attacker",
                },
            ).status_code
            == 422
        )
