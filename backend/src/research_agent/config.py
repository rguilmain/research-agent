"""Settings with no network or import-time initialization."""

from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
            )
            <= 0
        ):
            raise ValueError("Execution limits must be positive")
        return self
