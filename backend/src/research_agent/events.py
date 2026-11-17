"""Strict public SSE payloads. Never serialize unvalidated provider dictionaries."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from research_agent.tools import safe_url

ShortText = Annotated[str, Field(min_length=1, max_length=200)]
TokenCount = Annotated[int, Field(ge=0, le=1_000_000_000)]


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RunStarted(Event):
    type: Literal["run_started"]
    model_id: ShortText
    demo: bool


class Token(Event):
    type: Literal["token"]
    text: Annotated[str, Field(min_length=1, max_length=32_000)]


class ThreadEvent(Event):
    type: Literal["thread"]
    thread_id: Annotated[str, Field(pattern=r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")]


class Step(Event):
    type: Literal["step"]
    tool: Literal["tavily_search", "wikipedia", "wolfram_alpha"]
    status: Literal["started", "completed"]


class Source(Event):
    url: Annotated[str, Field(max_length=2000)]
    title: ShortText

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if not safe_url(value):
            raise ValueError("Invalid citation URL")
        return value


class Sources(Event):
    type: Literal["sources"]
    items: Annotated[list[Source], Field(max_length=100)]


class Usage(Event):
    type: Literal["usage"]
    input_tokens: TokenCount
    output_tokens: TokenCount
    total_tokens: TokenCount
    cached_input_tokens: TokenCount
    reasoning_tokens: TokenCount
    model_calls: TokenCount
    tool_calls: dict[Literal["tavily_search", "wikipedia", "wolfram_alpha"], TokenCount]
    estimated: bool

    @model_validator(mode="after")
    def validate_totals(self) -> "Usage":
        if (
            self.total_tokens != self.input_tokens + self.output_tokens
            or self.cached_input_tokens > self.input_tokens
            or self.reasoning_tokens > self.output_tokens
        ):
            raise ValueError("Invalid usage totals")
        return self


class Quota(Event):
    type: Literal["quota"]
    limit: Annotated[int, Field(ge=1, le=1_000_000)]
    remaining: Annotated[int, Field(ge=0, le=1_000_000)]

    @model_validator(mode="after")
    def validate_remaining(self) -> "Quota":
        if self.remaining > self.limit:
            raise ValueError("Invalid quota allowance")
        return self


class Done(Event):
    type: Literal["done"]


class Error(Event):
    type: Literal["error"]
    code: Literal["research_failed", "research_timeout", "run_limit_exceeded"]
    message: ShortText
    request_id: ShortText


EVENT_ADAPTER = TypeAdapter(
    Annotated[
        RunStarted | ThreadEvent | Token | Step | Sources | Usage | Quota | Done | Error,
        Field(discriminator="type"),
    ]
)


def validate_event(value: object) -> dict:
    return EVENT_ADAPTER.validate_python(value).model_dump(mode="json")
