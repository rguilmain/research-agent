"""Explicit local/release schema setup and bounded retention cleanup commands."""

import asyncio
from pathlib import Path

from alembic.config import Config

from alembic import command
from research_agent.config import Settings
from research_agent.db import Database, configure_event_loop
from research_agent.repository import Repository


async def setup_checkpoints(settings: Settings):
    db = Database(settings)
    try:
        await db.pool.open(wait=True, timeout=5)
        await db.checkpointer.setup()
    finally:
        await db.pool.close()
        await db.engine.dispose()


def migrate() -> None:
    configure_event_loop()
    try:
        settings = Settings()
        if not settings.database_url.get_secret_value():
            raise ValueError("DATABASE_URL required")
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        command.upgrade(config, "head")
        asyncio.run(setup_checkpoints(settings))
    except Exception:
        raise SystemExit(
            "Database migration failed; check configuration and database availability"
        ) from None
    print("Application and checkpoint migrations complete")


async def cleanup_database():
    settings = Settings()
    if not settings.database_url.get_secret_value():
        raise ValueError("DATABASE_URL required")
    db = Database(settings)
    async with db.open():
        return await Repository(db, settings).cleanup()


def cleanup() -> None:
    configure_event_loop()
    try:
        count = asyncio.run(cleanup_database())
    except Exception:
        raise SystemExit("Database cleanup failed") from None
    print(f"Removed {count} expired/deleted threads (batch limit 100)")
