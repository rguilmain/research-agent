"""Shared fixed-window rate limiting for anonymous admission and research starts.

Counters live in PostgreSQL so limits hold across processes, and every key is an HMAC:
raw addresses and identifiers are never stored, logged or returned. Records expire only
through the bounded cleanup command, and a process without a database is not limited.
"""

import hashlib
import hmac
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Protocol

from fastapi import Request

from research_agent.config import Settings

WINDOW_SECONDS = 3600
CLIENT_IP_HEADER = "fly-client-ip"
FORWARDED_FOR_HEADER = "x-forwarded-for"
UNKNOWN_CLIENT = "unknown"

ANON_SESSION = "anon_session"
RESEARCH_SESSION = "research_session"
RESEARCH_IP = "research_ip"
LOGIN_IP = "login_ip"
TURNSTILE_IP = "turnstile_ip"


class CounterStore(Protocol):
    """The single repository operation this module needs; keeps fakes type-checkable."""

    async def consume_rate_limit(
        self, scope: str, key: str, window_start: datetime, limit: int
    ) -> tuple[bool, int]: ...


class RateLimitExceeded(Exception):
    """Admission refusal rendered as 429 problem JSON with a Retry-After hint."""

    def __init__(self, scope: str, retry_after: int) -> None:
        super().__init__(f"Rate limit exceeded for {scope}")
        self.scope = scope
        self.retry_after = retry_after


def window_start(moment: datetime) -> datetime:
    """Aligned fixed window. A burst across a boundary can briefly exceed the limit."""
    epoch = int(moment.timestamp()) // WINDOW_SECONDS * WINDOW_SECONDS
    return datetime.fromtimestamp(epoch, UTC)


def _is_ip(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True


def _forwarded_ip(request: Request) -> str | None:
    """Rightmost valid entry: the hop appended by the trusted proxy.

    The leftmost entry is client-supplied and therefore spoofable, so it is never used.
    """
    header = request.headers.get(FORWARDED_FOR_HEADER, "")
    for candidate in reversed([part.strip() for part in header.split(",")]):
        if _is_ip(candidate):
            return candidate
    return None


def client_ip(request: Request, settings: Settings) -> str:
    """Caller address; proxy headers are ignored unless TRUST_PROXY_HEADERS is enabled.

    Fly's edge sets and overwrites Fly-Client-IP. This assumption must be re-verified
    against the real ingress before any public deployment.
    """
    peer = request.client.host if request.client and request.client.host else UNKNOWN_CLIENT
    if not settings.trust_proxy_headers:
        return peer
    edge = request.headers.get(CLIENT_IP_HEADER, "").strip()
    if _is_ip(edge):
        return edge
    return _forwarded_ip(request) or peer


def key_hash(secret: bytes, kind: str, value: str) -> str:
    return hmac.new(secret, (kind + ":" + value).encode(), hashlib.sha256).hexdigest()


def ip_key(secret: bytes, ip: str) -> str:
    return key_hash(secret, "ip", ip)


def principal_key(secret: bytes, principal_id: object) -> str:
    return key_hash(secret, "principal", str(principal_id))


async def enforce(repo: CounterStore, scope: str, key: str, limit: int) -> None:
    """Count one attempt and refuse it when the current window is already full.

    The counter is incremented before any work is admitted, so an attempt that later
    fails still consumes budget: this never over-admits.
    """
    allowed, retry_after = await repo.consume_rate_limit(
        scope, key, window_start(datetime.now(UTC)), limit
    )
    if not allowed:
        raise RateLimitExceeded(scope, retry_after)
