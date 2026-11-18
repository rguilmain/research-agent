"""Budget ledger, concurrency caps and admission tests (REQUIRE_PG=1 in CI)."""

import asyncio
import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import respx
from alembic import command
from alembic.config import Config
from auth_fixtures import signed_in_client
from http_fixtures import responses_stream
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from research_agent.budget import BudgetExhausted
from research_agent.config import Settings
from research_agent.db import Database
from research_agent.main import create_app
from research_agent.maintenance import setup_checkpoints
from research_agent.repository import Identity, Repository
from research_agent.storage_models import (
    BudgetDay,
    BudgetReservation,
    Principal,
    QuotaUsage,
    Run,
    Thread,
    now,
)

pytestmark = pytest.mark.pg

LIMIT = Decimal("5")


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
        quota_anon_daily=1000,
        quota_user_daily=1000,
        global_daily_budget_usd=LIMIT,
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
    "rate_limit_counters, quota_usage, budget_reservations, budget_days, checkpoints, "
    "checkpoint_blobs, checkpoint_writes CASCADE"
)


def truncate_all(settings: Settings) -> None:
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


def live_settings(pg_settings, **overrides) -> Settings:
    values = {
        "demo_mode": False,
        "openai_api_key": SecretStr("offline-key"),
        "tavily_api_key": SecretStr("offline-key"),
    }
    values.update(overrides)
    return pg_settings.model_copy(update=values)


async def budget_row(db) -> tuple[Decimal, Decimal] | None:
    async with db.sessions() as session:
        return (
            await session.execute(
                select(BudgetDay.reserved_usd, BudgetDay.spent_usd).where(
                    BudgetDay.day == now().date()
                )
            )
        ).first()


async def reservation_statuses(db) -> list[str]:
    async with db.sessions() as session:
        return list(await session.scalars(select(BudgetReservation.status)))


async def admission_counts(db) -> tuple[int, int, int]:
    """Threads, runs and quota units, used to prove a refused request created nothing."""
    async with db.sessions() as session:
        threads = await session.scalar(select(func.count()).select_from(Thread))
        runs = await session.scalar(select(func.count()).select_from(Run))
        units = await session.scalar(select(func.coalesce(func.sum(QuotaUsage.units), 0)))
    return threads or 0, runs or 0, units or 0


async def test_budget_tables_exist_at_head(db):
    async with db.sessions() as session:
        revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        tables = set(
            await session.scalars(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_name IN ('budget_days', 'budget_reservations')"
                )
            )
        )
    assert revision == "0006"
    assert tables == {"budget_days", "budget_reservations"}


async def test_reservations_are_atomic_and_never_over_reserve(db, pg_settings):
    repo = Repository(db, pg_settings)
    ttl = timedelta(minutes=10)
    results = await asyncio.gather(
        *(repo.reserve_budget(None, "model", Decimal("0.30"), LIMIT, ttl) for _ in range(20)),
        return_exceptions=True,
    )
    granted = [item for item in results if not isinstance(item, BaseException)]
    refused = [item for item in results if isinstance(item, BudgetExhausted)]
    assert len(granted) == 16 and len(refused) == 4
    row = await budget_row(db)
    assert row is not None and row[0] == Decimal("4.800000") and row[1] == Decimal("0")


async def test_settlement_releases_the_hold_and_records_the_charge_once(db, pg_settings):
    repo = Repository(db, pg_settings)
    reservation = await repo.reserve_budget(
        None, "model", Decimal("0.50"), LIMIT, timedelta(minutes=10)
    )
    await repo.settle_budget(reservation, Decimal("0.10"))
    row = await budget_row(db)
    assert row == (Decimal("0"), Decimal("0.100000"))
    # Settling twice must not charge twice.
    await repo.settle_budget(reservation, Decimal("0.10"))
    assert await budget_row(db) == (Decimal("0"), Decimal("0.100000"))
    async with db.sessions() as session:
        stored = await session.get(BudgetReservation, reservation)
    assert (
        stored is not None
        and stored.status == "settled"
        and stored.actual_usd == Decimal("0.100000")
    )


async def test_expiry_charges_the_full_hold_and_zeroes_inflated_headroom(db, pg_settings):
    repo = Repository(db, pg_settings)
    reservation = await repo.reserve_budget(
        None, "tool", Decimal("0.80"), LIMIT, timedelta(minutes=10)
    )
    async with db.sessions.begin() as session:
        await session.execute(
            text("UPDATE budget_reservations SET expires_at = now() - interval '1 minute'")
        )
    assert await repo.expire_stale_reservations() == 1
    assert await budget_row(db) == (Decimal("0"), Decimal("0.800000"))
    async with db.sessions() as session:
        stored = await session.get(BudgetReservation, reservation)
    assert stored is not None and stored.status == "expired"
    # Expiry is conservative: it inflates committed spend, never lowers it.
    assert await repo.budget_headroom(LIMIT) == LIMIT - Decimal("0.800000")


async def test_run_settlement_only_touches_that_runs_holds(db, pg_settings):
    repo = Repository(db, pg_settings)
    # Runs need real rows for the foreign key, so create them through the repository.
    owner_a, owner_b = Identity(uuid4()), Identity(uuid4())
    thread_a = (await repo.create_thread(owner_a, "gpt-6-luna"))["id"]
    thread_b = (await repo.create_thread(owner_b, "gpt-6-luna"))["id"]
    run_a = await repo.start_run(owner_a, thread_a, "gpt-6-luna")
    run_b = await repo.start_run(owner_b, thread_b, "gpt-6-luna")
    await repo.reserve_budget(run_a, "model", Decimal("0.20"), LIMIT, timedelta(minutes=10))
    await repo.reserve_budget(run_b, "model", Decimal("0.30"), LIMIT, timedelta(minutes=10))
    assert await repo.settle_open_for_run(run_a) == 1
    async with db.sessions() as session:
        rows = (
            await session.execute(select(BudgetReservation.run_id, BudgetReservation.status))
        ).all()
    settled = {row.run_id: row.status for row in rows}
    assert settled[run_a] == "settled" and settled[run_b] == "open"


async def test_headroom_starts_full_and_follows_spend(db, pg_settings):
    repo = Repository(db, pg_settings)
    assert await repo.budget_headroom(LIMIT) == LIMIT
    reservation = await repo.reserve_budget(
        None, "model", Decimal("2.00"), LIMIT, timedelta(minutes=10)
    )
    assert await repo.budget_headroom(LIMIT) == Decimal("3")
    await repo.settle_budget(reservation, Decimal("1.50"))
    assert await repo.budget_headroom(LIMIT) == Decimal("3.5")


async def test_exhausted_budget_refuses_before_creating_anything(db, pg_settings):
    async with db.sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO budget_days (day, reserved_usd, spent_usd, updated_at) "
                "VALUES (CURRENT_DATE, :limit, 0, now())"
            ),
            {"limit": LIMIT},
        )
    app = create_app(live_settings(pg_settings))
    with signed_in_client(app) as client:
        response = client.post("/api/v1/research", json={"query": "budget question"})
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "budget_exhausted"
    assert 0 < int(response.headers["retry-after"]) <= 86_400
    assert await admission_counts(db) == (0, 0, 0)


async def test_principal_concurrency_cap_uses_heartbeat_freshness(db, pg_settings):
    app = create_app(live_settings(pg_settings, max_active_runs=5, max_active_runs_per_principal=1))
    with respx.mock:
        # Only the second request reaches a provider; the first is refused before any work.
        respx.post("https://api.openai.com/v1/responses").mock(
            return_value=responses_stream(text="Allowed answer", response_id="cap-run")
        )
        with signed_in_client(app) as client:
            # Occupy the caller's single slot with a fresh run that is not streaming here.
            async with db.sessions.begin() as session:
                owner_id = await session.scalar(select(Principal.id))
                thread = Thread(owner_id=owner_id, model_id="gpt-6-luna", title="busy")
                session.add(thread)
                await session.flush()
                session.add(Run(thread_id=thread.id, owner_id=owner_id, model_id="gpt-6-luna"))
            blocked = client.post("/api/v1/research", json={"query": "too soon"})
            assert blocked.status_code == 429
            assert blocked.json()["code"] == "too_many_active_runs"
            assert blocked.headers["retry-after"] == "15"
            # A run whose worker stopped heartbeating is not allowed to block new work.
            async with db.sessions.begin() as session:
                await session.execute(
                    text("UPDATE runs SET heartbeat_at = now() - interval '10 minutes'")
                )
            allowed = client.post("/api/v1/research", json={"query": "now allowed"})
    assert allowed.status_code == 200
    assert '"type": "done"' in allowed.text


async def test_a_live_run_reserves_and_settles_without_leaving_holds_open(db, pg_settings):
    app = create_app(live_settings(pg_settings))
    with respx.mock:
        respx.post("https://api.openai.com/v1/responses").mock(
            return_value=responses_stream(text="Budgeted answer", response_id="budget-run")
        )
        with signed_in_client(app) as client:
            response = client.post("/api/v1/research", json={"query": "budgeted question"})
    assert response.status_code == 200
    assert '"type": "done"' in response.text
    statuses = await reservation_statuses(db)
    assert "open" not in statuses and "settled" in statuses
    row = await budget_row(db)
    assert row is not None
    reserved, spent = row
    # Everything charged was released from the hold and is strictly inside the daily limit.
    assert reserved == Decimal("0") and Decimal("0") < spent <= LIMIT
    async with db.sessions() as session:
        kinds = set(await session.scalars(select(BudgetReservation.kind)))
    assert kinds == {"model"}
