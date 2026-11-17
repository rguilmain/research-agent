"""Daily weighted question quotas, shared across processes in PostgreSQL.

Anonymous callers are limited both per session and per hashed client address, so deleting
a cookie does not reset the allowance. Signed-in callers are limited per principal. Units
are charged only when they fit, and a request that fails before it does any work is
refunded. Keys are HMACs: raw addresses and identifiers are never stored or logged.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol

from fastapi import Request

from research_agent import ratelimit
from research_agent.auth import AuthContext
from research_agent.catalog import ModelSpec
from research_agent.config import Settings

DAY_SECONDS = 86_400
QUOTA_PRINCIPAL = "quota_principal"
QUOTA_IP = "quota_ip"


class QuotaStore(Protocol):
    """The repository operations this module needs; keeps fakes type-checkable."""

    async def consume_quota(
        self, day: date, keys: list[tuple[str, str]], units: int, limit: int
    ) -> int | None: ...

    async def refund_quota(self, day: date, keys: list[tuple[str, str]], units: int) -> None: ...


class QuotaExceeded(Exception):
    """Refusal rendered as 429 problem JSON with a Retry-After hint."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("Daily quota exceeded")
        self.retry_after = retry_after


@dataclass(frozen=True)
class QuotaGrant:
    """A consumed allowance, retained so a failed request can be refunded."""

    day: date
    keys: tuple[tuple[str, str], ...]
    units: int
    limit: int
    remaining: int


def quota_day(moment: datetime) -> date:
    """UTC calendar day; the busy window is deliberately fixed, not rolling."""
    return moment.astimezone(UTC).date()


def seconds_until_reset(moment: datetime) -> int:
    """Whole seconds until the next UTC midnight, never below one."""
    moment = moment.astimezone(UTC)
    tomorrow = moment.date() + timedelta(days=1)
    reset = datetime.combine(tomorrow, time.min, tzinfo=UTC)
    return max(1, int((reset - moment).total_seconds()))


def daily_limit(settings: Settings, kind: str) -> int:
    """Signed-in callers get the larger allowance; everyone else the anonymous one."""
    return settings.quota_user_daily if kind == "user" else settings.quota_anon_daily


def model_allowed_for(kind: str, model: ModelSpec) -> bool:
    """Only models marked ``anonymous`` may be selected by an anonymous caller."""
    return kind != "anonymous" or model.anonymous


async def consume(
    repo: QuotaStore,
    settings: Settings,
    request: Request,
    session: AuthContext,
    model: ModelSpec,
) -> QuotaGrant:
    """Charge one question against every applicable daily counter, or refuse it."""
    secret = settings.session_secret_bytes()
    keys = [(QUOTA_PRINCIPAL, ratelimit.principal_key(secret, session.identity.id))]
    if session.kind == "anonymous":
        # Cookie-less clients must also be limited by address, not only by session.
        keys.append((QUOTA_IP, ratelimit.ip_key(secret, ratelimit.client_ip(request, settings))))
    moment = datetime.now(UTC)
    limit = daily_limit(settings, session.kind)
    remaining = await repo.consume_quota(quota_day(moment), list(keys), model.quota_weight, limit)
    if remaining is None:
        raise QuotaExceeded(seconds_until_reset(moment))
    return QuotaGrant(
        day=quota_day(moment),
        keys=tuple(keys),
        units=model.quota_weight,
        limit=limit,
        remaining=remaining,
    )


async def refund(repo: QuotaStore, grant: QuotaGrant) -> None:
    """Return the charged units after a request failed before producing any work."""
    await repo.refund_quota(grant.day, list(grant.keys), grant.units)
