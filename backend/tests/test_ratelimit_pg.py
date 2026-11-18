"""Rate limiting tests that need a dedicated PostgreSQL database (REQUIRE_PG=1 in CI)."""

import asyncio
import os
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from auth_fixtures import TEST_ORIGIN
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from research_agent import ratelimit
from research_agent.config import Settings
from research_agent.db import Database
from research_agent.main import create_app
from research_agent.maintenance import setup_checkpoints
from research_agent.repository import Repository
from research_agent.storage_models import RateLimitCounter, Run, Thread, now

pytestmark = pytest.mark.pg

CLIENT_A = "198.51.100.7"
CLIENT_B = "203.0.113.200"


@pytest.fixture(scope="module")
def pg_settings():
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated *_test PostgreSQL database")
    if not (make_url(raw).database or "").endswith("_test"):
        pytest.fail("Refusing destructive database tests: database name must end with _test")
    return Settings(
        app_env="test",
        database_url=SecretStr(raw),
        trust_proxy_headers=True,
        rate_limit_anon_session_per_hour=2,
        rate_limit_research_per_hour=1,
        rate_limit_login_per_hour=1,
        quota_anon_daily=1000,
        quota_user_daily=1000,
    )


@pytest.fixture(scope="module")
def migrated(pg_settings):
    os.environ["DATABASE_URL"] = pg_settings.database_url.get_secret_value()
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "src/research_agent/migrations")
    )
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        asyncio.run(setup_checkpoints(pg_settings))
    finally:
        os.environ.pop("DATABASE_URL", None)


@pytest.fixture
async def db(pg_settings, migrated):
    database = Database(pg_settings)
    async with database.open():
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE users, auth_sessions, oauth_states, principals, threads, runs, "
                    "rate_limit_counters, quota_usage, budget_reservations, budget_days, "
                    "checkpoints, checkpoint_blobs, "
                    "checkpoint_writes CASCADE"
                )
            )
        yield database


async def row_counts(db) -> tuple[int, int]:
    """Thread and run totals, used to prove that a refused request created nothing."""
    async with db.sessions() as session:
        threads = await session.scalar(select(func.count()).select_from(Thread))
        runs = await session.scalar(select(func.count()).select_from(Run))
    return threads or 0, runs or 0


async def test_consume_rate_limit_is_atomic_and_window_scoped(db, pg_settings):
    repo = Repository(db, pg_settings)
    start = ratelimit.window_start(now())
    results = await asyncio.gather(
        *(
            repo.consume_rate_limit(ratelimit.RESEARCH_SESSION, "k" * 64, start, 1000)
            for _ in range(20)
        )
    )
    assert all(allowed for allowed, _ in results)
    async with db.sessions() as session:
        stored = await session.scalar(
            select(RateLimitCounter.count).where(
                RateLimitCounter.scope == ratelimit.RESEARCH_SESSION
            )
        )
    assert stored == 20

    allowed, retry_after = await repo.consume_rate_limit("boundary", "b" * 64, start, 1)
    assert allowed and 0 < retry_after <= 3600
    allowed, retry_after = await repo.consume_rate_limit("boundary", "b" * 64, start, 1)
    assert not allowed and 0 < retry_after <= 3600

    later = start + timedelta(hours=1)
    allowed, _ = await repo.consume_rate_limit("boundary", "b" * 64, later, 1)
    assert allowed
    separate_scope, _ = await repo.consume_rate_limit("other_scope", "b" * 64, start, 1)
    assert separate_scope


async def test_cleanup_rate_limits_keeps_current_windows(db, pg_settings):
    repo = Repository(db, pg_settings)
    start = ratelimit.window_start(now())
    await repo.consume_rate_limit(ratelimit.RESEARCH_IP, "c" * 64, start - timedelta(hours=5), 5)
    await repo.consume_rate_limit(ratelimit.RESEARCH_IP, "c" * 64, start, 5)
    assert await repo.cleanup_rate_limits() == 1
    async with db.sessions() as session:
        remaining = (await session.scalars(select(RateLimitCounter.window_start))).all()
    assert remaining == [start]


async def test_anonymous_session_creation_is_limited_per_address(db, pg_settings):
    app = create_app(pg_settings)
    for _ in range(2):
        with TestClient(app, headers={"fly-client-ip": CLIENT_A}) as fresh:
            assert fresh.get("/api/v1/me").status_code == 200
    with TestClient(app, headers={"fly-client-ip": CLIENT_A}) as blocked:
        response = blocked.get("/api/v1/me")
    assert response.status_code == 429
    assert response.headers["content-type"] == "application/problem+json"
    assert 0 < int(response.headers["retry-after"]) <= 3600
    assert response.json()["code"] == "rate_limited"
    with TestClient(app, headers={"fly-client-ip": CLIENT_B}) as other:
        assert other.get("/api/v1/me").status_code == 200


async def test_research_admission_refuses_without_creating_rows(db, pg_settings):
    app = create_app(pg_settings)
    headers = {"origin": TEST_ORIGIN, "fly-client-ip": CLIENT_A}
    with TestClient(app, headers=headers) as client:
        csrf = client.get("/api/v1/me").json()["csrf_token"]
        first = client.post(
            "/api/v1/research", json={"query": "first question"}, headers={"x-csrf-token": csrf}
        )
        assert first.status_code == 200
        assert '"type": "done"' in first.text
        before = await row_counts(db)
        second = client.post(
            "/api/v1/research", json={"query": "second question"}, headers={"x-csrf-token": csrf}
        )
        assert second.status_code == 429
        assert second.json()["code"] == "rate_limited"
        assert second.headers["x-request-id"]
        assert await row_counts(db) == before


async def test_another_address_is_unaffected_by_a_limited_session(db, pg_settings):
    app = create_app(pg_settings)
    limited = {"origin": TEST_ORIGIN, "fly-client-ip": CLIENT_A}
    with TestClient(app, headers=limited) as client:
        csrf = client.get("/api/v1/me").json()["csrf_token"]
        first = client.post(
            "/api/v1/research", json={"query": "q1"}, headers={"x-csrf-token": csrf}
        )
        assert first.status_code == 200
    elsewhere = {"origin": TEST_ORIGIN, "fly-client-ip": CLIENT_B}
    with TestClient(app, headers=elsewhere) as client:
        csrf = client.get("/api/v1/me").json()["csrf_token"]
        second = client.post(
            "/api/v1/research", json={"query": "q2"}, headers={"x-csrf-token": csrf}
        )
        assert second.status_code == 200


async def test_github_login_start_is_limited_per_address(db, pg_settings):
    login_settings = pg_settings.model_copy(
        update={
            "github_client_id": "client-id",
            "github_client_secret": SecretStr("client-secret"),
        }
    )
    app = create_app(login_settings)
    with TestClient(app, headers={"fly-client-ip": CLIENT_A}) as client:
        assert client.get("/auth/github/login", follow_redirects=False).status_code == 302
        blocked = client.get("/auth/github/login", follow_redirects=False)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"
