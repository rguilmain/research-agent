import pytest

from research_agent.config import Settings


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch):
    """Developer .env files and credentials must never change offline tests."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
