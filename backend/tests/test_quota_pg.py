"""Daily quota tests that need a dedicated PostgreSQL database (REQUIRE_PG=1 in CI)."""

import asyncio
import os
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from auth_fixtures import TEST_ORIGIN, signed_in_client
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from research_agent.config import Settings
from research_agent.db import Database
from research_agent.main import create_app
from research_agent.maintenance import setup_checkpoints
from research_agent.repository import Repository
from research_agent.storage_models import QuotaUsage, Run, Thread, now

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
        rate_limit_anon_session_per_hour=100,
        rate_limit_research_per_hour=100,
        rate_limit_login_per_hour=100,
        quota_anon_daily=2,
        quota_user_daily=5,
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


def truncate_all(settings: Settings) -> None:
    """Drain shared tables so tests that only use ``migrated`` still start clean."""

    async def run() -> None:
        database = Database(settings)
        async with database.open():
            async with database.engine.begin() as connection:
                await connection.execute(
                    text(
                        "TRUNCATE users, auth_sessions, oauth_states, principals, threads, runs, "
                        "rate_limit_counters, quota_usage, checkpoints, checkpoint_blobs, "
                        "checkpoint_writes CASCADE"
                    )
                )

    asyncio.run(run())


@pytest.fixture(autouse=True)
def clean_database(pg_settings, request):
    """Quota and session rows are shared state; start every test from an empty database."""
    if "db" not in request.fixturenames:
        truncate_all(pg_settings)


@pytest.fixture
async def db(pg_settings, migrated):
    database = Database(pg_settings)
    async with database.open():
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE users, auth_sessions, oauth_states, principals, threads, runs, "
                    "rate_limit_counters, quota_usage, checkpoints, checkpoint_blobs, "
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


async def stored_units(db, scope: str, key: str) -> int | None:
    async with db.sessions() as session:
        return await session.scalar(
            select(QuotaUsage.units).where(QuotaUsage.scope == scope, QuotaUsage.key_hash == key)
        )


async def test_consume_quota_is_atomic_and_never_over_admits(db, pg_settings):
    repo = Repository(db, pg_settings)
    day = now().date()
    results = await asyncio.gather(
        *(repo.consume_quota(day, [("quota_principal", "k" * 64)], 1, 5) for _ in range(12))
    )
    assert len([value for value in results if value is not None]) == 5
    assert await stored_units(db, "quota_principal", "k" * 64) == 5


async def test_a_full_key_rolls_back_every_other_key(db, pg_settings):
    repo = Repository(db, pg_settings)
    day = now().date()
    assert await repo.consume_quota(day, [("quota_ip", "i" * 64)], 2, 2) == 0
    keys = [("quota_principal", "p" * 64), ("quota_ip", "i" * 64)]
    assert await repo.consume_quota(day, keys, 1, 2) is None
    assert await stored_units(db, "quota_ip", "i" * 64) == 2
    assert await stored_units(db, "quota_principal", "p" * 64) is None


async def test_a_question_heavier_than_the_limit_is_refused(db, pg_settings):
    repo = Repository(db, pg_settings)
    day = now().date()
    assert await repo.consume_quota(day, [("quota_principal", "h" * 64)], 5, 2) is None
    assert await stored_units(db, "quota_principal", "h" * 64) is None


async def test_refund_reduces_usage_and_never_goes_negative(db, pg_settings):
    repo = Repository(db, pg_settings)
    day = now().date()
    keys = [("quota_principal", "r" * 64)]
    assert await repo.consume_quota(day, keys, 2, 5) == 3
    await repo.refund_quota(day, keys, 2)
    assert await stored_units(db, "quota_principal", "r" * 64) == 0
    await repo.refund_quota(day, keys, 2)
    assert await stored_units(db, "quota_principal", "r" * 64) == 0


async def test_cleanup_drops_old_days_only(db, pg_settings):
    repo = Repository(db, pg_settings)
    old = now().date() - timedelta(days=3)
    today = now().date()
    assert await repo.consume_quota(old, [("quota_ip", "c" * 64)], 1, 5) == 4
    assert await repo.consume_quota(today, [("quota_ip", "c" * 64)], 1, 5) == 4
    assert await repo.cleanup_quotas() == 1
    async with db.sessions() as session:
        remaining = (await session.scalars(select(QuotaUsage.day))).all()
    assert remaining == [today]


def test_anonymous_quota_reports_remaining_and_refuses_the_third_question(pg_settings, migrated):
    app = create_app(pg_settings)
    headers = {"origin": TEST_ORIGIN, "fly-client-ip": CLIENT_A}
    with TestClient(app, headers=headers) as client:
        csrf = client.get("/api/v1/me").json()["csrf_token"]
        first = client.post(
            "/api/v1/research", json={"query": "first"}, headers={"x-csrf-token": csrf}
        )
        assert first.status_code == 200
        assert '"type": "quota"' in first.text
        assert '"remaining": 1' in first.text
        second = client.post(
            "/api/v1/research", json={"query": "second"}, headers={"x-csrf-token": csrf}
        )
        assert '"remaining": 0' in second.text
        refused = client.post(
            "/api/v1/research", json={"query": "third"}, headers={"x-csrf-token": csrf}
        )
    assert refused.status_code == 429
    assert refused.headers["content-type"] == "application/problem+json"
    assert refused.json()["code"] == "quota_exceeded"
    assert 0 < int(refused.headers["retry-after"]) <= 86_400


def test_a_new_cookie_from_a_limited_address_is_still_refused(pg_settings, migrated):
    app = create_app(pg_settings)
    used = {"origin": TEST_ORIGIN, "fly-client-ip": CLIENT_A}
    with TestClient(app, headers=used) as first:
        csrf = first.get("/api/v1/me").json()["csrf_token"]
        assert (
            first.post(
                "/api/v1/research", json={"query": "a"}, headers={"x-csrf-token": csrf}
            ).status_code
            == 200
        )
        assert (
            first.post(
                "/api/v1/research", json={"query": "b"}, headers={"x-csrf-token": csrf}
            ).status_code
            == 200
        )
    # A fresh client has a new session cookie but the same address.
    with TestClient(app, headers=used) as second:
        csrf = second.get("/api/v1/me").json()["csrf_token"]
        assert (
            second.post(
                "/api/v1/research", json={"query": "c"}, headers={"x-csrf-token": csrf}
            ).status_code
            == 429
        )
    elsewhere = {"origin": TEST_ORIGIN, "fly-client-ip": CLIENT_B}
    with TestClient(app, headers=elsewhere) as other:
        csrf = other.get("/api/v1/me").json()["csrf_token"]
        assert (
            other.post(
                "/api/v1/research", json={"query": "d"}, headers={"x-csrf-token": csrf}
            ).status_code
            == 200
        )


async def test_failed_admission_refunds_the_question(db, pg_settings):
    app = create_app(pg_settings)
    with signed_in_client(app) as client:
        client.headers["fly-client-ip"] = CLIENT_A
        before = await row_counts(db)
        response = client.post(
            "/api/v1/research",
            json={"query": "ghost", "thread_id": "00000000-0000-4000-8000-0000000000ff"},
        )
        assert response.status_code == 404
        assert await row_counts(db) == before
    async with db.sessions() as session:
        total = await session.scalar(select(func.coalesce(func.sum(QuotaUsage.units), 0)))
    assert total == 0
