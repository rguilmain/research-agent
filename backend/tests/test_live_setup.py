import httpx
from pydantic import SecretStr

from research_agent.config import Settings
from research_agent.research import LiveResearch


async def test_live_agent_constructs_without_network_and_is_cached():
    settings = Settings(
        demo_mode=False,
        openai_api_key=SecretStr("offline-test-key"),
        tavily_api_key=SecretStr("offline-test-key"),
    )
    async with httpx.AsyncClient() as client:
        service = LiveResearch(settings, client)
        agent = service.agent("gpt-6-luna")
        assert agent is service.agent("gpt-6-luna")
        assert callable(agent.astream)
