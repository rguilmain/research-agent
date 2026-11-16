"""Read-only tools; credentials and provider error URLs never enter model context."""

import httpx


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
