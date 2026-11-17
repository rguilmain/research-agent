"""Explicit local/release schema setup and bounded retention cleanup commands."""

import argparse
import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

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


async def cleanup_database(max_batches: int = 1):
    settings = Settings()
    if not settings.database_url.get_secret_value():
        raise ValueError("DATABASE_URL required")
    db = Database(settings)
    async with db.open():
        repo = Repository(db, settings)
        total = 0
        for _ in range(max_batches):
            count = await repo.cleanup()
            total += count
            if count == 0:
                break
        # Sessions and OAuth states expire independently of threads; each pass is
        # bounded so a large backlog cannot turn one invocation into a long scan.
        for _ in range(max_batches):
            removed = await repo.cleanup_identity()
            total += removed
            if removed == 0:
                break
        return total


def cleanup() -> None:
    parser = argparse.ArgumentParser(description="Bounded retention cleanup")
    parser.add_argument("--max-batches", type=int, choices=range(1, 51), default=1)
    args = parser.parse_args()
    configure_event_loop()
    try:
        count = asyncio.run(cleanup_database(args.max_batches))
    except Exception:
        raise SystemExit("Database cleanup failed") from None
    print(
        f"Removed {count} expired threads, sessions, OAuth states, empty principals, "
        f"quota days or rate-limit windows (maximum {args.max_batches} batches)"
    )
