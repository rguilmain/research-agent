"""Session, CSRF and origin rules exercised without a database."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI, Response
from starlette.requests import Request

from research_agent import ratelimit
from research_agent.auth import (
    SECURE_SESSION_COOKIE,
    SESSION_COOKIE,
    AuthContext,
    AuthError,
    allowed_origins,
    authenticated,
    browser_hash,
    clear_session_cookie,
    csrf_token,
    enforce_origin,
    optional_identity,
    require_csrf,
    safe_next_path,
    session_cookie_name,
    session_or_create,
    set_session_cookie,
    token_hash,
)
from research_agent.config import Settings
from research_agent.repository import Identity, SessionRecord


def settings(**overrides) -> Settings:
    return Settings(app_env="test", **overrides)


class FakeRepository:
    """Minimal session store so dependencies can be tested without PostgreSQL."""

    def __init__(self) -> None:
        self.sessions: dict[str, SessionRecord] = {}
        self.created = 0
        self.revoked: list[str] = []
        self.denied = False
        self.rate_limits: list[tuple[str, str, int]] = []

    async def consume_rate_limit(
        self, scope: str, key: str, window_start: datetime, limit: int
    ) -> tuple[bool, int]:
        self.rate_limits.append((scope, key, limit))
        return not self.denied, 60

    async def create_anonymous_session(self, token_hash: str) -> SessionRecord:
        self.created += 1
        record = SessionRecord(
            token_hash=token_hash,
            principal_id=uuid4(),
            kind="anonymous",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        self.sessions[token_hash] = record
        return record

    async def resolve_session(self, token_hash: str) -> SessionRecord | None:
        return self.sessions.get(token_hash)

    async def revoke_session(self, token_hash: str) -> None:
        self.revoked.append(token_hash)


def make_request(
    config: Settings,
    repo: FakeRepository | None,
    *,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    method: str = "GET",
) -> Request:
    """Build a bare Starlette request with an app that carries settings and storage."""
    app = FastAPI()
    app.state.settings = config
    app.state.repository = repo
    raw = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    if cookies:
        raw.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
    scope = {
        "type": "http",
        "method": method,
        "path": "/",
        "headers": raw,
        "app": app,
    }
    return Request(scope)


def secret() -> bytes:
    return settings().session_secret_bytes()


def signed_in(
    config: Settings,
    repo: FakeRepository,
    token: str,
    *,
    kind: str = "anonymous",
    login: str | None = None,
) -> SessionRecord:
    """Store a session row and return it, mirroring what the repository would hold."""
    record = SessionRecord(
        token_hash=token_hash(secret(), token),
        principal_id=uuid4(),
        kind=kind,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        login=login,
    )
    repo.sessions[record.token_hash] = record
    return record


def cookie_name(config: Settings) -> str:
    return session_cookie_name(config)


@pytest.mark.parametrize("value", ["/", "/threads", "/threads/abc?x=1", "/a/b#c", "/a-b_c~d"])
def test_safe_next_path_accepts_relative_paths(value: str) -> None:
    assert safe_next_path(value) == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "https://evil.example",
        "//evil.example",
        "/\\evil.example",
        "/a\nb",
        "/a\rb",
        "a/b",
        "/" + "x" * 600,
    ],
)
def test_safe_next_path_rejects_anything_else(value: str | None) -> None:
    assert safe_next_path(value) == "/"


def test_token_hash_is_deterministic_and_secret_bound() -> None:
    key = b"k" * 32
    assert token_hash(key, "abc") == token_hash(key, "abc")
    assert token_hash(key, "abc") != token_hash(b"j" * 32, "abc")
    assert token_hash(key, "abc") != token_hash(key, "abd")


def test_csrf_token_is_derived_from_the_session_hash() -> None:
    key = b"k" * 32
    digest = token_hash(key, "abc")
    assert csrf_token(key, digest) == csrf_token(key, digest)
    assert csrf_token(key, digest) != csrf_token(key, "other")
    assert csrf_token(key, digest) != digest


def test_browser_hash_is_secret_bound() -> None:
    key = b"k" * 32
    assert browser_hash(key, "agent") == browser_hash(key, "agent")
    assert browser_hash(key, "agent") != browser_hash(key, "other")
    assert browser_hash(key, "agent") != token_hash(key, "agent")


def test_allowed_origins_include_loopback_aliases_outside_production() -> None:
    config = settings()
    origins = allowed_origins(config)
    assert config.public_origin in origins
    assert "http://127.0.0.1:5173" in origins
    assert "http://localhost:5173" in origins
    assert "http://[::1]:5173" in origins


def test_allowed_origins_are_strict_in_production() -> None:
    production = settings().model_copy(update={"app_env": "production"})
    assert allowed_origins(production) == {"http://127.0.0.1:5173"}


def test_allowed_origins_skip_aliases_for_non_loopback_bases() -> None:
    config = settings(public_base_url="https://app.example.com")
    assert allowed_origins(config) == {"https://app.example.com"}


def test_session_cookie_name_uses_host_prefix_only_when_secure() -> None:
    assert session_cookie_name(settings()) == SESSION_COOKIE
    secure = settings(public_base_url="https://app.example.com")
    assert session_cookie_name(secure) == SECURE_SESSION_COOKIE


def test_set_session_cookie_is_httponly_and_lax() -> None:
    response = Response()
    set_session_cookie(response, settings(), "token-value", 3600)
    header = response.headers["set-cookie"]
    assert "ra_session=token-value" in header
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Path=/" in header
    assert "Max-Age=3600" in header


def test_clear_session_cookie_expires_immediately() -> None:
    response = Response()
    clear_session_cookie(response, settings())
    header = response.headers["set-cookie"]
    assert "ra_session=" in header
    assert "Max-Age=0" in header


def test_enforce_origin_accepts_the_configured_origin() -> None:
    config = settings()
    request = make_request(config, None, headers={"origin": "http://127.0.0.1:5173"})
    enforce_origin(request, config)


def test_enforce_origin_rejects_a_foreign_origin() -> None:
    config = settings()
    request = make_request(config, None, headers={"origin": "https://evil.example"})
    with pytest.raises(AuthError) as excinfo:
        enforce_origin(request, config)
    assert excinfo.value.status == 403
    assert excinfo.value.code == "csrf_failed"


def test_enforce_origin_accepts_same_origin_fetch_metadata() -> None:
    config = settings()
    request = make_request(config, None, headers={"sec-fetch-site": "same-origin"})
    enforce_origin(request, config)


def test_enforce_origin_rejects_missing_origin_metadata() -> None:
    config = settings()
    with pytest.raises(AuthError) as excinfo:
        enforce_origin(make_request(config, None), config)
    assert excinfo.value.code == "csrf_failed"


def test_auth_context_defaults_to_anonymous() -> None:
    identity = Identity(uuid4())
    context = AuthContext(identity=identity, kind="anonymous")
    assert context.identity is identity
    assert context.created is False
    assert context.csrf_token is None
    assert context.login is None


async def test_session_or_create_makes_an_anonymous_session() -> None:
    config = settings()
    repo = FakeRepository()
    context = await session_or_create(make_request(config, repo))
    assert context.created is True
    assert context.kind == "anonymous"
    assert context.token is not None
    assert context.csrf_token is not None
    assert repo.created == 1
    assert repo.rate_limits[0][0] == ratelimit.ANON_SESSION


async def test_session_or_create_is_refused_when_the_address_is_limited() -> None:
    config = settings()
    repo = FakeRepository()
    repo.denied = True
    with pytest.raises(ratelimit.RateLimitExceeded) as excinfo:
        await session_or_create(make_request(config, repo))
    assert excinfo.value.scope == ratelimit.ANON_SESSION
    assert repo.created == 0
    scope, key, limit = repo.rate_limits[0]
    assert (scope, limit) == (ratelimit.ANON_SESSION, config.rate_limit_anon_session_per_hour)
    assert len(key) == 64


async def test_session_or_create_reuses_an_existing_session() -> None:
    config = settings()
    repo = FakeRepository()
    record = signed_in(config, repo, "existing-token")
    request = make_request(config, repo, cookies={cookie_name(config): "existing-token"})
    context = await session_or_create(request)
    assert context.created is False
    assert context.identity.id == record.principal_id
    assert repo.created == 0


async def test_session_or_create_without_a_repository_has_no_identity() -> None:
    context = await session_or_create(make_request(settings(), None))
    assert context.kind == "none"
    assert context.created is False


async def test_authenticated_requires_a_session() -> None:
    config = settings()
    with pytest.raises(AuthError) as excinfo:
        await authenticated(make_request(config, FakeRepository()))
    assert excinfo.value.status == 401
    assert excinfo.value.code == "session_required"


async def test_authenticated_returns_the_session_identity() -> None:
    config = settings()
    repo = FakeRepository()
    record = signed_in(config, repo, "live-token", kind="user", login="octocat")
    request = make_request(config, repo, cookies={cookie_name(config): "live-token"})
    context = await authenticated(request)
    assert context.identity.id == record.principal_id
    assert context.kind == "user"
    assert context.login == "octocat"


async def test_authenticated_without_a_repository_uses_a_placeholder() -> None:
    context = await authenticated(make_request(settings(), None))
    assert context.kind == "none"


async def test_require_csrf_rejects_a_missing_token() -> None:
    config = settings()
    repo = FakeRepository()
    signed_in(config, repo, "live-token")
    request = make_request(
        config,
        repo,
        headers={"origin": "http://127.0.0.1:5173"},
        cookies={cookie_name(config): "live-token"},
        method="POST",
    )
    with pytest.raises(AuthError) as excinfo:
        await require_csrf(request)
    assert excinfo.value.status == 403
    assert excinfo.value.code == "csrf_failed"


async def test_require_csrf_accepts_the_session_token() -> None:
    config = settings()
    repo = FakeRepository()
    signed_in(config, repo, "live-token")
    expected = csrf_token(secret(), token_hash(secret(), "live-token"))
    request = make_request(
        config,
        repo,
        headers={"origin": "http://127.0.0.1:5173", "x-csrf-token": expected},
        cookies={cookie_name(config): "live-token"},
        method="POST",
    )
    context = await require_csrf(request)
    assert context.csrf_token == expected


async def test_require_csrf_rejects_a_wrong_token() -> None:
    config = settings()
    repo = FakeRepository()
    signed_in(config, repo, "live-token")
    request = make_request(
        config,
        repo,
        headers={"origin": "http://127.0.0.1:5173", "x-csrf-token": "nope"},
        cookies={cookie_name(config): "live-token"},
        method="POST",
    )
    with pytest.raises(AuthError) as excinfo:
        await require_csrf(request)
    assert excinfo.value.code == "csrf_failed"


async def test_require_csrf_checks_origin_before_the_session() -> None:
    config = settings()
    repo = FakeRepository()
    signed_in(config, repo, "live-token")
    request = make_request(
        config,
        repo,
        headers={"origin": "https://evil.example"},
        cookies={cookie_name(config): "live-token"},
        method="POST",
    )
    with pytest.raises(AuthError) as excinfo:
        await require_csrf(request)
    assert excinfo.value.status == 403


async def test_require_csrf_without_a_repository_has_no_identity() -> None:
    config = settings()
    request = make_request(config, None, headers={"origin": "http://127.0.0.1:5173"}, method="POST")
    context = await require_csrf(request)
    assert context.kind == "none"


async def test_optional_identity_returns_none_without_a_cookie() -> None:
    config = settings()
    assert await optional_identity(make_request(config, FakeRepository())) is None


async def test_optional_identity_returns_none_without_a_repository() -> None:
    assert await optional_identity(make_request(settings(), None)) is None


async def test_optional_identity_resolves_an_existing_session() -> None:
    config = settings()
    repo = FakeRepository()
    record = signed_in(config, repo, "live-token")
    request = make_request(config, repo, cookies={cookie_name(config): "live-token"})
    context = await optional_identity(request)
    assert context is not None
    assert context.identity.id == record.principal_id
