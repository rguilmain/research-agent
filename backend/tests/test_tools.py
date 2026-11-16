import json

import httpx
import pytest
import respx

from research_agent.research import extract_sources
from research_agent.tools import tavily_query, wikipedia_query, wolfram_query


@respx.mock
async def test_tavily_fixed_request_and_bounded_evidence():
    route = respx.post("https://api.tavily.com/search").respond(
        200,
        json={
            "results": [{"url": "https://example.org", "title": "T" * 300, "content": "x" * 5000}]
            * 5
        },
    )
    async with httpx.AsyncClient() as client:
        result = json.loads(await tavily_query(client, "SECRET-KEY", "question"))
    assert len(result["results"]) == 3
    assert len(result["results"][0]["title"]) == 200
    assert len(result["results"][0]["content"]) == 1000
    assert "SECRET-KEY" not in json.dumps(result)
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer SECRET-KEY"
    assert json.loads(request.content) == {
        "query": "question",
        "max_results": 3,
        "search_depth": "basic",
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "auto_parameters": False,
    }


@pytest.mark.parametrize("url", ["javascript:bad", "https://key@example.org", "https://[bad"])
@respx.mock
async def test_tavily_unsafe_sources_removed(url):
    respx.post("https://api.tavily.com/search").respond(200, json={"results": [{"url": url}]})
    async with httpx.AsyncClient() as client:
        assert json.loads(await tavily_query(client, "secret", "q")) == {"results": []}


@pytest.mark.parametrize("failure", [400, 500, "timeout", "oversize", "malformed"])
@respx.mock
async def test_tavily_failures_are_redacted(failure):
    route = respx.post("https://api.tavily.com/search")
    if failure == "timeout":
        route.mock(side_effect=httpx.ReadTimeout("https://provider/?key=SECRET"))
    elif failure == "oversize":
        route.respond(200, content=b"x" * 256_001)
    elif failure == "malformed":
        route.respond(200, json={"results": "bad"})
    else:
        route.respond(failure, text="SECRET")
    async with httpx.AsyncClient() as client:
        assert await tavily_query(client, "SECRET", "q") == "Tavily is temporarily unavailable."


@respx.mock
async def test_wolfram_result_and_secret_redaction():
    route = respx.get("https://api.wolframalpha.com/v2/query")
    route.respond(
        200,
        json={
            "queryresult": {
                "success": True,
                "pods": [{"primary": True, "subpods": [{"plaintext": "4"}]}],
            }
        },
    )
    async with httpx.AsyncClient() as client:
        assert await wolfram_query(client, "SECRET-APPID", "2+2") == "4"
        route.respond(403)
        result = await wolfram_query(client, "SECRET-APPID", "2+2")
        assert "403" in result
        assert "SECRET-APPID" not in result
        route.respond(200, text="bad json")
        assert "unavailable" in await wolfram_query(client, "SECRET-APPID", "2+2")


@respx.mock
async def test_wikipedia_failure_is_safe():
    respx.get("https://en.wikipedia.org/w/api.php").respond(503)
    async with httpx.AsyncClient() as client:
        assert "unavailable" in await wikipedia_query(client, "query")


def test_only_structured_http_sources():
    assert extract_sources("not json") == []
    assert extract_sources({"url": "javascript:alert(1)"}) == []
    assert extract_sources({"url": "https://secret@example.org/"}) == []
    assert extract_sources({"results": [{"url": "https://example.org", "title": "Evidence"}]}) == [
        {"url": "https://example.org", "title": "Evidence"}
    ]
