"""Daily quota policy exercised without a database or any provider call."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from starlette.requests import Request

from research_agent import quota, ratelimit
from research_agent.auth import AuthContext
from research_agent.catalog import ModelSpec, load_catalog
from research_agent.config import Settings
from research_agent.repository import Identity

SECRET = "s" * 40
ANONYMOUS_MODEL = ModelSpec(
    id="anon-model",
    label="Anon",
    input_usd_per_mtok=Decimal(0),
    output_usd_per_mtok=Decimal(0),
    enabled=True,
    anonymous=True,
)
STAFF_MODEL = ModelSpec(
    id="staff-model",
    label="Staff",
    input_usd_per_mtok=Decimal(0),
    output_usd_per_mtok=Decimal(0),
    enabled=True,
)


class FakeQuotaStore:
    """Records every charge/refund so policy changes can be asserted offline."""

    def __init__(self, remaining: int | None = 2) -> None:
        self.remaining = remaining
        self.consumed: list[tuple[date, list[tuple[str, str]], int, int]] = []
        self.refunded: list[tuple[date, list[tuple[str, str]], int]] = []

    async def consume_quota(self, day, keys, units, limit) -> int | None:
        self.consumed.append((day, list(keys), units, limit))
        return self.remaining

    async def refund_quota(self, day, keys, units) -> None:
        self.refunded.append((day, list(keys), units))


def settings(**overrides) -> Settings:
    return Settings(app_env="test", session_secret=SecretStr(SECRET), **overrides)


def request_with_ip(host: str = "198.51.100.7") -> Request:
    return Request(
        {"type": "http", "method": "POST", "path": "/", "headers": [], "client": (host, 1234)}
    )


def session(kind: str = "anonymous") -> AuthContext:
    return AuthContext(identity=Identity(uuid4(), kind), kind=kind)


def test_quota_day_and_reset_are_utc_aligned():
    assert quota.quota_day(datetime(2026, 11, 17, 23, 59, 59, tzinfo=UTC)) == date(2026, 11, 17)
    assert quota.seconds_until_reset(datetime(2026, 11, 17, 23, 59, 59, tzinfo=UTC)) == 1
    assert quota.seconds_until_reset(datetime(2026, 11, 17, 0, 0, 0, tzinfo=UTC)) == 86_400
    # A non-UTC moment is normalised before the day is taken.
    shifted = datetime(2026, 11, 17, 1, 30, tzinfo=UTC).astimezone(UTC)
    assert quota.quota_day(shifted) == date(2026, 11, 17)


def test_daily_limit_depends_on_session_kind():
    config = Settings(
        app_env="test",
        session_secret=SecretStr(SECRET),
        quota_anon_daily=4,
        quota_user_daily=40,
    )
    assert quota.daily_limit(config, "anonymous") == 4
    assert quota.daily_limit(config, "user") == 40
    # The no-database demo identity is never charged, but must not crash.
    assert quota.daily_limit(config, "none") == 4


def test_model_tiers_gate_anonymous_callers():
    assert quota.model_allowed_for("anonymous", ANONYMOUS_MODEL)
    assert not quota.model_allowed_for("anonymous", STAFF_MODEL)
    assert quota.model_allowed_for("user", STAFF_MODEL)
    assert quota.model_allowed_for("none", STAFF_MODEL)


def test_catalog_carries_quota_weights_and_tiers():
    catalog = load_catalog()
    assert catalog["gpt-6-luna"].quota_weight == 1
    assert catalog["gpt-6-luna"].anonymous is True
    assert catalog["gpt-6.1-terra"].quota_weight == 3
    assert catalog["gpt-6.1-sol"].quota_weight == 5
    assert catalog["gpt-6.1-terra"].anonymous is False


async def test_anonymous_consume_charges_session_and_address():
    store = FakeQuotaStore(remaining=2)
    config = settings(quota_anon_daily=3)
    grant = await quota.consume(
        store, config, request_with_ip(), session("anonymous"), ANONYMOUS_MODEL
    )
    day, keys, units, limit = store.consumed[0]
    assert day == quota.quota_day(datetime.now(UTC))
    assert units == ANONYMOUS_MODEL.quota_weight == 1
    assert limit == 3
    assert [scope for scope, _ in keys] == [quota.QUOTA_PRINCIPAL, quota.QUOTA_IP]
    assert keys[1][1] == ratelimit.ip_key(config.session_secret_bytes(), "198.51.100.7")
    assert grant.remaining == 2 and grant.limit == 3 and grant.units == 1
    assert grant.keys == tuple(keys)


async def test_signed_in_consume_is_charged_once_per_principal():
    store = FakeQuotaStore(remaining=25)
    grant = await quota.consume(store, settings(), request_with_ip(), session("user"), STAFF_MODEL)
    _, keys, units, limit = store.consumed[0]
    assert [scope for scope, _ in keys] == [quota.QUOTA_PRINCIPAL]
    assert units == STAFF_MODEL.quota_weight
    assert grant.limit == 25


async def test_a_refused_charge_raises_with_a_retry_hint():
    store = FakeQuotaStore(remaining=None)
    with pytest.raises(quota.QuotaExceeded) as excinfo:
        await quota.consume(store, settings(), request_with_ip(), session("user"), STAFF_MODEL)
    assert 1 <= excinfo.value.retry_after <= quota.DAY_SECONDS


async def test_refund_returns_the_exact_charge():
    store = FakeQuotaStore(remaining=2)
    grant = await quota.consume(store, settings(), request_with_ip(), session("user"), STAFF_MODEL)
    await quota.refund(store, grant)
    assert store.refunded == [(grant.day, list(grant.keys), grant.units)]


def test_quotas_must_be_positive():
    with pytest.raises(ValidationError):
        Settings(app_env="test", quota_anon_daily=0)
