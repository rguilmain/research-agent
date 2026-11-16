"""Explicit, read-only account visibility probe. Never runs during app startup."""

import asyncio

import httpx

from research_agent.catalog import load_catalog
from research_agent.config import Settings


async def check_models(client: httpx.AsyncClient, key: str) -> dict[str, str]:
    results = {}
    for model_id in load_catalog():
        try:
            response = await client.get(
                f"https://api.openai.com/v1/models/{model_id}",
                headers={"Authorization": f"Bearer {key}"},
                timeout=15,
                follow_redirects=False,
            )
            results[model_id] = (
                "visible" if response.status_code == 200 else f"HTTP {response.status_code}"
            )
        except httpx.HTTPError:
            results[model_id] = "unavailable"
    return results


def run() -> None:
    key = Settings().openai_api_key.get_secret_value()
    if not key:
        raise SystemExit("Set OPENAI_API_KEY in backend/.env; never paste credentials into chat.")

    async def main():
        async with httpx.AsyncClient() as client:
            for model, result in (await check_models(client, key)).items():
                print(f"{model}: {result}")
        print(
            "Visibility does not verify Responses support, reasoning, pricing or generation access."
        )

    asyncio.run(main())
