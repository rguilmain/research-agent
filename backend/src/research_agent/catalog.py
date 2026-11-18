"""Server-owned model and paid-tool allowlist."""

import tomllib
from decimal import ROUND_CEILING, Decimal
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MICRO = Decimal("0.000001")
ONE_MTOK = Decimal(1_000_000)


class ModelSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    label: str
    input_usd_per_mtok: Decimal = Field(ge=0)
    output_usd_per_mtok: Decimal = Field(ge=0)
    cached_input_usd_per_mtok: Decimal | None = Field(default=None, ge=0)
    enabled: bool
    # Daily quota units charged per question; a policy weight, not a cost estimate.
    quota_weight: int = Field(default=1, ge=1, le=100)
    # Anonymous callers may select only models marked anonymous.
    anonymous: bool = False


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


class ToolPrice(BaseModel):
    """A paid tool's flat per-call price in USD, or zero for a free tool."""

    model_config = ConfigDict(frozen=True)
    id: Literal["tavily_search", "wikipedia", "wolfram_alpha"]
    usd_per_call: Decimal = Field(ge=0)

    @field_validator("usd_per_call")
    @classmethod
    def rounded_up_to_micro(cls, value: Decimal) -> Decimal:
        return ceil_usd(value)


def load_tool_prices() -> dict[str, Decimal]:
    """Per-call USD prices for known tools. A tool absent here is never offered live."""
    data = tomllib.loads(files("research_agent").joinpath("models.toml").read_text("utf-8"))
    prices = [ToolPrice.model_validate(entry) for entry in data.get("tools", [])]
    if len({price.id for price in prices}) != len(prices):
        raise ValueError("Duplicate tool identifiers")
    return {price.id: price.usd_per_call for price in prices}


def ceil_usd(value: Decimal) -> Decimal:
    """Round money up: a reservation must never be smaller than a possible charge."""
    return value.quantize(MICRO, rounding=ROUND_CEILING)


def conservative_model_cost(
    model: ModelSpec,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> Decimal:
    """Settlement estimate. An unverified cached price bills cached tokens as normal input.

    Reasoning tokens are already part of output_tokens. No tool costs are included.
    """
    if not 0 <= cached_input_tokens <= input_tokens or output_tokens < 0:
        raise ValueError("Invalid usage totals")
    billable = (input_tokens - cached_input_tokens) * model.input_usd_per_mtok
    cached_price = model.cached_input_usd_per_mtok
    billable += cached_input_tokens * (
        cached_price if cached_price is not None else model.input_usd_per_mtok
    )
    billable += output_tokens * model.output_usd_per_mtok
    return ceil_usd(billable / ONE_MTOK)


def model_call_reservation(
    model: ModelSpec, *, input_tokens: int, output_tokens: int, attempts: int
) -> Decimal:
    """Worst-case reservation: all input uncached, the full output allowance, every try billed."""
    if input_tokens < 0 or output_tokens < 0 or attempts < 1:
        raise ValueError("Invalid reservation inputs")
    per_attempt = (
        input_tokens * model.input_usd_per_mtok + output_tokens * model.output_usd_per_mtok
    ) / ONE_MTOK
    return ceil_usd(per_attempt * attempts)
