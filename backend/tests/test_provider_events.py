import httpx
from langchain_core.messages import AIMessageChunk, ToolMessage

from research_agent.config import Settings
from research_agent.research import LiveResearch, extract_sources


async def test_adapter_only_streams_assistant_text_and_structured_sources():
    class FakeGraph:
        async def astream(self, *args, **kwargs):
            yield "messages", (ToolMessage(content="untrusted raw tool text", tool_call_id="1"), {})
            yield (
                "messages",
                (
                    AIMessageChunk(
                        content=[
                            {"type": "reasoning", "reasoning": "private reasoning"},
                            {"type": "text", "text": "Evidence-based answer"},
                        ]
                    ),
                    {},
                ),
            )
            yield (
                "updates",
                {
                    "tools": {
                        "messages": [
                            ToolMessage(
                                content='[{"url":"https://example.org","title":"Evidence"}]',
                                tool_call_id="1",
                                name="wikipedia",
                            )
                        ]
                    }
                },
            )

    async with httpx.AsyncClient() as client:
        service = LiveResearch(Settings(), client)
        service.agents["gpt-6-luna"] = FakeGraph()
        events = [event async for event in service.events("question", "gpt-6-luna")]
    text = "".join(event.get("text", "") for event in events)
    assert text == "Evidence-based answer"
    assert events[-3] == {
        "type": "sources",
        "items": [{"url": "https://example.org", "title": "Evidence"}],
    }
    assert events[-1] == {"type": "done"}


def test_malformed_citation_url_is_ignored():
    assert extract_sources({"url": "https://[invalid"}) == []
