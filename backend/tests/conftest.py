import asyncio
import os
import sys

import pytest

from research_agent.config import Settings

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def pytest_configure(config):
    config.addinivalue_line("markers", "pg: requires a dedicated PostgreSQL test database")
    if os.environ.get("REQUIRE_PG") == "1" and not os.environ.get("TEST_DATABASE_URL"):
        raise pytest.UsageError("REQUIRE_PG=1 requires TEST_DATABASE_URL")


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch):
    """Developer .env files and credentials must never change offline tests."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
