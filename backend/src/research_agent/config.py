"""Settings with no network or import-time initialization."""

from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


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
        return self
