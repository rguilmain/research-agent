"""Identity endpoints: lazy anonymous sessions, GitHub OAuth and logout.

Only ``GET /api/v1/me`` creates a session. Every other endpoint requires one or
rejects the request, so anonymous rows appear only when a browser asks for identity.
"""

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from research_agent import github_oauth, ratelimit, turnstile
from research_agent.auth import (
    AuthError,
    CsrfChecked,
    MeSession,
    browser_hash,
    clear_oauth_cookie,
    clear_session_cookie,
    human_verified,
    new_token,
    optional_identity,
    repository_of,
    safe_next_path,
    set_oauth_cookie,
    set_session_cookie,
    settings_of,
    token_hash,
)
from research_agent.github_oauth import OAuthError

logger = logging.getLogger(__name__)

router = APIRouter()

OAUTH_STATE_MAX_AGE = 600
AUTH_ERROR_REDIRECT = "/?auth_error=1"


class TurnstileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=turnstile.MAX_TOKEN_CHARS)


def _problem(request: Request, status: int, code: str, title: str) -> JSONResponse:
    """RFC 7807 response carrying a machine-readable code and the request id."""
    return JSONResponse(
        status_code=status,
        content={
            "type": "about:blank",
            "title": title,
            "status": status,
            "code": code,
            "request_id": request.state.request_id,
        },
        media_type="application/problem+json",
    )


def _me_body(context, settings) -> dict:
    user = None
    if context.kind == "user" and context.login:
        user = {"login": context.login, "avatar_url": context.avatar_url}
    required = settings.turnstile_enabled and context.kind == "anonymous"
    return {
        "kind": context.kind,
        "user": user,
        "csrf_token": context.csrf_token,
        "login_enabled": settings.login_enabled,
        "turnstile_required": required and not human_verified(context),
        "human_verified_until": (
            context.human_verified_until.isoformat() if context.human_verified_until else None
        ),
    }


@router.get("/api/v1/me")
async def me(context: MeSession, request: Request):
    """Return the caller's identity, creating an anonymous session when absent."""
    settings = settings_of(request)
    body = _me_body(context, settings)
    response = JSONResponse(body)
    # Only a freshly created session gets a cookie; re-setting on every call would
    # shorten a signed-in user's cookie to the anonymous lifetime.
    if context.created and context.token is not None:
        set_session_cookie(
            response,
            settings,
            context.token,
            max_age=settings.anon_thread_ttl_hours * 3600,
        )
    return response


@router.post("/api/v1/turnstile/verify")
async def verify_human(body: TurnstileBody, request: Request, context: CsrfChecked):
    """Validate a Turnstile token and record the human-verification window on the session.

    Signed-in callers are never challenged. A refused or unreachable provider leaves the
    session unverified, so anonymous research stays blocked: this always fails closed.
    """
    settings = settings_of(request)
    repo = repository_of(request)
    if repo is None or not settings.turnstile_enabled:
        raise HTTPException(404, "Human verification is not configured")
    if context.kind != "anonymous" or human_verified(context):
        until = context.human_verified_until
        return {"human_verified_until": until.isoformat() if until else None}
    secret = settings.session_secret_bytes()
    ip = ratelimit.client_ip(request, settings)
    await ratelimit.enforce(
        repo,
        ratelimit.TURNSTILE_IP,
        ratelimit.ip_key(secret, ip),
        settings.rate_limit_turnstile_per_hour,
    )
    client: httpx.AsyncClient = request.app.state.http
    try:
        accepted = await turnstile.verify(client, settings, body.token, ip)
    except turnstile.TurnstileUnavailable:
        # Log the class only: provider messages can carry the token or secret.
        logger.warning("Turnstile unavailable request_id=%s", request.state.request_id)
        return _problem(
            request, 503, "turnstile_unavailable", "Human verification is temporarily unavailable"
        )
    if not accepted:
        return _problem(request, 403, "turnstile_failed", "Human verification failed")
    until = datetime.now(UTC) + timedelta(minutes=settings.turnstile_verified_minutes)
    if context.token_hash is None or not await repo.mark_human_verified(context.token_hash, until):
        raise AuthError(401, "session_required", "Reload the page to start a session")
    return {"human_verified_until": until.isoformat()}


@router.get("/auth/github/login")
async def github_login(request: Request, next: str = "/"):
    """Start the OAuth flow. Disabled providers report 404 rather than leaking state."""
    settings = settings_of(request)
    repo = repository_of(request)
    if repo is None or not settings.login_enabled:
        raise HTTPException(404, "GitHub sign-in is not configured")
    secret = settings.session_secret_bytes()
    await ratelimit.enforce(
        repo,
        ratelimit.LOGIN_IP,
        ratelimit.ip_key(secret, ratelimit.client_ip(request, settings)),
        settings.rate_limit_login_per_hour,
    )
    state = new_token()
    verifier = new_token()
    browser = new_token()
    await repo.create_oauth_state(
        token_hash(secret, state),
        verifier,
        safe_next_path(next),
        browser_hash(secret, browser),
    )
    response = RedirectResponse(
        github_oauth.authorize_url(settings, state, github_oauth.code_challenge(verifier)),
        status_code=302,
    )
    set_oauth_cookie(response, settings, browser, max_age=OAUTH_STATE_MAX_AGE)
    return response


@router.get("/auth/github/callback")
async def github_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Finish the flow. Every failure redirects without leaking the reason in the URL."""
    settings = settings_of(request)
    repo = repository_of(request)
    if repo is None or not settings.login_enabled:
        raise HTTPException(404, "GitHub sign-in is not configured")
    secret = settings.session_secret_bytes()
    browser = request.cookies.get("ra_oauth")
    if error or not code or not state or not browser:
        return RedirectResponse(AUTH_ERROR_REDIRECT, status_code=303)
    record = await repo.consume_oauth_state(
        token_hash(secret, state), browser_hash(secret, browser)
    )
    if record is None:
        return RedirectResponse(AUTH_ERROR_REDIRECT, status_code=303)
    client: httpx.AsyncClient = request.app.state.http
    try:
        token = await github_oauth.exchange_code(client, settings, code, record.code_verifier)
        profile = await github_oauth.fetch_user(client, token)
    except OAuthError:
        logger.warning("GitHub sign-in failed request_id=%s", request.state.request_id)
        return RedirectResponse(AUTH_ERROR_REDIRECT, status_code=303)
    existing = await optional_identity(request)
    session_token = new_token()
    result = await repo.login_github_user(
        profile,
        token_hash(secret, session_token),
        existing.identity if existing is not None else None,
    )
    response = RedirectResponse(record.next_path, status_code=303)
    set_session_cookie(
        response,
        settings,
        session_token,
        max_age=settings.user_session_max_days * 86400,
    )
    clear_oauth_cookie(response, settings)
    logger.info(
        "GitHub sign-in completed request_id=%s claimed_threads=%s",
        request.state.request_id,
        result.claimed_threads,
    )
    return response


@router.post("/auth/logout", status_code=204)
async def logout(context: CsrfChecked, request: Request):
    """Revoke the current session and clear the cookie."""
    settings = settings_of(request)
    repo = repository_of(request)
    if repo is not None and context.token_hash:
        await repo.revoke_session(context.token_hash)
    response = Response(status_code=204)
    clear_session_cookie(response, settings)
    return response


def login_url(next_path: str) -> str:
    return f"/auth/github/login?{urlencode({'next': next_path})}"
