"""Settings with no network or import-time initialization."""

import logging
import secrets
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from research_agent.catalog import load_tool_prices

logger = logging.getLogger(__name__)

# Development-only fallback so sessions keep working within one process without a secret.
# Production readiness rejects an unset SESSION_SECRET, so this key never ships.
EPHEMERAL_SESSION_KEY = secrets.token_bytes(32)
MIN_SESSION_SECRET_LENGTH = 32
MAX_TURNSTILE_VERIFIED_MINUTES = 60
_ephemeral_warned = False

# Cloudflare's public always-pass/always-fail/already-spent keys, documented for local testing.
# They prove nothing, so production readiness rejects them.
TURNSTILE_TEST_SECRETS = frozenset(
    {
        "1x0000000000000000000000000000000AA",
        "2x0000000000000000000000000000000AA",
        "3x0000000000000000000000000000000AA",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    app_env: Literal["development", "test", "production"] = "development"
    demo_mode: bool = True
    default_model: str = "gpt-6-luna"
    openai_api_key: SecretStr = SecretStr("")
    tavily_api_key: SecretStr = SecretStr("")
    wolfram_alpha_appid: SecretStr = SecretStr("")
    host: str = "127.0.0.1"
    port: int = 8000
    max_model_calls: int = 8
    max_tool_calls: int = 12
    run_timeout_s: float = 120
    max_output_tokens: int = 4096
    max_tool_input_chars: int = 400
    max_tool_output_chars: int = 6000
    max_parallel_tool_calls: int = 3
    max_context_tokens: int = 24_000
    max_run_input_tokens: int = 120_000
    database_url: SecretStr = SecretStr("")
    thread_retention_days: int = 30
    anon_thread_ttl_hours: int = 24
    empty_thread_ttl_hours: int = 1
    # Minutes between in-process retention cleanup passes; 0 disables the scheduler.
    cleanup_interval_minutes: int = 15
    public_base_url: str = "http://127.0.0.1:5173"
    github_client_id: str = ""
    github_client_secret: SecretStr = SecretStr("")
    session_secret: SecretStr = SecretStr("")
    user_session_idle_days: int = 7
    user_session_max_days: int = 30
    trust_proxy_headers: bool = False
    rate_limit_anon_session_per_hour: int = 30
    rate_limit_research_per_hour: int = 30
    rate_limit_login_per_hour: int = 10
    quota_anon_daily: int = 3
    quota_user_daily: int = 25
    turnstile_site_key: str = ""
    turnstile_secret_key: SecretStr = SecretStr("")
    turnstile_verified_minutes: int = 60
    rate_limit_turnstile_per_hour: int = 30
    global_daily_budget_usd: Decimal = Decimal("5")
    budget_reservation_ttl_minutes: int = 10
    max_active_runs: int = 20
    max_active_runs_per_principal: int = 2

    @property
    def login_enabled(self) -> bool:
        return bool(self.github_client_id and self.github_client_secret.get_secret_value())

    @property
    def turnstile_enabled(self) -> bool:
        """Anonymous admission is protected only when both keys are configured."""
        return bool(self.turnstile_site_key and self.turnstile_secret_key.get_secret_value())

    @property
    def secure_cookies(self) -> bool:
        return self.public_base_url.startswith("https://")

    @property
    def public_origin(self) -> str:
        """Scheme plus host plus port, used for the Origin header comparison."""
        parts = urlsplit(self.public_base_url)
        return f"{parts.scheme}://{parts.netloc}"

    def session_secret_bytes(self) -> bytes:
        """Configured secret, or a process-local fallback in development and tests."""
        global _ephemeral_warned
        raw = self.session_secret.get_secret_value()
        if raw:
            return raw.encode()
        if not _ephemeral_warned:
            _ephemeral_warned = True
            logger.warning(
                "SESSION_SECRET is not configured; using a process-local secret. "
                "Sessions will not survive a restart."
            )
        return EPHEMERAL_SESSION_KEY

    @model_validator(mode="after")
    def validate_database(self) -> "Settings":
        raw = self.database_url.get_secret_value()
        if raw:
            try:
                url = make_url(raw)
                valid = url.drivername in {"postgresql", "postgresql+psycopg"} and bool(
                    url.host and url.database
                )
            except Exception:
                valid = False
            if not valid:
                raise ValueError("DATABASE_URL must be a PostgreSQL URL with host and database")
        if (
            min(self.thread_retention_days, self.anon_thread_ttl_hours, self.empty_thread_ttl_hours)
            <= 0
        ):
            raise ValueError("Retention limits must be positive")
        if not 0 <= self.cleanup_interval_minutes <= 1440:
            raise ValueError("CLEANUP_INTERVAL_MINUTES must be between 0 and 1440")
        if self.user_session_idle_days <= 0 or self.user_session_max_days <= 0:
            raise ValueError("Session lifetimes must be positive")
        if self.user_session_idle_days > self.user_session_max_days:
            raise ValueError("Idle session lifetime cannot exceed the absolute lifetime")
        parts = urlsplit(self.public_base_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("PUBLIC_BASE_URL must be an absolute http(s) URL")
        if parts.query or parts.fragment:
            raise ValueError("PUBLIC_BASE_URL must not contain a query or fragment")
        secret = self.session_secret.get_secret_value()
        if secret and len(secret) < MIN_SESSION_SECRET_LENGTH:
            raise ValueError(
                f"SESSION_SECRET must be at least {MIN_SESSION_SECRET_LENGTH} characters"
            )
        return self

    @model_validator(mode="after")
    def validate_runtime(self) -> "Settings":
        if self.app_env == "production":
            raise ValueError("Public deployment blocked until security launch gates pass")
        if not self.demo_mode and not (
            self.openai_api_key.get_secret_value() and self.tavily_api_key.get_secret_value()
        ):
            raise ValueError("Live research requires OPENAI_API_KEY and TAVILY_API_KEY")
        if (
            min(
                self.max_model_calls,
                self.max_tool_calls,
                self.run_timeout_s,
                self.max_output_tokens,
                self.max_tool_input_chars,
                self.max_tool_output_chars,
                self.max_parallel_tool_calls,
                self.max_context_tokens,
                self.max_run_input_tokens,
            )
            <= 0
        ):
            raise ValueError("Execution limits must be positive")
        if (
            min(
                self.rate_limit_anon_session_per_hour,
                self.rate_limit_research_per_hour,
                self.rate_limit_login_per_hour,
                self.rate_limit_turnstile_per_hour,
            )
            <= 0
        ):
            raise ValueError("Rate limits must be positive")
        if min(self.quota_anon_daily, self.quota_user_daily) <= 0:
            raise ValueError("Daily quotas must be positive")
        site = bool(self.turnstile_site_key)
        secret = bool(self.turnstile_secret_key.get_secret_value())
        if site != secret:
            raise ValueError("TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY must be set together")
        if not 1 <= self.turnstile_verified_minutes <= MAX_TURNSTILE_VERIFIED_MINUTES:
            raise ValueError(
                f"TURNSTILE_VERIFIED_MINUTES must be between 1 and {MAX_TURNSTILE_VERIFIED_MINUTES}"
            )
        if self.global_daily_budget_usd <= 0:
            raise ValueError("GLOBAL_DAILY_BUDGET_USD must be positive")
        if self.budget_reservation_ttl_minutes * 60 <= self.run_timeout_s:
            # An open reservation must outlive the run that owns it, or a settlement race
            # would double-charge the day: expiry moves the reserved amount into spent.
            raise ValueError("BUDGET_RESERVATION_TTL_MINUTES must exceed RUN_TIMEOUT_S")
        if min(self.max_active_runs, self.max_active_runs_per_principal) <= 0:
            raise ValueError("Concurrency limits must be positive")
        if self.max_active_runs_per_principal > self.max_active_runs:
            raise ValueError("MAX_ACTIVE_RUNS_PER_PRINCIPAL cannot exceed MAX_ACTIVE_RUNS")
        return self


def production_readiness_errors(settings: Settings) -> list[str]:
    """Identity/session prerequisites still missing before public launch.

    Reported as data rather than raised so operators see every gap at once. Production
    startup stays unconditionally blocked until the remaining security gates land.
    """
    errors: list[str] = []
    if not settings.github_client_id:
        errors.append("GITHUB_CLIENT_ID is not configured")
    if not settings.github_client_secret.get_secret_value():
        errors.append("GITHUB_CLIENT_SECRET is not configured")
    if not settings.session_secret.get_secret_value():
        errors.append("SESSION_SECRET is not configured")
    if not settings.secure_cookies:
        errors.append("PUBLIC_BASE_URL must use https for production cookies")
    if not settings.turnstile_enabled:
        errors.append("TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY are not configured")
    elif settings.turnstile_secret_key.get_secret_value() in TURNSTILE_TEST_SECRETS:
        errors.append("TURNSTILE_SECRET_KEY is a Cloudflare test key")
    if settings.wolfram_alpha_appid.get_secret_value() and "wolfram_alpha" not in (
        load_tool_prices()
    ):
        errors.append("WOLFRAM_ALPHA_APPID is configured without a price in models.toml")
    return errors
