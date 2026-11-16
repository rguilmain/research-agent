"""Per-run tool admission; cached agents never own mutable execution state."""

import asyncio
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from research_agent.config import Settings


@dataclass
class RunContext:
    max_parallel: int = 3
    seen: set[str] = field(default_factory=set)
    tool_calls: Counter[str] = field(default_factory=Counter)
    semaphore: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self.semaphore = asyncio.Semaphore(self.max_parallel)


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
