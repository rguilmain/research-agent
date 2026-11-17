"""Owner-scoped storage and cross-process run exclusion (not authentication)."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, exists, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from research_agent.config import Settings
from research_agent.db import Database
from research_agent.history import History, public_messages
from research_agent.storage_models import Principal, Run, Thread, now


@dataclass(frozen=True)
class Identity:
    id: UUID
    kind: str = "anonymous"


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


def thread_view(thread: Thread) -> dict:
    return {
        key: getattr(thread, key)
        for key in ("id", "title", "model_id", "demo", "created_at", "updated_at", "expires_at")
    }


class Repository:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings

    @staticmethod
    def owned(owner: Identity, thread_id: UUID | None = None):
        conditions = [
            Thread.owner_id == owner.id,
            Thread.deleted_at.is_(None),
            or_(Thread.expires_at.is_(None), Thread.expires_at > now()),
        ]
        if thread_id is not None:
            conditions.append(Thread.id == thread_id)
        return select(Thread).where(*conditions)

    async def create_thread(self, owner: Identity, model_id: str, title="New research") -> dict:
        async with self.db.sessions.begin() as session:
            await session.execute(
                insert(Principal)
                .values(id=owner.id, kind=owner.kind)
                .on_conflict_do_nothing(index_elements=[Principal.id])
            )
            thread = Thread(
                owner_id=owner.id,
                model_id=model_id,
                title=title,
                demo=self.settings.demo_mode,
                expires_at=now()
                + (
                    timedelta(hours=self.settings.anon_thread_ttl_hours)
                    if owner.kind == "anonymous"
                    else timedelta(days=self.settings.thread_retention_days)
                ),
            )
            session.add(thread)
            await session.flush()
            return thread_view(thread)

    async def list_threads(self, owner: Identity, limit: int, offset: int) -> list[dict]:
        async with self.db.sessions() as session:
            rows = await session.scalars(
                self.owned(owner)
                .order_by(Thread.updated_at.desc(), Thread.id)
                .limit(limit)
                .offset(offset)
            )
            return [thread_view(row) for row in rows]

    async def get_thread(self, owner: Identity, thread_id: UUID) -> dict:
        async with self.db.sessions() as session:
            thread = await session.scalar(self.owned(owner, thread_id))
            if thread is None:
                raise NotFound
            return thread_view(thread)

    async def change_thread(self, owner: Identity, thread_id: UUID, title: str | None) -> dict:
        async with self.db.sessions.begin() as session:
            thread = await session.scalar(self.owned(owner, thread_id).with_for_update())
            if thread is None:
                raise NotFound
            if await session.scalar(
                select(exists().where(Run.thread_id == thread_id, Run.status == "running"))
            ):
                raise Conflict("Thread is busy")
            if title is None:
                thread.deleted_at = now()
            else:
                thread.title = title
            thread.updated_at = now()
            return thread_view(thread)

    async def discard_empty_thread(self, owner: Identity, thread_id: UUID) -> None:
        """Only delete an owned thread with no admitted runs; safe after admission failure."""
        async with self.db.sessions.begin() as session:
            thread = await session.scalar(self.owned(owner, thread_id).with_for_update())
            if thread is not None and not await session.scalar(
                select(exists().where(Run.thread_id == thread_id))
            ):
                await session.delete(thread)

    async def messages(self, owner: Identity, thread_id: UUID) -> History:
        thread = await self.get_thread(owner, thread_id)
        items: list = []
        truncated = False
        # No advisory lock: this is a read-only projection, and an in-flight run is
        # reported instead of being blocked or blocking the stream.
        async with self.db.sessions() as session:
            status = await session.scalar(
                select(Run.status)
                .where(Run.thread_id == thread_id)
                .order_by(Run.created_at.desc(), Run.id.desc())
                .limit(1)
            )
            busy = await session.scalar(
                select(exists().where(Run.thread_id == thread_id, Run.status == "running"))
            )
        settled = status == "completed" and not busy
        if settled and not thread["demo"]:
            checkpoint = await self.db.checkpointer.aget_tuple(
                {"configurable": {"thread_id": str(thread_id)}}
            )
            if checkpoint:
                items, truncated = public_messages(
                    checkpoint.checkpoint.get("channel_values", {}).get("messages", [])
                )
        return History.model_validate(
            dict(
                items=items,
                status="running" if busy else (status or "idle"),
                can_continue=(
                    settled and not thread["demo"] and thread["demo"] == self.settings.demo_mode
                ),
                demo=thread["demo"],
                truncated=truncated,
            )
        )

    @asynccontextmanager
    async def thread_lock(self, thread_id: UUID):
        # A dedicated connection owns a session advisory lock for the whole stream.
        # Connection close/process death releases it; active-run uniqueness is a second guard.
        # Requires direct/session-mode connections, never a transaction-mode DB proxy.
        key = int.from_bytes(thread_id.bytes[:8], "big", signed=True)
        async with self.db.engine.connect() as connection:
            locked = await connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
            )
            await connection.commit()
            if not locked:
                raise Conflict("Thread is busy")
            try:
                yield
            finally:
                try:
                    await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                    await connection.commit()
                except BaseException:
                    await connection.invalidate()
                    raise

    async def start_run(self, owner: Identity, thread_id: UUID, model_id: str) -> UUID:
        async with self.db.sessions.begin() as session:
            thread = await session.scalar(self.owned(owner, thread_id).with_for_update())
            if thread is None:
                raise NotFound
            if thread.model_id != model_id or thread.demo != self.settings.demo_mode:
                raise Conflict("Thread model or mode differs; start a new thread")
            previous = await session.scalar(
                select(Run)
                .where(Run.thread_id == thread_id)
                .order_by(Run.created_at.desc())
                .limit(1)
            )
            if previous and previous.status != "completed":
                raise Conflict("Thread is busy or interrupted; start a new thread")
            run = Run(thread_id=thread_id, owner_id=owner.id, model_id=model_id)
            session.add(run)
            thread.updated_at = now()
            await session.flush()
            return run.id

    async def save_usage(self, run_id: UUID, usage: dict) -> None:
        values = {
            k: usage[k]
            for k in (
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "reasoning_tokens",
                "model_calls",
                "tool_calls",
                "estimated",
            )
        }
        async with self.db.sessions.begin() as session:
            await session.execute(
                update(Run)
                .where(Run.id == run_id, Run.status == "running")
                .values(**values, heartbeat_at=now())
            )

    async def heartbeat(self, run_id: UUID) -> None:
        async with self.db.sessions.begin() as session:
            await session.execute(
                update(Run)
                .where(Run.id == run_id, Run.status == "running")
                .values(heartbeat_at=now())
            )

    async def finish_run(self, run_id: UUID, status: str, code: str | None = None) -> None:
        if status not in {"completed", "failed", "cancelled", "abandoned"}:
            raise ValueError("Invalid terminal run status")
        async with self.db.sessions.begin() as session:
            values = {"status": status, "error_code": code, "finished_at": now()}
            if status != "completed":
                values["estimated"] = True
            await session.execute(
                update(Run).where(Run.id == run_id, Run.status == "running").values(**values)
            )

    async def recover(self) -> int:
        async with self.db.sessions() as session:
            candidates = (
                await session.execute(
                    select(Run.id, Run.thread_id)
                    .where(Run.status == "running", Run.heartbeat_at < now() - timedelta(minutes=5))
                    .limit(100)
                )
            ).all()
        count = 0
        for run_id, thread_id in candidates:
            try:
                async with self.thread_lock(thread_id):
                    await self.finish_run(run_id, "abandoned", "worker_lost")
                    count += 1
            except Conflict:
                continue
        return count

    async def cleanup(self) -> int:
        await self.recover()
        async with self.db.sessions() as session:
            ids = (
                await session.scalars(
                    select(Thread.id)
                    .where(
                        or_(
                            Thread.expires_at < now(),
                            Thread.deleted_at
                            < now() - timedelta(days=self.settings.thread_retention_days),
                            (
                                Thread.created_at
                                < now() - timedelta(hours=self.settings.empty_thread_ttl_hours)
                            )
                            & ~exists().where(Run.thread_id == Thread.id),
                        )
                    )
                    .limit(100)
                )
            ).all()
        count = 0
        for thread_id in ids:
            try:
                async with self.thread_lock(thread_id), self.db.sessions.begin() as session:
                    thread = await session.scalar(
                        select(Thread).where(Thread.id == thread_id).with_for_update()
                    )
                    if thread is None or await session.scalar(
                        select(exists().where(Run.thread_id == thread_id, Run.status == "running"))
                    ):
                        continue
                    # Delete checkpoints first. A retry safely finishes a partially completed purge.
                    await self.db.checkpointer.adelete_thread(str(thread_id))
                    await session.execute(delete(Thread).where(Thread.id == thread_id))
                    count += 1
            except Conflict:
                continue
        return count
