import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from research_agent.config import Settings
from research_agent.events import validate_event
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
