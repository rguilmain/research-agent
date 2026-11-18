"""Turnstile validation exercised offline; no real Cloudflare request is made."""

import httpx
import pytest
import respx
from pydantic import SecretStr, ValidationError

from research_agent import turnstile
from research_agent.config import Settings, production_readiness_errors

PASS_SECRET = "1x0000000000000000000000000000000AA"
FAIL_SECRET = "2x0000000000000000000000000000000AA"
REAL_SECRET = "real-" + "s" * 20
DUMMY_TOKEN = "XXXX.DUMMY.TOKEN.XXXX"
HOST = "research.example"


def settings(**overrides) -> Settings:
    values = {
        "app_env": "test",
        "session_secret": SecretStr("s" * 40),
        "public_base_url": f"https://{HOST}",
        "turnstile_site_key": "1x00000000000000000000AA",
        "turnstile_secret_key": SecretStr(PASS_SECRET),
    }
    values.update(overrides)
    return Settings(**values)


def route_success(**fields) -> respx.Route:
    body = {"success": True, "challenge_ts": "2026-11-17T15:14:30.096Z", "error-codes": []}
    body.update(fields)
    return respx.post(turnstile.SITEVERIFY_URL).mock(return_value=httpx.Response(200, json=body))


def route_failure(code: str = "invalid-input-response") -> respx.Route:
    return respx.post(turnstile.SITEVERIFY_URL).mock(
        return_value=httpx.Response(200, json={"success": False, "error-codes": [code]})
    )


@pytest.fixture
async def client():
    async with httpx.AsyncClient(follow_redirects=False) as http:
        yield http


async def test_test_key_success_sends_bounded_form(client):
    config = settings()
    with respx.mock:
        route = route_success(hostname="example.com")
        assert await turnstile.verify(client, config, DUMMY_TOKEN, "198.51.100.7") is True
    request = route.calls.last.request
    form = dict(httpx.QueryParams(request.content.decode()))
    assert form["secret"] == PASS_SECRET
    assert form["response"] == DUMMY_TOKEN
    assert form["remoteip"] == "198.51.100.7"
    assert form["idempotency_key"]
    assert len(form) == 4


async def test_unknown_client_is_not_sent_as_remoteip(client):
    with respx.mock:
        route = route_success(hostname="example.com")
        assert await turnstile.verify(client, settings(), DUMMY_TOKEN, "unknown") is True
    assert "remoteip" not in httpx.QueryParams(route.calls.last.request.content.decode())


async def test_refusals_and_spent_tokens_are_not_accepted(client):
    for code in ("invalid-input-response", "timeout-or-duplicate"):
        with respx.mock:
            route_failure(code)
            assert await turnstile.verify(client, settings(), DUMMY_TOKEN, None) is False


async def test_real_secret_requires_matching_host_and_action(client):
    config = settings(turnstile_secret_key=SecretStr(REAL_SECRET))
    with respx.mock:
        route_success(hostname=HOST, action=turnstile.ACTION)
        assert await turnstile.verify(client, config, "a-real-token", None) is True
    with respx.mock:
        route_success(hostname="attacker.example", action=turnstile.ACTION)
        assert await turnstile.verify(client, config, "a-real-token", None) is False
    with respx.mock:
        route_success(hostname=HOST)
        assert await turnstile.verify(client, config, "a-real-token", None) is False
    with respx.mock:
        route_success(hostname=HOST, action="login")
        assert await turnstile.verify(client, config, "a-real-token", None) is False


@pytest.mark.parametrize(
    "token",
    ["", "x" * (turnstile.MAX_TOKEN_CHARS + 1), "has space", "has\nnewline"],
)
async def test_unusable_tokens_never_reach_the_provider(client, token):
    with respx.mock:
        respx.post(turnstile.SITEVERIFY_URL).mock(return_value=httpx.Response(200, json={}))
        assert await turnstile.verify(client, settings(), token, None) is False
        assert not respx.calls


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"success": False}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=["unexpected"]),
        httpx.Response(200, text="x" * (turnstile.MAX_RESPONSE_BYTES + 1)),
    ],
)
async def test_unusable_provider_replies_fail_closed(client, response):
    with respx.mock:
        respx.post(turnstile.SITEVERIFY_URL).mock(return_value=response)
        with pytest.raises(turnstile.TurnstileUnavailable):
            await turnstile.verify(client, settings(), DUMMY_TOKEN, None)


async def test_transient_failure_is_retried_once_with_the_same_key(client):
    with respx.mock:
        route = respx.post(turnstile.SITEVERIFY_URL).mock(
            side_effect=[
                httpx.ConnectError("boom"),
                httpx.Response(200, json={"success": True, "hostname": "example.com"}),
            ]
        )
        assert await turnstile.verify(client, settings(), DUMMY_TOKEN, None) is True
    keys = [
        httpx.QueryParams(call.request.content.decode())["idempotency_key"] for call in route.calls
    ]
    assert len(keys) == 2 and keys[0] == keys[1]


async def test_repeated_transport_failure_is_unavailable(client):
    with respx.mock:
        route = respx.post(turnstile.SITEVERIFY_URL).mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(turnstile.TurnstileUnavailable):
            await turnstile.verify(client, settings(), DUMMY_TOKEN, None)
    assert len(route.calls) == turnstile.ATTEMPTS


async def test_failures_never_leak_the_secret_or_token(client, caplog):
    caplog.set_level("DEBUG")
    config = settings(turnstile_secret_key=SecretStr(REAL_SECRET))
    with respx.mock:
        respx.post(turnstile.SITEVERIFY_URL).mock(return_value=httpx.Response(500, text="denied"))
        with pytest.raises(turnstile.TurnstileUnavailable) as excinfo:
            await turnstile.verify(client, config, DUMMY_TOKEN, None)
    leaked = "".join(
        (
            str(excinfo.value),
            repr(excinfo.value.__cause__),
            caplog.text,
        )
    )
    assert REAL_SECRET not in leaked
    assert DUMMY_TOKEN not in leaked


def test_keys_must_be_configured_together():
    with pytest.raises(ValidationError):
        Settings(app_env="test", turnstile_site_key="1x00000000000000000000AA")
    with pytest.raises(ValidationError):
        Settings(app_env="test", turnstile_secret_key=SecretStr(PASS_SECRET))


@pytest.mark.parametrize("minutes", [0, 61])
def test_verified_window_is_bounded(minutes):
    with pytest.raises(ValidationError):
        settings(turnstile_verified_minutes=minutes)


def test_readiness_reports_missing_and_test_keys():
    assert "TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY are not configured" in (
        production_readiness_errors(Settings(app_env="test"))
    )
    assert "TURNSTILE_SECRET_KEY is a Cloudflare test key" in (
        production_readiness_errors(settings())
    )
    assert "TURNSTILE_SECRET_KEY is a Cloudflare test key" not in (
        production_readiness_errors(settings(turnstile_secret_key=SecretStr(REAL_SECRET)))
    )


def test_default_verify_endpoint_is_cloudflare():
    assert settings().turnstile_verify_url == turnstile.SITEVERIFY_URL
    assert turnstile.SITEVERIFY_URL.startswith("https://challenges.cloudflare.com/")


async def test_overridden_endpoint_is_actually_used(client):
    # Development/test may point at a local stub so the browser suite never calls Cloudflare.
    stub = "http://127.0.0.1:8124/turnstile/v0/siteverify"
    config = settings(turnstile_verify_url=stub)
    with respx.mock:
        route = respx.post(stub).mock(
            return_value=httpx.Response(200, json={"success": True, "hostname": "example.com"})
        )
        assert await turnstile.verify(client, config, DUMMY_TOKEN, None) is True
    assert route.called


def test_readiness_refuses_a_substituted_verify_endpoint():
    # A swapped endpoint would mark every caller as verified, so production must refuse it.
    assert "TURNSTILE_VERIFY_URL must be the Cloudflare siteverify endpoint" in (
        production_readiness_errors(settings(turnstile_verify_url="http://127.0.0.1:8124/x"))
    )
    assert "TURNSTILE_VERIFY_URL must be the Cloudflare siteverify endpoint" not in (
        production_readiness_errors(settings())
    )


@pytest.mark.parametrize("url", ["not-a-url", "challenges.cloudflare.com", "/relative"])
def test_verify_endpoint_must_be_absolute(url):
    with pytest.raises(ValidationError, match="TURNSTILE_VERIFY_URL must be an absolute"):
        Settings(app_env="test", turnstile_verify_url=url)
