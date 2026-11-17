"""Turnstile enforcement tests that need a dedicated PostgreSQL database (REQUIRE_PG=1)."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import respx
from alembic import command
from alembic.config import Config
from auth_fixtures import TEST_ORIGIN
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from research_agent import ratelimit, turnstile
from research_agent.auth import AuthContext, require_csrf, session_or_create
from research_agent.config import Settings
from research_agent.db import Database
from research_agent.main import create_app
from research_agent.maintenance import setup_checkpoints
from research_agent.repository import Identity, Repository
from research_agent.storage_models import AuthSession, QuotaUsage, RateLimitCounter, Run, Thread

pytestmark = pytest.mark.pg

CLIENT_A = "198.51.100.7"
# Second address so verification state from other tests cannot affect this one.
CLIENT_C = "192.0.2.44"
TOKEN = "XXXX.DUMMY.TOKEN.XXXX"


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
        rate_limit_turnstile_per_hour=100,
        quota_anon_daily=100,
        quota_user_daily=100,
        turnstile_site_key="1x00000000000000000000AA",
        turnstile_secret_key=SecretStr("1x0000000000000000000000000000000AA"),
    )


@pytest.fixture(scope="module")
def migrated(pg_settings):
    """Bring the schema to head without downgrading: other suites share this database."""
    os.environ["DATABASE_URL"] = pg_settings.database_url.get_secret_value()
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "src/research_agent/migrations")
    )
    try:
        command.upgrade(config, "head")
        asyncio.run(setup_checkpoints(pg_settings))
    finally:
        os.environ.pop("DATABASE_URL", None)


TRUNCATE = (
    "TRUNCATE users, auth_sessions, oauth_states, principals, threads, runs, "
    "rate_limit_counters, quota_usage, checkpoints, checkpoint_blobs, "
    "checkpoint_writes CASCADE"
)


def truncate_all(settings: Settings) -> None:
    """Drain shared tables so tests that only use ``migrated`` still start clean."""

    async def run() -> None:
        database = Database(settings)
        async with database.open():
            async with database.engine.begin() as connection:
                await connection.execute(text(TRUNCATE))

    asyncio.run(run())


@pytest.fixture(autouse=True)
def clean_database(pg_settings, request):
    if "db" not in request.fixturenames:
        truncate_all(pg_settings)


@pytest.fixture
async def db(pg_settings, migrated):
    database = Database(pg_settings)
    async with database.open():
        async with database.engine.begin() as connection:
            await connection.execute(text(TRUNCATE))
        yield database


def approve() -> respx.Route:
    return respx.post(turnstile.SITEVERIFY_URL).mock(
        return_value=httpx.Response(
            200, json={"success": True, "hostname": "example.com", "error-codes": []}
        )
    )


def reject() -> respx.Route:
    return respx.post(turnstile.SITEVERIFY_URL).mock(
        return_value=httpx.Response(
            200, json={"success": False, "error-codes": ["invalid-input-response"]}
        )
    )


def client_for(app, ip: str = CLIENT_A) -> TestClient:
    return TestClient(app, headers={"origin": TEST_ORIGIN, "fly-client-ip": ip})


def csrf_of(client: TestClient) -> str:
    token = client.get("/api/v1/me").json()["csrf_token"]
    assert token
    return token


def post_research(client: TestClient, csrf: str):
    return client.post("/api/v1/research", json={"query": "q"}, headers={"x-csrf-token": csrf})


def post_verify(client: TestClient, csrf: str, token: str = TOKEN):
    return client.post(
        "/api/v1/turnstile/verify", json={"token": token}, headers={"x-csrf-token": csrf}
    )


async def counters(db) -> tuple[int, int, int]:
    """Threads, runs and quota units, used to prove a refusal consumed nothing."""
    async with db.sessions() as session:
        threads = await session.scalar(select(func.count()).select_from(Thread))
        runs = await session.scalar(select(func.count()).select_from(Run))
        units = await session.scalar(select(func.coalesce(func.sum(QuotaUsage.units), 0)))
    return threads or 0, runs or 0, units or 0


async def test_migration_records_human_verification_column(db):
    """The column is assertable because the fixtures migrate this database to head."""
    async with db.sessions() as session:
        revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        column = await session.scalar(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'auth_sessions' AND column_name = 'human_verified_until'"
            )
        )
    assert revision == "0005"
    assert column == "human_verified_until"


def test_identity_requires_verification_and_reports_the_site_key(pg_settings, migrated):
    app = create_app(pg_settings)
    with client_for(app) as client:
        body = client.get("/api/v1/me").json()
        assert body["turnstile_required"] is True
        assert body["human_verified_until"] is None
        assert client.get("/api/v1/config").json()["turnstile_site_key"] == (
            pg_settings.turnstile_site_key
        )


async def test_unverified_anonymous_is_refused_before_any_state(db, pg_settings):
    app = create_app(pg_settings)
    with client_for(app) as client:
        csrf = csrf_of(client)
        response = post_research(client, csrf)
        assert response.status_code == 403
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["code"] == "turnstile_required"
        assert response.headers["x-request-id"]
    assert await counters(db) == (0, 0, 0)


def test_verified_anonymous_can_research(pg_settings, migrated):
    app = create_app(pg_settings)
    with respx.mock:
        approve()
        with client_for(app) as client:
            csrf = csrf_of(client)
            verified = post_verify(client, csrf)
            assert verified.status_code == 200
            until = datetime.fromisoformat(verified.json()["human_verified_until"])
            assert until > datetime.now(UTC)
            body = client.get("/api/v1/me").json()
            assert body["turnstile_required"] is False
            assert body["human_verified_until"]
            streamed = post_research(client, csrf)
    assert streamed.status_code == 200
    assert '"type": "done"' in streamed.text


def test_refused_token_leaves_the_session_unverified(pg_settings, migrated):
    app = create_app(pg_settings)
    with respx.mock:
        reject()
        with client_for(app) as client:
            csrf = csrf_of(client)
            assert post_verify(client, csrf).status_code == 403
            assert post_verify(client, csrf).json()["code"] == "turnstile_failed"
            assert post_research(client, csrf).status_code == 403


def test_unreachable_provider_fails_closed(pg_settings, migrated):
    app = create_app(pg_settings)
    with respx.mock:
        respx.post(turnstile.SITEVERIFY_URL).mock(side_effect=httpx.ConnectError("boom"))
        with client_for(app) as client:
            csrf = csrf_of(client)
            response = post_verify(client, csrf)
            assert response.status_code == 503
            assert response.json()["code"] == "turnstile_unavailable"
            assert post_research(client, csrf).status_code == 403


async def test_an_expired_verification_requires_a_new_check(db, pg_settings):
    app = create_app(pg_settings)
    with respx.mock:
        approve()
        with client_for(app) as client:
            csrf = csrf_of(client)
            assert post_verify(client, csrf).status_code == 200
            assert post_research(client, csrf).status_code == 200
            async with db.sessions.begin() as session:
                await session.execute(
                    text(
                        "UPDATE auth_sessions SET human_verified_until = "
                        "now() - interval '1 minute'"
                    )
                )
            assert post_research(client, csrf).status_code == 403


def test_verify_requires_the_csrf_token(pg_settings, migrated):
    app = create_app(pg_settings)
    with respx.mock:
        route = approve()
        with client_for(app) as client:
            csrf_of(client)
            blocked = client.post("/api/v1/turnstile/verify", json={"token": TOKEN})
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "csrf_failed"
    assert not route.called


def test_a_new_cookie_must_verify_again(pg_settings, migrated):
    app = create_app(pg_settings)
    with respx.mock:
        approve()
        with client_for(app, CLIENT_C) as first:
            csrf = csrf_of(first)
            assert post_verify(first, csrf).status_code == 200
            assert post_research(first, csrf).status_code == 200
        # A fresh client has a new session cookie, so it needs its own check.
        with client_for(app, CLIENT_C) as second:
            assert second.get("/api/v1/me").json()["turnstile_required"] is True
            assert post_research(second, csrf_of(second)).status_code == 403


def test_signed_in_callers_are_never_challenged(pg_settings, migrated):
    app = create_app(pg_settings)
    owner = Identity(uuid4(), "user")
    signed_in = AuthContext(identity=owner, kind="user")
    app.dependency_overrides[require_csrf] = lambda: signed_in
    app.dependency_overrides[session_or_create] = lambda: signed_in
    with respx.mock:
        route = approve()
        with client_for(app) as client:
            verified = client.post("/api/v1/turnstile/verify", json={"token": TOKEN})
            assert verified.status_code == 200
            assert verified.json()["human_verified_until"] is None
            assert client.get("/api/v1/me").json()["turnstile_required"] is False
            assert post_research(client, "unused").status_code == 200
    assert not route.called
    app.dependency_overrides.clear()


def test_verification_is_disabled_without_keys_and_then_not_required(pg_settings, migrated):
    config = pg_settings.model_copy(
        update={
            "turnstile_site_key": "",
            "turnstile_secret_key": SecretStr(""),
        }
    )
    app = create_app(config)
    with client_for(app) as client:
        csrf = csrf_of(client)
        assert client.get("/api/v1/me").json()["turnstile_required"] is False
        assert post_verify(client, csrf).status_code == 404
        assert post_research(client, csrf).status_code == 200


async def test_verified_window_never_outlives_the_session(db, pg_settings):
    async with db.sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO principals (id, kind, created_at) VALUES "
                "('00000000-0000-4000-8000-0000000000aa', 'anonymous', now())"
            )
        )
        await session.execute(text("DELETE FROM auth_sessions"))
        await session.execute(
            text(
                "INSERT INTO auth_sessions (id, token_hash, principal_id, kind, expires_at, "
                "last_seen_at, created_at) VALUES "
                "('00000000-0000-4000-8000-0000000000bb', :token_hash, "
                "'00000000-0000-4000-8000-0000000000aa', 'anonymous', "
                "now() + interval '10 minutes', now(), now())"
            ),
            {"token_hash": "a" * 64},
        )
    store = Repository(db, pg_settings)
    assert await store.mark_human_verified("a" * 64, datetime.now(UTC) + timedelta(minutes=60))
    async with db.sessions() as session:
        until = await session.scalar(
            select(AuthSession.human_verified_until).where(AuthSession.token_hash == "a" * 64)
        )
        expires = await session.scalar(
            select(AuthSession.expires_at).where(AuthSession.token_hash == "a" * 64)
        )
    assert until is not None and expires is not None and until <= expires


async def test_marking_a_missing_session_is_refused(db, pg_settings):
    assert not await Repository(db, pg_settings).mark_human_verified(
        "b" * 64, datetime.now(UTC) + timedelta(minutes=60)
    )
    async with db.sessions() as session:
        count = await session.scalar(select(func.count()).select_from(AuthSession))
    assert count == 0


async def test_turnstile_attempts_are_rate_limited_per_address(db, pg_settings):
    app = create_app(pg_settings.model_copy(update={"rate_limit_turnstile_per_hour": 1}))
    with respx.mock:
        approve()
        with client_for(app) as client:
            assert post_verify(client, csrf_of(client)).status_code == 200
        # A new session from the same address must not exceed the per-address attempt limit.
        with client_for(app) as client:
            blocked = post_verify(client, csrf_of(client))
            assert blocked.status_code == 429
            assert blocked.json()["code"] == "rate_limited"
    async with db.sessions() as session:
        scopes = (await session.scalars(select(RateLimitCounter.scope))).all()
    assert ratelimit.TURNSTILE_IP in scopes
