import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from research_agent.catalog import estimate_model_cost, load_catalog
from research_agent.config import Settings
from research_agent.guards import ModelGuards, RunContext, RunLimitExceeded, ToolGuards

# A free tool and a paid tool; a tool absent here is refused by the guard.
PRICES = {"wikipedia": Decimal("0"), "tavily_search": Decimal("0.008")}
LUNA = load_catalog()["gpt-6-luna"]


def request(context, query="question", name="wikipedia", **extra):
    return SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        tool_call={"name": name, "args": {"query": query, **extra}, "id": "call"},
    )


async def test_parallel_duplicates_are_rejected_and_runs_are_isolated():
    guard = ToolGuards(Settings(), PRICES)
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

    result = await ToolGuards(Settings(), PRICES).awrap_tool_call(
        request(RunContext(), query), handler
    )
    assert "rejected" in result.content


async def test_concurrency_bound_and_valid_json_output():
    active = peak = 0
    guard = ToolGuards(Settings(max_tool_output_chars=100), PRICES)
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

    result = await ToolGuards(Settings(), PRICES).awrap_tool_call(request(RunContext()), handler)
    assert result.content == "Tool output exceeded its size limit."


async def test_usage_counts_each_call_once_and_details_are_subsets():
    ctx = RunContext()
    guard = ModelGuards(Settings(), LUNA)
    req = SimpleNamespace(
        runtime=SimpleNamespace(context=ctx),
        system_message=None,
        messages=[HumanMessage(content="question")],
        tools=[],
    )

    async def handler(req):
        return SimpleNamespace(
            result=[
                AIMessage(
                    content="answer",
                    usage_metadata={
                        "input_tokens": 100,
                        "output_tokens": 30,
                        "total_tokens": 130,
                        "input_token_details": {"cache_read": 40},
                        "output_token_details": {"reasoning": 20},
                    },
                )
            ]
        )

    await guard.awrap_model_call(req, handler)
    await guard.awrap_model_call(req, handler)
    event = ctx.usage_event()
    assert (event["input_tokens"], event["output_tokens"], event["total_tokens"]) == (200, 60, 260)
    assert (event["cached_input_tokens"], event["reasoning_tokens"]) == (80, 40)
    assert event["model_calls"] == 2 and not event["estimated"]


async def test_missing_usage_estimated_and_cumulative_context_rejected():
    ctx = RunContext()
    settings = Settings(max_output_tokens=10, max_run_input_tokens=150, max_context_tokens=200)
    guard = ModelGuards(settings, LUNA)
    req = SimpleNamespace(
        runtime=SimpleNamespace(context=ctx),
        system_message=None,
        messages=[HumanMessage(content="x" * 100)],
        tools=[],
    )

    async def handler(req):
        return SimpleNamespace(result=[AIMessage(content="answer")])

    await guard.awrap_model_call(req, handler)
    assert ctx.estimated and ctx.input_tokens >= 100 and ctx.output_tokens == 10
    with pytest.raises(RunLimitExceeded):
        await guard.awrap_model_call(req, handler)
    assert ctx.model_calls == 1
    req.messages.append(HumanMessage(content="x" * 200))
    with pytest.raises(RunLimitExceeded):
        await guard.awrap_model_call(req, handler)


def test_cost_estimate_requires_cached_price_and_never_adds_reasoning():
    model = load_catalog()["gpt-6-luna"]
    assert estimate_model_cost(model, input_tokens=100, output_tokens=30) == Decimal("0.000025")
    with pytest.raises(ValueError, match="unverified"):
        estimate_model_cost(model, input_tokens=100, output_tokens=30, cached_input_tokens=40)
    priced = model.model_copy(update={"cached_input_usd_per_mtok": Decimal("0.05")})
    assert estimate_model_cost(
        priced, input_tokens=100, output_tokens=30, cached_input_tokens=40
    ) == Decimal("0.000023")
    with pytest.raises(ValueError):
        estimate_model_cost(model, input_tokens=-1, output_tokens=30)
