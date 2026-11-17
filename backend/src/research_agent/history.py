"""Explicit public projection of private checkpoints; never serialize agent state."""

from typing import Annotated, Literal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field

from research_agent.events import Source
from research_agent.research import extract_sources

MAX_MESSAGES = 100
MAX_TEXT = 32_000


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["user", "assistant"]
    text: Annotated[str, Field(min_length=1, max_length=MAX_TEXT)]
    sources: Annotated[list[Source], Field(max_length=100)] = Field(default_factory=list)


class History(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    items: Annotated[list[HistoryMessage], Field(max_length=MAX_MESSAGES)]
    status: Literal["idle", "running", "completed", "failed", "cancelled", "abandoned"]
    can_continue: bool
    demo: bool
    truncated: bool = False


def public_messages(messages: list) -> tuple[list[HistoryMessage], bool]:
    """One final assistant answer per user turn, plain text blocks only.

    Ignore intermediate assistant messages with tool calls and every private field.
    Sources are only drawn from structured tool evidence in the same turn.
    Bound the public response without altering the underlying conversation.
    """
    items: list[HistoryMessage] = []
    sources: dict[str, Source] = {}
    answer: str | None = None
    truncated = False
    in_turn = False

    def append(role: Literal["user", "assistant"], value: str, evidence: list[Source]):
        nonlocal truncated
        if len(value) > MAX_TEXT:
            truncated = True
        if value:
            items.append(HistoryMessage(role=role, text=value[:MAX_TEXT], sources=evidence))

    for message in messages:
        if isinstance(message, HumanMessage):
            if answer:
                append("assistant", answer, list(sources.values())[:100])
            sources, answer = {}, None
            in_turn = True
            # Application inputs are strings; reject unexpected multimodal content.
            if isinstance(message.content, str):
                append("user", message.content, [])
        elif in_turn and isinstance(message, ToolMessage):
            for source in extract_sources(message.content):
                if source["title"]:
                    sources[source["url"]] = Source.model_validate(source)
        elif in_turn and isinstance(message, AIMessage) and not message.tool_calls:
            parts = []
            for block in message.content_blocks:
                text = block.get("text")
                if block.get("type") == "text" and isinstance(text, str):
                    parts.append(text)
            answer = "".join(parts)
    if answer:
        append("assistant", answer, list(sources.values())[:100])
    return items[-MAX_MESSAGES:], truncated or len(items) > MAX_MESSAGES
