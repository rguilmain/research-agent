"""Do not configure SQL logging: even migration exceptions must not expose the DSN."""

import asyncio

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from research_agent.config import Settings
from research_agent.storage_models import Base


def apply(connection):
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def migrate():
    raw = Settings().database_url.get_secret_value()
    if not raw:
        raise ValueError("DATABASE_URL is required for migrations")
    engine = create_async_engine(
        make_url(raw).set(drivername="postgresql+psycopg"), hide_parameters=True
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(apply)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise ValueError("Offline SQL generation is not supported")
else:
    asyncio.run(migrate())
