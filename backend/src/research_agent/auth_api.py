"""Identity endpoints: lazy anonymous sessions, GitHub OAuth and logout.

Only ``GET /api/v1/me`` creates a session. Every other endpoint requires one or
rejects the request, so anonymous rows appear only when a browser asks for identity.
"""

import logging
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from research_agent import github_oauth, ratelimit
from research_agent.auth import (
    CsrfChecked,
    MeSession,
    browser_hash,
    clear_oauth_cookie,
    clear_session_cookie,
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


def _me_body(context, settings) -> dict:
    user = None
    if context.kind == "user" and context.login:
        user = {"login": context.login, "avatar_url": context.avatar_url}
    return {
        "kind": context.kind,
        "user": user,
        "csrf_token": context.csrf_token,
        "login_enabled": settings.login_enabled,
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
