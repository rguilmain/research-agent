"""Lifespan-owned PostgreSQL connections. Schema changes are explicit, never startup DDL."""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from research_agent.config import Settings


class RedactedPoolMessages(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Pool reconnect logs contain exception strings; discard these entirely.
        record.msg, record.args, record.exc_info = "Database pool operation", (), None
        return True


pool_log_filter = RedactedPoolMessages()


def configure_event_loop() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def selector_loop_factory():
    return asyncio.SelectorEventLoop()


class Database:
    def __init__(self, settings: Settings):
        logging.getLogger("psycopg.pool").addFilter(pool_log_filter)
        url = make_url(settings.database_url.get_secret_value())
        self.engine = create_async_engine(
            url.set(drivername="postgresql+psycopg"),
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args={"connect_timeout": 5, "options": "-c statement_timeout=5000"},
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.pool = AsyncConnectionPool[AsyncConnection[DictRow]](
            url.set(drivername="postgresql").render_as_string(hide_password=False),
            open=False,
            min_size=1,
            max_size=5,
            timeout=5,
            reconnect_timeout=5,
            kwargs={
                "autocommit": True,
                "row_factory": dict_row,
                "prepare_threshold": 0,
                "connect_timeout": 5,
            },
        )
        self.checkpointer = AsyncPostgresSaver(
            self.pool, serde=JsonPlusSerializer(allowed_msgpack_modules=None, pickle_fallback=False)
        )

    @asynccontextmanager
    async def open(self):
        try:
            try:
                await self.pool.open(wait=True, timeout=5)
                await self.ready()
            except Exception:
                raise RuntimeError(
                    "Database startup failed; check configuration and migrations"
                ) from None
            yield self
        finally:
            await self.pool.close()
            await self.engine.dispose()

    async def ready(self) -> None:
        async with self.engine.connect() as connection:
            # Fail readiness if migrations/checkpointer setup have not been run.
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            checkpoint_version = await connection.scalar(
                text("SELECT max(v) FROM checkpoint_migrations")
            )
            if revision != "0002" or checkpoint_version != len(self.checkpointer.MIGRATIONS) - 1:
                raise RuntimeError("Database migrations are not current")
            await connection.execute(text("SELECT owner_id FROM threads LIMIT 0"))
            await connection.execute(text("SELECT heartbeat_at FROM runs LIMIT 0"))
            await connection.execute(text("SELECT github_id FROM users LIMIT 0"))
            await connection.execute(text("SELECT token_hash FROM auth_sessions LIMIT 0"))
            await connection.execute(text("SELECT state_hash FROM oauth_states LIMIT 0"))
