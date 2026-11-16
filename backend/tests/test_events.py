import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from research_agent.config import Settings
from research_agent.events import validate_event
from research_agent.guards import RunContext
from research_agent.main import create_app


@pytest.mark.parametrize(
    "value",
    [
        None,
        {"type": "unknown"},
        {"type": "done", "secret": "key"},
        {"type": "token", "text": 1},
        {"type": "token", "text": "x" * 32001},
        {"type": "step", "tool": "shell", "status": "started"},
        {"type": "sources", "items": [{"url": "javascript:bad", "title": "T"}]},
        {"type": "run_started", "model_id": "model", "demo": "true"},
    ],
)
def test_bad_events_rejected(value):
    with pytest.raises(ValidationError):
        validate_event(value)


def test_malformed_service_event_is_never_serialized(caplog):
    class BadService:
        async def events(self, query, model_id):
            yield {"type": "token", "text": "ok", "secret": "NEVER-EXPOSE"}

    with TestClient(create_app(Settings(), BadService())) as client:
        response = client.post("/api/v1/research", json={"query": "q"})
    assert '"type": "error"' in response.text
    assert "NEVER-EXPOSE" not in response.text + caplog.text


def test_usage_totals_must_not_add_reasoning_or_cached_twice():
    event = RunContext(
        input_tokens=100, output_tokens=30, cached_input_tokens=40, reasoning_tokens=20
    ).usage_event()
    assert validate_event(event)["total_tokens"] == 130
    with pytest.raises(ValidationError):
        validate_event({**event, "total_tokens": 190})
