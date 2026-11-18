"""Scheduled retention cleanup: locking, failure isolation and scheduler wiring."""

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from research_agent.config import Settings
from research_agent.main import create_app
from research_agent.maintenance import periodic_cleanup


@pytest.mark.parametrize("value", [-1, 1441])
def test_cleanup_interval_rejects_out_of_range_values(value):
    with pytest.raises(ValidationError, match="CLEANUP_INTERVAL_MINUTES"):
        Settings(app_env="test", cleanup_interval_minutes=value)


@pytest.mark.parametrize("value", [0, 1440])
def test_cleanup_interval_accepts_disabled_and_maximum(value):
    config = Settings(app_env="test", cleanup_interval_minutes=value)
    assert config.cleanup_interval_minutes == value


async def test_periodic_cleanup_survives_failure_and_shuts_down(caplog):
    calls = 0
    done = asyncio.Event()

    async def run_once() -> None:
        nonlocal calls
        calls += 1
        done.set()
        raise RuntimeError("SECRET https://provider/?key=NEVER-EXPOSE")

    with caplog.at_level(logging.ERROR, logger="research_agent.maintenance"):
        task = asyncio.create_task(periodic_cleanup(run_once, 0.01))
        await asyncio.wait_for(done.wait(), 3)
        await asyncio.wait_for(asyncio.sleep(0.05), 3)
        assert calls >= 2  # A failing pass must not stop the schedule.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert "RuntimeError" in caplog.text
    assert "SECRET" not in caplog.text and "NEVER-EXPOSE" not in caplog.text


def test_demo_app_without_a_database_starts_no_cleanup_task():
    app = create_app(Settings(app_env="test"))
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert getattr(app.state, "cleanup_task", None) is None
