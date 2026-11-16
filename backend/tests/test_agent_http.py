import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from http_fixtures import responses_stream
from pydantic import SecretStr

from research_agent.config import Settings
from research_agent.events import validate_event
from research_agent.guards import RunLimitExceeded
from research_agent.main import create_app
from research_agent.research import LiveResearch


def settings(**kwargs):
    return Settings(
        demo_mode=False,
        openai_api_key=SecretStr("offline-SECRET"),
        tavily_api_key=SecretStr("offline-TAVILY"),
        **kwargs,
    )


@respx.mock
async def test_real_agent_responses_tavily_loop_and_usage():
    model = respx.post("https://api.openai.com/v1/responses").mock(
        side_effect=[
            responses_stream(calls=[("tavily_search", {"query": "evidence"})]),
            responses_stream(text="Supported answer", response_id="resp_final", reasoning=True),
        ]
    )
    tavily = respx.post("https://api.tavily.com/search").respond(
        200,
        json={
            "results": [{"url": "https://example.org", "title": "Evidence", "content": "e" * 9000}]
            * 4
        },
    )
    async with httpx.AsyncClient() as client:
        events = [
            validate_event(e)
            async for e in LiveResearch(settings(), client).events("q", "gpt-6-luna")
        ]
    assert len(model.calls) == 2 and len(tavily.calls) == 1
    first = json.loads(model.calls[0].request.content)
    assert first["stream"] and first["reasoning"] == {"effort": "low"}
    assert first["max_output_tokens"] == 4096
    second = json.loads(model.calls[1].request.content)
    tool_output = next(i for i in second["input"] if i.get("type") == "function_call_output")
    assert len(tool_output["output"]) <= 6000
    assert len(json.loads(tool_output["output"])["results"]) == 3
    assert "".join(e.get("text", "") for e in events) == "Supported answer"
    assert events[-3]["items"] == [{"url": "https://example.org", "title": "Evidence"}]
    assert events[-2] == {
        "type": "usage",
        "input_tokens": 200,
        "output_tokens": 60,
        "total_tokens": 260,
        "cached_input_tokens": 80,
        "reasoning_tokens": 40,
        "model_calls": 2,
        "tool_calls": {"tavily_search": 1},
        "estimated": False,
    }
    assert events[-1] == {"type": "done"}
    assert "SECRET" not in json.dumps(events) and "offline-TAVILY" not in json.dumps(events)
    assert "PRIVATE-REASONING" not in json.dumps(events)


@respx.mock
async def test_parallel_and_cross_turn_duplicate_searches_only_dispatch_once():
    model = respx.post("https://api.openai.com/v1/responses").mock(
        side_effect=[
            responses_stream(
                calls=[
                    ("tavily_search", {"query": " Evidence "}),
                    ("tavily_search", {"query": "evidence"}),
                ]
            ),
            responses_stream(calls=[("tavily_search", {"query": "EVIDENCE"})], response_id="r2"),
            responses_stream(response_id="r3"),
        ]
    )
    tavily = respx.post("https://api.tavily.com/search").respond(200, json={"results": []})
    async with httpx.AsyncClient() as client:
        events = [e async for e in LiveResearch(settings(), client).events("q", "gpt-6-luna")]
    assert len(model.calls) == 3 and len(tavily.calls) == 1
    assert events[-2]["tool_calls"] == {"tavily_search": 1}


@respx.mock
async def test_later_context_is_rejected_before_next_http_call():
    model = respx.post("https://api.openai.com/v1/responses").mock(
        side_effect=[
            responses_stream(calls=[("tavily_search", {"query": "evidence"})]),
        ]
    )
    respx.post("https://api.tavily.com/search").respond(200, json={"results": []})
    async with httpx.AsyncClient() as client:
        with pytest.raises(RunLimitExceeded):
            async for _ in LiveResearch(settings(max_run_input_tokens=1800), client).events(
                "q", "gpt-6-luna"
            ):
                pass
    assert len(model.calls) == 1


@respx.mock
async def test_model_call_limit_stops_http_dispatch():
    model = respx.post("https://api.openai.com/v1/responses").mock(
        side_effect=[
            responses_stream(calls=[("tavily_search", {"query": "evidence"})]),
        ]
    )
    respx.post("https://api.tavily.com/search").respond(200, json={"results": []})
    async with httpx.AsyncClient() as client:
        events = [
            e
            async for e in LiveResearch(settings(max_model_calls=1), client).events(
                "q", "gpt-6-luna"
            )
        ]
    assert len(model.calls) == 1 and events[-1]["type"] == "done"


@respx.mock
def test_openai_http_failure_is_redacted_at_sse_boundary(caplog):
    route = respx.post("https://api.openai.com/v1/responses").respond(
        500, json={"error": {"message": "SECRET https://provider/?key=NEVER-EXPOSE"}}
    )
    # Client has no open resources until first use, and is closed after TestClient exits.
    client = httpx.AsyncClient()
    service = LiveResearch(settings(), client)
    with TestClient(create_app(settings(), service)) as app:
        response = app.post("/api/v1/research", json={"query": "q"})
        assert app.portal is not None
        app.portal.call(client.aclose)
    assert len(route.calls) == 2  # The configured single SDK retry is bounded.
    assert '"code": "research_failed"' in response.text
    assert "NEVER-EXPOSE" not in response.text + caplog.text
    assert "SECRET" not in response.text + caplog.text


@respx.mock
async def test_oversized_tool_query_does_not_reach_http():
    route = respx.post("https://api.openai.com/v1/responses").mock(
        side_effect=[
            responses_stream(calls=[("tavily_search", {"query": "x" * 401})]),
            responses_stream(response_id="r2"),
        ]
    )
    async with httpx.AsyncClient() as client:
        events = [e async for e in LiveResearch(settings(), client).events("q", "gpt-6-luna")]
    assert len(route.calls) == 2
    assert events[-2]["tool_calls"] == {}


@respx.mock
async def test_missing_provider_usage_is_marked_estimated():
    respx.post("https://api.openai.com/v1/responses").mock(
        return_value=responses_stream(usage=False)
    )
    async with httpx.AsyncClient() as client:
        events = [e async for e in LiveResearch(settings(), client).events("q", "gpt-6-luna")]
    assert events[-2]["estimated"]
    assert events[-2]["input_tokens"] > 0
    assert events[-2]["output_tokens"] == 4096


@respx.mock
async def test_parallel_tool_call_limit_bounds_paid_dispatch():
    respx.post("https://api.openai.com/v1/responses").mock(
        return_value=responses_stream(
            calls=[("tavily_search", {"query": "one"}), ("tavily_search", {"query": "two"})]
        )
    )
    tavily = respx.post("https://api.tavily.com/search").respond(200, json={"results": []})
    async with httpx.AsyncClient() as client:
        events = [
            e
            async for e in LiveResearch(settings(max_tool_calls=1), client).events(
                "q", "gpt-6-luna"
            )
        ]
    assert len(tavily.calls) <= 1
    assert events[-1]["type"] == "done"
