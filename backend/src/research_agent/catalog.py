"""Server-owned model allowlist."""

import tomllib
from decimal import Decimal
from importlib.resources import files

from pydantic import BaseModel, ConfigDict, Field


class ModelSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    label: str
    input_usd_per_mtok: Decimal = Field(ge=0)
    output_usd_per_mtok: Decimal = Field(ge=0)
    cached_input_usd_per_mtok: Decimal | None = Field(default=None, ge=0)
    enabled: bool


def estimate_model_cost(
    model: ModelSpec, *, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0
) -> Decimal:
    """Informational only; fail closed when a cached-input price is unavailable.

    Reasoning tokens are already included in output_tokens. No tool costs included.
    """
    if not 0 <= cached_input_tokens <= input_tokens or output_tokens < 0:
        raise ValueError("Invalid usage totals")
    if cached_input_tokens and model.cached_input_usd_per_mtok is None:
        raise ValueError("Cached-input price is unverified")
    return (
        (input_tokens - cached_input_tokens) * model.input_usd_per_mtok
        + cached_input_tokens * (model.cached_input_usd_per_mtok or Decimal(0))
        + output_tokens * model.output_usd_per_mtok
    ) / Decimal(1_000_000)


def load_catalog() -> dict[str, ModelSpec]:
    data = tomllib.loads(files("research_agent").joinpath("models.toml").read_text("utf-8"))
    models = [ModelSpec.model_validate(entry) for entry in data["models"]]
    if len({model.id for model in models}) != len(models):
        raise ValueError("Duplicate model identifiers")
    return {model.id: model for model in models}
