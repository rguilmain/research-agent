from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from research_agent.history import MAX_MESSAGES, MAX_TEXT, public_messages


def test_projection_only_exposes_user_and_final_text_with_turn_local_sources():
    message = '[{"url":"https://example.org/first","title":"Evidence","content":"SECRET raw"}]'
    messages = [
        SystemMessage(content="SECRET system"),
        HumanMessage(content="First question"),
        AIMessage(
            content="SECRET intermediate",
            tool_calls=[{"id": "a", "name": "wikipedia", "args": {"query": "SECRET args"}}],
        ),
        ToolMessage(content=message, tool_call_id="a"),
        ToolMessage(content='{"url":"javascript:alert(1)"}', tool_call_id="b"),
        AIMessage(
            content=[
                {"type": "reasoning", "reasoning": "SECRET reasoning"},
                {"type": "text", "text": "First answer"},
            ],
            additional_kwargs={"private": "SECRET metadata"},
        ),
        HumanMessage(content="Next question"),
        AIMessage(content="Next answer"),
    ]
    items, truncated = public_messages(messages)
    assert not truncated
    assert [item.text for item in items] == [
        "First question",
        "First answer",
        "Next question",
        "Next answer",
    ]
    assert items[1].sources[0].url == "https://example.org/first"
    assert items[-1].sources == []
    assert "SECRET" not in str([item.model_dump() for item in items])


def test_projection_is_bounded_and_rejects_unexpected_roles_and_content():
    items, truncated = public_messages(
        [HumanMessage(content="x" * (MAX_TEXT + 1)), AIMessage(content="answer")]
    )
    assert truncated and len(items[0].text) == MAX_TEXT
    items, truncated = public_messages(
        [
            message
            for _ in range(MAX_MESSAGES)
            for message in [HumanMessage(content="q"), AIMessage(content="a")]
        ]
    )
    assert truncated and len(items) == MAX_MESSAGES
    assert public_messages([AIMessage(content="orphan"), {"role": "user", "content": "raw"}]) == (
        [],
        False,
    )
