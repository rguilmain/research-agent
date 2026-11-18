"""Production readiness gates: configuration checks, incompleteness and secret redaction."""

from unittest.mock import patch

import pytest
from pydantic import SecretStr, ValidationError

from research_agent import config
from research_agent.catalog import load_catalog
from research_agent.config import (
    INCOMPLETE_SECURITY_GATES,
    MIN_SESSION_SECRET_LENGTH,
    Settings,
    production_readiness_errors,
)

HOST = "research.example"
REAL_TURNSTILE_SECRET = "real-" + "s" * 20
DATABASE = "postgresql://user:secret@host/db"


def data():
    """Base configuration that satisfies every readiness item except the shipped-model policy."""
    return {
        "demo_mode": False,
        "database_url": SecretStr(DATABASE),
        "openai_api_key": SecretStr("openai-key"),
        "tavily_api_key": SecretStr("tavily-key"),
        "github_client_id": "client-id",
        "github_client_secret": SecretStr("client-secret"),
        "session_secret": SecretStr("s" * MIN_SESSION_SECRET_LENGTH),
        "public_base_url": f"https://{HOST}",
        "turnstile_site_key": "1x00000000000000000000AA",
        "turnstile_secret_key": SecretStr(REAL_TURNSTILE_SECRET),
    }


def valid():
    """The base configuration, validated the way the application builds it."""
    return Settings(app_env="test", **data())


def unvalidated(**overrides) -> Settings:
    """A settings object built without validation.

    Some gaps (a blank provider key, a short secret, a missing database) are refused by the
    settings validator, so this builds the object directly to assert what the readiness report
    says about them.
    """
    return Settings.model_construct(app_env="test", **{**data(), **overrides})


def plain(**overrides) -> Settings:
    """Minimal settings: only the fields the identity checks look at are set."""
    values = {
        **data(),
        "demo_mode": True,
        "database_url": SecretStr(""),
        "openai_api_key": SecretStr(""),
        "tavily_api_key": SecretStr(""),
        "github_client_id": "",
        "github_client_secret": SecretStr(""),
    }
    return Settings.model_construct(app_env="test", **{**values, **overrides})


def test_catalog_has_enabled_and_disabled_models():
    catalog = load_catalog()
    assert any(spec.enabled for spec in catalog.values())
    assert any(not spec.enabled for spec in catalog.values())


def test_identity_and_session_readiness_errors():
    errors = production_readiness_errors(plain())
    assert "GITHUB_CLIENT_ID is not configured" in errors
    assert "GITHUB_CLIENT_SECRET is not configured" in errors
    invalid = production_readiness_errors(
        plain(public_base_url="http://research.example", github_client_id="id")
    )
    assert "PUBLIC_BASE_URL must use https for production cookies" in invalid


def test_short_session_secret_is_reported_without_echoing_the_value():
    # 31 characters is long enough to pass the field validator but below the launch minimum.
    secret = "x" * (MIN_SESSION_SECRET_LENGTH - 1)
    errors = production_readiness_errors(unvalidated(session_secret=SecretStr(secret)))
    assert any("SESSION_SECRET must be at least" in item for item in errors)
    assert all(secret not in item for item in errors)


def test_turnstile_readiness_rejects_missing_and_test_keys():
    assert (
        "TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY are not configured"
        in production_readiness_errors(
            plain(turnstile_site_key="", turnstile_secret_key=SecretStr(""))
        )
    )
    assert "TURNSTILE_SECRET_KEY is a Cloudflare test key" in production_readiness_errors(
        plain(turnstile_secret_key=SecretStr("1x0000000000000000000000000000000AA"))
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"demo_mode": True},
        {"database_url": SecretStr("")},
        {"openai_api_key": SecretStr("")},
        {"public_base_url": "https://localhost"},
    ],
)
def test_production_configuration_gaps_are_reported(overrides):
    assert production_readiness_errors(unvalidated(**overrides))


def test_complete_configuration_clears_the_checklist():
    assert production_readiness_errors(valid()) == []


def test_model_policy_requires_an_enabled_default_and_anonymous_option():
    assert "DEFAULT_MODEL must name an enabled model" in production_readiness_errors(
        unvalidated(default_model="gpt-6.1-terra")
    )
    # A catalog where every enabled model is user-only leaves anonymous callers nothing.
    catalog = {
        model_id: spec.model_copy(update={"anonymous": False})
        for model_id, spec in load_catalog().items()
    }
    with patch("research_agent.config.load_catalog", return_value=catalog):
        assert "No anonymous-eligible model is enabled" in production_readiness_errors(valid())


def test_unimplemented_gates_keep_production_blocked_for_a_complete_configuration():
    # Every configuration item passes here, so the only reason to refuse is the unimplemented
    # security work. The block is an explicit code guard, not data an operator can work
    # around, so reassigning the public tuple must not unlock production.
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(config, "INCOMPLETE_SECURITY_GATES", ())
        with pytest.raises(ValidationError) as exc:
            Settings(app_env="production", **data())
    finally:
        monkeypatch.undo()
    message = str(exc.value)
    assert "security launch gates" in message
    assert "backup and restore have not been tested" in message
    assert "acceptance tests" in message
    # Secret values must never appear in a startup error.
    for key in ("openai_api_key", "tavily_api_key", "github_client_secret", "session_secret"):
        assert data()[key].get_secret_value() not in message


def test_production_block_message_lists_the_unimplemented_gates():
    with pytest.raises(ValidationError) as exc:
        Settings(app_env="production", **data())
    message = str(exc.value)
    # The documented list and the inline refusal text must stay in step.
    for gate in INCOMPLETE_SECURITY_GATES:
        assert gate in message


def test_incomplete_gate_list_matches_the_inline_refusal():
    # The refusal is hard-coded in the validator, so this guards the duplication.
    with pytest.raises(ValidationError) as exc:
        Settings(app_env="production", **data())
    message = str(exc.value)
    assert len(INCOMPLETE_SECURITY_GATES) == 2
    assert "backup and restore" in message and "acceptance tests" in message


def test_incomplete_gate_list_names_backup_and_acceptance_work():
    joined = " ".join(INCOMPLETE_SECURITY_GATES)
    assert "backup" in joined
    assert "acceptance" in joined
