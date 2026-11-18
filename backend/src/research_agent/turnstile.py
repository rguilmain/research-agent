"""Cloudflare Turnstile server-side validation.

Tokens, secrets and provider bodies are never logged or stored. A validation failure or an
unreachable provider both mean "not verified": callers must fail closed.
"""

import json
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from research_agent import ratelimit
from research_agent.config import TURNSTILE_SITEVERIFY_URL, TURNSTILE_TEST_SECRETS, Settings

SITEVERIFY_URL = TURNSTILE_SITEVERIFY_URL
MAX_TOKEN_CHARS = 2048
MAX_RESPONSE_BYTES = 8_000
TIMEOUT_S = 10
ATTEMPTS = 2
ACTION = "research"


class TurnstileUnavailable(Exception):
    """Provider unreachable or answered unusably. Callers must refuse the request."""


class TurnstileRequired(Exception):
    """Anonymous research attempted without a current human verification."""


def _expected_hostname(settings: Settings) -> str | None:
    return urlsplit(settings.public_base_url).hostname


def _parse(body: bytes) -> dict:
    if len(body) > MAX_RESPONSE_BYTES:
        raise TurnstileUnavailable("Turnstile response too large")
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise TurnstileUnavailable("Turnstile returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise TurnstileUnavailable("Turnstile returned an unexpected body")
    return data


async def _siteverify(
    client: httpx.AsyncClient, settings: Settings, token: str, ip: str | None
) -> dict:
    """One bounded siteverify call, retried once with the same idempotency key."""
    data = {
        "secret": settings.turnstile_secret_key.get_secret_value(),
        "response": token,
        "idempotency_key": str(uuid4()),
    }
    if ip and ip != ratelimit.UNKNOWN_CLIENT:
        data["remoteip"] = ip
    for attempt in range(ATTEMPTS):
        try:
            async with client.stream(
                "POST",
                settings.turnstile_verify_url,
                data=data,
                timeout=TIMEOUT_S,
                follow_redirects=False,
            ) as response:
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=4096):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise TurnstileUnavailable("Turnstile response too large")
            if response.status_code >= 400:
                raise TurnstileUnavailable("Turnstile validation failed")
            return _parse(bytes(body))
        except httpx.TransportError as exc:
            # Retrying is safe because Cloudflare treats the idempotency key as a replay guard.
            if attempt + 1 >= ATTEMPTS:
                raise TurnstileUnavailable("Turnstile validation unavailable") from exc
    raise TurnstileUnavailable("Turnstile validation unavailable")


def _acceptable(data: dict, settings: Settings) -> bool:
    if data.get("success") is not True:
        return False
    # Cloudflare's test secrets always report hostname "example.com" and omit "action",
    # so those two checks are only meaningful with a real secret key.
    if settings.turnstile_secret_key.get_secret_value() in TURNSTILE_TEST_SECRETS:
        return True
    return data.get("hostname") == _expected_hostname(settings) and data.get("action") == ACTION


async def verify(
    client: httpx.AsyncClient, settings: Settings, token: str, remote_ip: str | None
) -> bool:
    """True only for a successful validation from the expected host and action."""
    if not token or len(token) > MAX_TOKEN_CHARS or any(char.isspace() for char in token):
        return False
    return _acceptable(await _siteverify(client, settings, token, remote_ip), settings)
