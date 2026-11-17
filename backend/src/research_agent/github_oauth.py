"""GitHub OAuth with PKCE. The access token is used once and never stored or logged."""

import base64
import hashlib
import json
from urllib.parse import urlencode

import httpx

from research_agent.config import Settings
from research_agent.repository import GitHubProfile

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"
MAX_RESPONSE_BYTES = 64_000
AVATAR_PREFIX = "https://avatars.githubusercontent.com/"


class OAuthError(Exception):
    """Provider failure. Messages never contain codes, tokens or URLs."""


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def redirect_uri(settings: Settings) -> str:
    return f"{settings.public_origin}/auth/github/callback"


def authorize_url(settings: Settings, state: str, challenge: str) -> str:
    """Fixed endpoint, S256 PKCE and no scopes: public profile only."""
    query = urlencode(
        {
            "client_id": settings.github_client_id,
            "redirect_uri": redirect_uri(settings),
            "scope": "",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


async def _bounded_json(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> object:
    async with client.stream(method, url, follow_redirects=False, **kwargs) as response:
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=8192):
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise OAuthError("GitHub response too large")
        try:
            return json.loads(body)
        except ValueError as exc:
            raise OAuthError("GitHub returned invalid JSON") from exc


async def exchange_code(
    client: httpx.AsyncClient, settings: Settings, code: str, verifier: str
) -> str:
    """Exchange the authorization code; GitHub returns HTTP 200 with an error field."""
    try:
        data = await _bounded_json(
            client,
            "POST",
            TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret.get_secret_value(),
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri(settings),
            },
            timeout=15,
        )
    except (httpx.HTTPError, OAuthError) as exc:
        raise OAuthError("GitHub token exchange failed") from exc
    if not isinstance(data, dict) or data.get("error") or not data.get("access_token"):
        raise OAuthError("GitHub token exchange failed")
    token = data["access_token"]
    if not isinstance(token, str):
        raise OAuthError("GitHub token exchange failed")
    return token


async def fetch_user(client: httpx.AsyncClient, token: str) -> GitHubProfile:
    """Read the public profile once; the caller must discard the token afterwards."""
    try:
        data = await _bounded_json(
            client,
            "GET",
            USER_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15,
        )
    except (httpx.HTTPError, OAuthError) as exc:
        raise OAuthError("GitHub profile request failed") from exc
    if not isinstance(data, dict):
        raise OAuthError("GitHub profile request failed")
    github_id = data.get("id")
    login = data.get("login")
    avatar = data.get("avatar_url")
    if isinstance(github_id, bool) or not isinstance(github_id, int) or not isinstance(login, str):
        raise OAuthError("GitHub profile request failed")
    if not login.strip():
        raise OAuthError("GitHub profile request failed")
    safe_avatar = avatar if isinstance(avatar, str) and avatar.startswith(AVATAR_PREFIX) else None
    return GitHubProfile(
        github_id=github_id,
        login=login.strip()[:64],
        avatar_url=safe_avatar[:500] if safe_avatar else None,
    )
