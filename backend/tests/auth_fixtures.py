"""Session and CSRF helpers shared by API tests.

Unsafe requests must prove same-origin and carry the session's CSRF token, so tests
build those headers explicitly instead of disabling the checks in the application.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient

from research_agent.auth import csrf_token, new_token, session_cookie_name, token_hash

# Default ``PUBLIC_BASE_URL`` in development and test; ``enforce_origin`` accepts it.
TEST_ORIGIN = "http://127.0.0.1:5173"


def origin_headers() -> dict[str, str]:
    """Headers for unsafe requests when no session row exists (demo mode)."""
    return {"origin": TEST_ORIGIN}


def start_session(client: TestClient) -> str:
    """Create an anonymous session and return its CSRF token.

    ``GET /api/v1/me`` also stores the session cookie in the client's cookie jar, so
    later requests only need the origin and CSRF headers.
    """
    response = client.get("/api/v1/me")
    assert response.status_code == 200
    token = response.json()["csrf_token"]
    assert token
    return token


def session_headers(client: TestClient) -> dict[str, str]:
    """Origin plus CSRF headers for a freshly created anonymous session."""
    return {"origin": TEST_ORIGIN, "x-csrf-token": start_session(client)}


@contextmanager
def signed_in_client(app) -> Iterator[TestClient]:
    """A ``TestClient`` whose default headers carry an anonymous session's CSRF token."""
    with TestClient(app, headers=origin_headers()) as client:
        client.headers["x-csrf-token"] = start_session(client)
        yield client


async def raw_session_headers(app) -> dict[str, str]:
    """Session cookie and CSRF headers for raw ASGI scopes without an HTTP client."""
    settings = app.state.settings
    secret = settings.session_secret_bytes()
    token = new_token()
    record = await app.state.repository.create_anonymous_session(token_hash(secret, token))
    return {
        "origin": TEST_ORIGIN,
        "x-csrf-token": csrf_token(secret, record.token_hash),
        "cookie": f"{session_cookie_name(settings)}={token}",
    }
