import httpx
import respx

from research_agent.research import extract_sources
from research_agent.tools import wikipedia_query, wolfram_query


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
