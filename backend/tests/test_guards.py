import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from research_agent.config import Settings
from research_agent.guards import RunContext, ToolGuards


def request(context, query="question", name="wikipedia", **extra):
    return SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        tool_call={"name": name, "args": {"query": query, **extra}, "id": "call"},
    )


async def test_parallel_duplicates_are_rejected_and_runs_are_isolated():
    guard = ToolGuards(Settings())
    count = 0

    async def handler(req):
        nonlocal count
        count += 1
        await asyncio.sleep(0)
        return ToolMessage(content="ok", tool_call_id="call")

    ctx = RunContext()
    results = await asyncio.gather(
        guard.awrap_tool_call(request(ctx, " Question "), handler),
        guard.awrap_tool_call(request(ctx, "question"), handler),
    )
    assert count == 1
    assert "Already searched" in results[1].content
    await guard.awrap_tool_call(request(RunContext()), handler)
    assert count == 2


@pytest.mark.parametrize("query", ["", "  ", "x" * 401, 123])
async def test_invalid_input_never_executes(query):
    async def handler(req):
        pytest.fail("Invalid call executed")

    result = await ToolGuards(Settings()).awrap_tool_call(request(RunContext(), query), handler)
    assert "rejected" in result.content


async def test_concurrency_bound_and_valid_json_output():
    active = peak = 0
    guard = ToolGuards(Settings(max_tool_output_chars=100))
    ctx = RunContext(max_parallel=2)

    async def handler(req):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return ToolMessage(
            content=json.dumps({"results": [{"url": "https://example.org", "content": "x" * 80}]}),
            tool_call_id="call",
        )

    results = await asyncio.gather(
        *[guard.awrap_tool_call(request(ctx, f"q{i}"), handler) for i in range(6)]
    )
    assert peak == 2
    assert ctx.tool_calls == {"wikipedia": 6}
    assert all(json.loads(r.content) == {"results": []} for r in results)


async def test_non_json_oversize_output_dropped():
    async def handler(req):
        return ToolMessage(content="x" * 7000, tool_call_id="call")

    result = await ToolGuards(Settings()).awrap_tool_call(request(RunContext()), handler)
    assert result.content == "Tool output exceeded its size limit."
