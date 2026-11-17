"""Session cookies, CSRF tokens and request-time identity dependencies.

Sessions are opaque random tokens. The database stores only an HMAC of the token, so a
database leak cannot be replayed as a live cookie. CSRF tokens are derived from the same
HMAC and are never stored or sent in a URL.
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, Request, Response

from research_agent.config import Settings
from research_agent.repository import Identity, Repository, SessionRecord

SESSION_COOKIE = "ra_session"
SECURE_SESSION_COOKIE = "__Host-ra_session"
OAUTH_COOKIE = "ra_oauth"
SESSION_TOKEN_BYTES = 32
CSRF_CONTEXT = "csrf:"
MAX_NEXT_PATH = 512
# Same-origin relative path only: no scheme, authority, backslash or line breaks.
NEXT_PATH = re.compile(r"^/(?!/)[^\\\r\n]*$")
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
NO_PRINCIPAL = UUID(int=0)


class AuthError(Exception):
    """Authentication or CSRF failure rendered as RFC 7807 problem JSON."""

    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class AuthContext:
    identity: Identity
    kind: str
    token: str | None = None
    token_hash: str | None = None
    csrf_token: str | None = None
    login: str | None = None
    avatar_url: str | None = None
    # True only when this request created the session, so only ``/me`` sets a cookie.
    created: bool = False


def token_hash(secret: bytes, token: str) -> str:
    return hmac.new(secret, token.encode(), hashlib.sha256).hexdigest()


def csrf_token(secret: bytes, session_token_hash: str) -> str:
    return hmac.new(
        secret, (CSRF_CONTEXT + session_token_hash).encode(), hashlib.sha256
    ).hexdigest()


def browser_hash(secret: bytes, value: str) -> str:
    return hmac.new(secret, ("browser:" + value).encode(), hashlib.sha256).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def safe_next_path(value: str | None) -> str:
    """Only a same-origin relative path is accepted; everything else becomes the root."""
    if not value or len(value) > MAX_NEXT_PATH or not NEXT_PATH.fullmatch(value):
        return "/"
    return value


def session_cookie_name(settings: Settings) -> str:
    return SECURE_SESSION_COOKIE if settings.secure_cookies else SESSION_COOKIE


def _origin_host(host: str) -> str:
    """Brackets IPv6 literals so the origin matches what browsers send."""
    return f"[{host}]" if ":" in host else host


def allowed_origins(settings: Settings) -> set[str]:
    """Configured origin plus loopback aliases, relaxed only outside production."""
    origins = {settings.public_origin}
    if settings.app_env != "production":
        parts = urlsplit(settings.public_base_url)
        if parts.hostname in LOOPBACK_HOSTS:
            port = f":{parts.port}" if parts.port else ""
            origins.update(
                {f"{parts.scheme}://{_origin_host(host)}{port}" for host in LOOPBACK_HOSTS}
            )
    return origins


def set_session_cookie(response: Response, settings: Settings, token: str, max_age: int) -> None:
    response.set_cookie(
        session_cookie_name(settings),
        token,
        max_age=max_age,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        session_cookie_name(settings),
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
    )


def set_oauth_cookie(response: Response, settings: Settings, value: str, max_age: int) -> None:
    response.set_cookie(
        OAUTH_COOKIE,
        value,
        max_age=max_age,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
    )


def clear_oauth_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        OAUTH_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
    )


def enforce_origin(request: Request, settings: Settings) -> None:
    """Reject unsafe requests whose origin cannot be proven same-origin."""
    origin = request.headers.get("origin")
    if origin is not None:
        if origin in allowed_origins(settings):
            return
        raise AuthError(403, "csrf_failed", "Cross-origin request rejected")
    if request.headers.get("sec-fetch-site") == "same-origin":
        return
    raise AuthError(403, "csrf_failed", "Missing origin information")


def settings_of(request: Request) -> Settings:
    return request.app.state.settings


def repository_of(request: Request) -> Repository | None:
    return request.app.state.repository


def _context_from(record: SessionRecord, secret: bytes, token: str | None) -> AuthContext:
    return AuthContext(
        identity=Identity(record.principal_id, kind=record.kind),
        kind=record.kind,
        token=token,
        token_hash=record.token_hash,
        csrf_token=csrf_token(secret, record.token_hash),
        login=record.login,
        avatar_url=record.avatar_url,
    )


async def _resolve(request: Request) -> AuthContext | None:
    repo = repository_of(request)
    if repo is None:
        return None
    settings = settings_of(request)
    token = request.cookies.get(session_cookie_name(settings))
    if not token:
        return None
    secret = settings.session_secret_bytes()
    record = await repo.resolve_session(token_hash(secret, token))
    if record is None:
        return None
    return _context_from(record, secret, token)


async def authenticated(request: Request) -> AuthContext:
    """Identity for thread and research routes; 401 when persistence exists but no session."""
    repo = repository_of(request)
    if repo is None:
        # Demo streaming without a database: no owner is used and no credentials exist.
        return AuthContext(identity=Identity(NO_PRINCIPAL), kind="none")
    context = await _resolve(request)
    if context is None:
        raise AuthError(401, "session_required", "Reload the page to start a session")
    return context


async def session_or_create(request: Request) -> AuthContext:
    """Identity for GET /api/v1/me: lazily create an anonymous session when absent.

    The cookie is set by the endpoint, not here: a dependency's injected ``Response``
    is discarded when the endpoint returns its own ``JSONResponse``.
    """
    repo = repository_of(request)
    if repo is None:
        return AuthContext(identity=Identity(NO_PRINCIPAL), kind="none")
    settings = settings_of(request)
    context = await _resolve(request)
    if context is not None:
        return context
    secret = settings.session_secret_bytes()
    token = new_token()
    record = await repo.create_anonymous_session(token_hash(secret, token))
    created = _context_from(record, secret, token)
    return AuthContext(
        identity=created.identity,
        kind=created.kind,
        token=token,
        token_hash=created.token_hash,
        csrf_token=created.csrf_token,
        created=True,
    )


async def require_csrf(request: Request) -> AuthContext:
    """Origin check plus a per-session CSRF token on every unsafe request."""
    settings = settings_of(request)
    enforce_origin(request, settings)
    repo = repository_of(request)
    if repo is None:
        return AuthContext(identity=Identity(NO_PRINCIPAL), kind="none")
    context = await _resolve(request)
    if context is None:
        raise AuthError(401, "session_required", "Reload the page to start a session")
    supplied = request.headers.get("x-csrf-token", "")
    if (
        not supplied
        or not context.csrf_token
        or not hmac.compare_digest(supplied, context.csrf_token)
    ):
        raise AuthError(403, "csrf_failed", "CSRF token missing or invalid")
    return context


Principal = Annotated[AuthContext, Depends(authenticated)]
CsrfChecked = Annotated[AuthContext, Depends(require_csrf)]
MeSession = Annotated[AuthContext, Depends(session_or_create)]


async def optional_identity(request: Request) -> AuthContext | None:
    """Best-effort identity for redirect endpoints that must not create sessions."""
    return await _resolve(request)
