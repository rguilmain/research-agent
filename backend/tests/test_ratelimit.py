"""Offline tests for address handling, hashed keys, window alignment and enforcement."""

from datetime import UTC, datetime, timedelta

import pytest
from starlette.requests import Request

from research_agent import ratelimit
from research_agent.config import Settings

SECRET = b"unit-test-secret-used-only-for-hmac-keys"
NO_CLIENT = object()


def make_request(headers: dict[str, str] | None = None, client: object = NO_CLIENT) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [
            (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
        ],
        "client": None if client is NO_CLIENT else client,
        "server": ("testserver", 80),
        "scheme": "http",
        "query_string": b"",
    }
    return Request(scope)


def settings(**overrides) -> Settings:
    return Settings(app_env="test", **overrides)


def test_proxy_headers_are_ignored_unless_trusted():
    request = make_request(
        {"fly-client-ip": "198.51.100.7", "x-forwarded-for": "198.51.100.7, 192.0.2.4"},
        client=("203.0.113.9", 4321),
    )
    assert ratelimit.client_ip(request, settings()) == "203.0.113.9"


def test_trusted_edge_header_wins_when_configured():
    request = make_request(
        {"fly-client-ip": "198.51.100.7", "x-forwarded-for": "192.0.2.4"},
        client=("203.0.113.9", 4321),
    )
    trusted = settings(trust_proxy_headers=True)
    assert ratelimit.client_ip(request, trusted) == "198.51.100.7"


def test_forwarded_for_uses_the_rightmost_valid_entry():
    request = make_request({"x-forwarded-for": "192.0.2.4, 203.0.113.50"})
    trusted = settings(trust_proxy_headers=True)
    # The left-most entry is client-supplied and therefore spoofable, so it is never used.
    assert ratelimit.client_ip(request, trusted) == "203.0.113.50"


def test_forwarded_for_skips_garbage_entries():
    request = make_request({"x-forwarded-for": "198.51.100.7, not-an-ip"})
    trusted = settings(trust_proxy_headers=True)
    assert ratelimit.client_ip(request, trusted) == "198.51.100.7"


def test_missing_or_invalid_proxy_headers_fall_back_to_the_peer():
    trusted = settings(trust_proxy_headers=True)
    fallback = ("203.0.113.9", 4321)
    assert ratelimit.client_ip(make_request({"fly-client-ip": "not-an-ip"}, fallback), trusted) == (
        "203.0.113.9"
    )
    assert ratelimit.client_ip(make_request(client=fallback), trusted) == "203.0.113.9"
    assert ratelimit.client_ip(make_request({"x-forwarded-for": ""}, fallback), trusted) == (
        "203.0.113.9"
    )


def test_missing_client_information_is_marked_unknown():
    assert ratelimit.client_ip(make_request(client=None), settings()) == "unknown"


def test_ipv6_peer_is_preserved_for_keying():
    request = make_request(client=("2001:db8::1", 4321))
    assert ratelimit.client_ip(request, settings()) == "2001:db8::1"


def test_ip_key_is_hashed_deterministic_and_secret_bound():
    key = ratelimit.ip_key(SECRET, "198.51.100.7")
    assert key == ratelimit.ip_key(SECRET, "198.51.100.7")
    assert key != ratelimit.ip_key(b"a-different-secret-value", "198.51.100.7")
    assert key != ratelimit.ip_key(SECRET, "198.51.100.8")
    assert "198.51.100.7" not in key
    assert len(key) == 64


def test_principal_key_never_reveals_the_identifier():
    identifier = "b7b8c0f2-2a6c-4a7e-8f0a-0a1b2c3d4e5f"
    key = ratelimit.principal_key(SECRET, identifier)
    assert identifier not in key
    assert len(key) == 64


def test_window_start_aligns_to_hour_buckets():
    aligned = datetime(2026, 11, 17, 13, 0, tzinfo=UTC)
    assert ratelimit.window_start(aligned) == aligned
    assert ratelimit.window_start(datetime(2026, 11, 17, 13, 45, 59, tzinfo=UTC)) == aligned
    assert ratelimit.window_start(datetime(2026, 11, 17, 12, 59, 59, tzinfo=UTC)) == (
        aligned - timedelta(hours=1)
    )


class FakeStore:
    def __init__(self, allowed: bool, retry_after: int = 17) -> None:
        self.allowed = allowed
        self.retry_after = retry_after
        self.calls: list[tuple[str, str, datetime, int]] = []

    async def consume_rate_limit(
        self, scope: str, key: str, window_start: datetime, limit: int
    ) -> tuple[bool, int]:
        self.calls.append((scope, key, window_start, limit))
        return self.allowed, self.retry_after


async def test_enforce_passes_the_aligned_window_and_the_limit():
    store = FakeStore(allowed=True)
    before = ratelimit.window_start(datetime.now(UTC))
    await ratelimit.enforce(store, ratelimit.RESEARCH_IP, "k" * 64, 7)
    scope, key, window, limit = store.calls[0]
    assert (scope, key, limit) == (ratelimit.RESEARCH_IP, "k" * 64, 7)
    assert before <= window <= ratelimit.window_start(datetime.now(UTC))


async def test_enforce_raises_with_the_scope_and_retry_hint():
    store = FakeStore(allowed=False, retry_after=42)
    with pytest.raises(ratelimit.RateLimitExceeded) as raised:
        await ratelimit.enforce(store, ratelimit.ANON_SESSION, "k" * 64, 1)
    assert raised.value.scope == ratelimit.ANON_SESSION
    assert raised.value.retry_after == 42
    assert "k" * 64 not in str(raised.value)


def test_scope_names_fit_the_storage_column():
    scopes = (
        ratelimit.ANON_SESSION,
        ratelimit.RESEARCH_SESSION,
        ratelimit.RESEARCH_IP,
        ratelimit.LOGIN_IP,
    )
    assert all(scope and len(scope) <= 32 for scope in scopes)
    assert len(set(scopes)) == len(scopes)
