"""Explicit local/release schema setup, retained-data cleanup and its in-process schedule."""

import argparse
import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from alembic import command
from alembic.config import Config

from research_agent.config import Settings
from research_agent.db import Database, configure_event_loop
from research_agent.repository import Conflict, Repository

logger = logging.getLogger(__name__)


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


async def cleanup_batches(repo: Repository, max_batches: int) -> int:
    """Remove expired data in bounded batches, stopping early once a pass finds nothing."""
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


async def scheduled_cleanup_pass(repo: Repository) -> int | None:
    """One locked cleanup pass; None when another process holds the maintenance lock."""
    try:
        async with repo.maintenance_lock():
            return await cleanup_batches(repo, 1)
    except Conflict:
        return None


async def periodic_cleanup(run_once: Callable[[], Awaitable[object]], interval_s: float) -> None:
    """Run cleanup after every interval until cancelled.

    A failing pass is logged by class name only, because exception messages can carry
    key-bearing URLs, and the loop keeps running. Cancellation propagates for shutdown.
    """
    while True:
        await asyncio.sleep(interval_s)
        try:
            await run_once()
        except Exception as exc:
            logger.error("Scheduled cleanup failed class=%s", type(exc).__name__)


async def cleanup_database(max_batches: int = 1):
    settings = Settings()
    if not settings.database_url.get_secret_value():
        raise ValueError("DATABASE_URL required")
    db = Database(settings)
    async with db.open():
        repo = Repository(db, settings)
        async with repo.maintenance_lock():
            return await cleanup_batches(repo, max_batches)


def cleanup() -> None:
    parser = argparse.ArgumentParser(description="Bounded retention cleanup")
    parser.add_argument("--max-batches", type=int, choices=range(1, 51), default=1)
    args = parser.parse_args()
    configure_event_loop()
    try:
        count = asyncio.run(cleanup_database(args.max_batches))
    except Conflict:
        raise SystemExit("Cleanup is already running in another process") from None
    except Exception:
        raise SystemExit("Database cleanup failed") from None
    print(
        f"Removed {count} expired threads, sessions, OAuth states, empty principals, "
        f"quota days or rate-limit windows (maximum {args.max_batches} batches)"
    )
