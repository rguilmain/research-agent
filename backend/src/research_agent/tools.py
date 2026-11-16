"""Read-only tools; credentials and provider error URLs never enter model context."""

import json
from urllib.parse import urlparse

import httpx


def safe_url(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 2000 or any(c.isspace() for c in value):
        return False
    try:
        parsed = urlparse(value)
        return bool(
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        )
    except ValueError:
        return False


async def bounded_json(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> object:
    """Bound decoded response bytes, including compressed bodies; never follow redirects."""
    async with client.stream(method, url, follow_redirects=False, **kwargs) as response:
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=8192):
            body.extend(chunk)
            if len(body) > 256_000:
                raise ValueError("Tool response too large")
        return json.loads(body)


async def tavily_query(client: httpx.AsyncClient, api_key: str, query: str) -> str:
    """Search a fixed endpoint with server-controlled depth, result count and fields."""
    try:
        data = await bounded_json(
            client,
            "POST",
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "query": query,
                "max_results": 3,
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
                "auto_parameters": False,
            },
            timeout=30,
        )
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ValueError("Invalid search response")
        results = []
        for item in data["results"][:3]:
            if isinstance(item, dict) and safe_url(item.get("url")):
                results.append(
                    {
                        "title": str(item.get("title") or "Source")[:200],
                        "url": item["url"],
                        "content": str(item.get("content") or "")[:1000],
                    }
                )
        return json.dumps({"results": results}, ensure_ascii=False)
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        return "Tavily is temporarily unavailable."


async def wolfram_query(client: httpx.AsyncClient, app_id: str, query: str) -> str:
    try:
        response = await client.get(
            "https://api.wolframalpha.com/v2/query",
            params={"appid": app_id, "input": query, "output": "json", "format": "plaintext"},
            headers={"User-Agent": "research-agent/0.1 (wolfram-alpha tool)"},
            timeout=30,
        )
        response.raise_for_status()
        result = response.json().get("queryresult", {})
        if not result.get("success"):
            return "Wolfram Alpha could not answer that query."
        pods = result.get("pods", [])
        preferred = [p for p in pods if p.get("primary") or p.get("title", "").lower() == "result"]
        fallback = [p for p in pods if p.get("title", "").lower() != "input"]
        for pod in preferred + fallback:
            for subpod in pod.get("subpods", []):
                text = (subpod.get("plaintext") or "").strip()
                if text:
                    return text[:8000]
        return "Wolfram Alpha returned no usable result."
    except httpx.HTTPStatusError as exc:
        return f"Wolfram Alpha returned HTTP {exc.response.status_code}."
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        return "Wolfram Alpha is temporarily unavailable."


async def wikipedia_query(client: httpx.AsyncClient, query: str) -> str:
    """Search Wikipedia's fixed API endpoint; do not fetch model-supplied URLs."""
    import json

    try:
        response = await client.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrlimit": 3,
                "prop": "extracts|info",
                "exintro": 1,
                "explaintext": 1,
                "inprop": "url",
                "format": "json",
            },
            headers={"User-Agent": "research-agent/0.1 (web research; local development)"},
            timeout=20,
        )
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", {})
        return json.dumps(
            [
                {
                    "title": p.get("title"),
                    "url": p.get("fullurl"),
                    "content": p.get("extract", "")[:3000],
                }
                for p in pages.values()
            ]
        )
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        return "Wikipedia is temporarily unavailable."
