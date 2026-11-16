"""Per-run tool admission; cached agents never own mutable execution state."""

import asyncio
import json
from collections import Counter
from dataclasses import dataclass, field
from math import ceil
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from research_agent.config import Settings


@dataclass
class RunContext:
    max_parallel: int = 3
    seen: set[str] = field(default_factory=set)
    tool_calls: Counter[str] = field(default_factory=Counter)
    semaphore: asyncio.Semaphore = field(init=False)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    model_calls: int = 0
    reserved_input_tokens: int = 0
    estimated: bool = False

    def __post_init__(self) -> None:
        self.semaphore = asyncio.Semaphore(self.max_parallel)

    def usage_event(self) -> dict[str, Any]:
        return {
            "type": "usage",
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "model_calls": self.model_calls,
            "tool_calls": dict(self.tool_calls),
            "estimated": self.estimated,
        }


class RunLimitExceeded(RuntimeError):
    """A local safety bound rejected work before a provider call."""


class ModelGuards(AgentMiddleware):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        context: RunContext = request.runtime.context
        messages = ([request.system_message] if request.system_message else []) + request.messages
        # Deliberately conservative character-based estimate, not a tokenizer/billing promise.
        estimate = ceil(
            count_tokens_approximately(messages, tools=request.tools, chars_per_token=1)
        )
        if (
            estimate + self.settings.max_output_tokens > self.settings.max_context_tokens
            or context.reserved_input_tokens + estimate > self.settings.max_run_input_tokens
        ):
            raise RunLimitExceeded("Model context safety limit reached")
        context.reserved_input_tokens += estimate
        context.model_calls += 1
        response = await handler(request)
        # Only final model messages, never streamed chunks or graph updates, are counted.
        usages = [m.usage_metadata for m in response.result if getattr(m, "usage_metadata", None)]
        if not usages:
            context.input_tokens += estimate
            context.output_tokens += self.settings.max_output_tokens
            context.estimated = True
        else:
            for usage in usages:
                inp, out = usage["input_tokens"], usage["output_tokens"]
                cached = sum(
                    v
                    for k, v in usage.get("input_token_details", {}).items()
                    if k == "cache_read" or k.endswith("_cache_read")
                )
                reasoning = sum(
                    v
                    for k, v in usage.get("output_token_details", {}).items()
                    if k == "reasoning" or k.endswith("_reasoning")
                )
                if not (0 <= cached <= inp and 0 <= reasoning <= out):
                    raise ValueError("Invalid usage totals")
                context.input_tokens += inp
                context.output_tokens += out
                context.cached_input_tokens += cached
                context.reasoning_tokens += reasoning
            # Later calls must account for actual input if it exceeded our estimate.
            context.reserved_input_tokens += max(
                0, sum(u["input_tokens"] for u in usages) - estimate
            )
        return response


class ToolGuards(AgentMiddleware):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        context: RunContext = request.runtime.context
        call = request.tool_call
        args = call["args"]

        def reject(text: str) -> ToolMessage:
            return ToolMessage(content=text, tool_call_id=call["id"], name=call["name"])

        if (
            set(args) != {"query"}
            or not isinstance(args.get("query"), str)
            or not args["query"].strip()
            or len(args["query"]) > self.settings.max_tool_input_chars
        ):
            return reject("Tool input rejected: provide one bounded, nonempty query.")
        key = json.dumps([call["name"], " ".join(args["query"].casefold().split())])
        # No await between membership and insertion: parallel duplicates cannot race.
        if key in context.seen:
            return reject("Already searched; reuse the earlier result.")
        context.seen.add(key)
        async with context.semaphore:
            context.tool_calls[call["name"]] += 1
            result = await handler(request)
        if isinstance(result, ToolMessage):
            content = result.content
            encoded = content if isinstance(content, str) else json.dumps(content)
            if len(encoded) > self.settings.max_tool_output_chars:
                # Never slice serialized JSON: retain whole evidence objects or drop them.
                try:
                    data: Any = json.loads(encoded)
                    items = data.get("results") if isinstance(data, dict) else data
                    if not isinstance(items, list):
                        raise ValueError
                    while (
                        items
                        and len(json.dumps(data, ensure_ascii=False))
                        > self.settings.max_tool_output_chars
                    ):
                        items.pop()
                    encoded = json.dumps(data, ensure_ascii=False)
                    if len(encoded) > self.settings.max_tool_output_chars:
                        raise ValueError
                except (ValueError, TypeError):
                    encoded = "Tool output exceeded its size limit."[
                        : self.settings.max_tool_output_chars
                    ]
                result = result.model_copy(update={"content": encoded, "artifact": None})
        return result
