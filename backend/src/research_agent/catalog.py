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
    enabled: bool


def load_catalog() -> dict[str, ModelSpec]:
    data = tomllib.loads(files("research_agent").joinpath("models.toml").read_text("utf-8"))
    models = [ModelSpec.model_validate(entry) for entry in data["models"]]
    if len({model.id for model in models}) != len(models):
        raise ValueError("Duplicate model identifiers")
    return {model.id: model for model in models}
