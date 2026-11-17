"""Owner-scoped storage, sessions and cross-process run exclusion."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, delete, exists, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from research_agent.config import Settings
from research_agent.db import Database
from research_agent.history import History, public_messages
from research_agent.storage_models import AuthSession, OAuthState, Principal, Run, Thread, User, now

SESSION_TOUCH_INTERVAL = timedelta(minutes=1)
OAUTH_STATE_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class Identity:
    id: UUID
    kind: str = "anonymous"


@dataclass(frozen=True)
class SessionRecord:
    token_hash: str
    principal_id: UUID
    kind: str
    expires_at: datetime
    login: str | None = None
    avatar_url: str | None = None


@dataclass(frozen=True)
class OAuthStateRecord:
    code_verifier: str
    next_path: str


@dataclass(frozen=True)
class GitHubProfile:
    github_id: int
    login: str
    avatar_url: str | None


@dataclass(frozen=True)
class LoginResult:
    session: SessionRecord
    claimed_threads: int


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

    async def create_anonymous_session(self, token_hash: str) -> SessionRecord:
        """Create an anonymous principal and its session for a browser without one."""
        expires_at = now() + timedelta(hours=self.settings.anon_thread_ttl_hours)
        async with self.db.sessions.begin() as session:
            principal = Principal(kind="anonymous")
            session.add(principal)
            await session.flush()
            row = AuthSession(
                token_hash=token_hash,
                principal_id=principal.id,
                kind="anonymous",
                expires_at=expires_at,
            )
            session.add(row)
            await session.flush()
            return SessionRecord(
                token_hash=token_hash,
                principal_id=principal.id,
                kind="anonymous",
                expires_at=expires_at,
            )

    async def resolve_session(self, token_hash: str) -> SessionRecord | None:
        """Return a live session, enforcing absolute and idle expiry."""
        async with self.db.sessions.begin() as session:
            row = await session.scalar(
                select(AuthSession).where(AuthSession.token_hash == token_hash)
            )
            if row is None or row.revoked_at is not None or row.expires_at <= now():
                return None
            login = avatar_url = None
            if row.kind == "user":
                user = await session.get(User, row.principal_id)
                if user is None:
                    return None
                idle_deadline = row.last_seen_at + timedelta(
                    days=self.settings.user_session_idle_days
                )
                if idle_deadline <= now():
                    return None
                login, avatar_url = user.login, user.avatar_url
            if now() - row.last_seen_at >= SESSION_TOUCH_INTERVAL:
                await session.execute(
                    update(AuthSession).where(AuthSession.id == row.id).values(last_seen_at=now())
                )
            return SessionRecord(
                token_hash=row.token_hash,
                principal_id=row.principal_id,
                kind=row.kind,
                expires_at=row.expires_at,
                login=login,
                avatar_url=avatar_url,
            )

    async def revoke_session(self, token_hash: str) -> None:
        async with self.db.sessions.begin() as session:
            await session.execute(
                update(AuthSession)
                .where(AuthSession.token_hash == token_hash, AuthSession.revoked_at.is_(None))
                .values(revoked_at=now())
            )

    async def create_oauth_state(
        self, state_hash: str, code_verifier: str, next_path: str, browser_hash: str
    ) -> None:
        async with self.db.sessions.begin() as session:
            session.add(
                OAuthState(
                    state_hash=state_hash,
                    code_verifier=code_verifier,
                    next_path=next_path,
                    browser_hash=browser_hash,
                    expires_at=now() + OAUTH_STATE_TTL,
                )
            )

    async def consume_oauth_state(
        self, state_hash: str, browser_hash: str
    ) -> OAuthStateRecord | None:
        """Atomically consume a single-use state; replay and wrong browsers get None."""
        async with self.db.sessions.begin() as session:
            row = (
                await session.execute(
                    update(OAuthState)
                    .where(
                        OAuthState.state_hash == state_hash,
                        OAuthState.browser_hash == browser_hash,
                        OAuthState.consumed_at.is_(None),
                        OAuthState.expires_at > now(),
                    )
                    .values(consumed_at=now())
                    .returning(OAuthState.code_verifier, OAuthState.next_path)
                )
            ).first()
            if row is None:
                return None
            return OAuthStateRecord(code_verifier=row.code_verifier, next_path=row.next_path)

    async def login_github_user(
        self, profile: GitHubProfile, token_hash: str, anonymous: Identity | None
    ) -> LoginResult:
        """Attach a GitHub profile to a principal and rotate the browser's session.

        The anonymous session is revoked and its live threads (with their runs) move to
        the user in one transaction, so nothing is claimed twice or left ownerless.
        """
        expires_at = now() + timedelta(days=self.settings.user_session_max_days)
        async with self.db.sessions.begin() as session:
            candidate = uuid4()
            await session.execute(
                insert(Principal)
                .values(id=candidate, kind="user")
                .on_conflict_do_nothing(index_elements=[Principal.id])
            )
            stamp = now()
            principal_id = (
                await session.execute(
                    insert(User)
                    .values(
                        principal_id=candidate,
                        github_id=profile.github_id,
                        login=profile.login,
                        avatar_url=profile.avatar_url,
                        created_at=stamp,
                        last_login_at=stamp,
                    )
                    .on_conflict_do_update(
                        index_elements=[User.github_id],
                        set_={
                            "login": profile.login,
                            "avatar_url": profile.avatar_url,
                            "last_login_at": stamp,
                        },
                    )
                    .returning(User.principal_id)
                )
            ).scalar_one()
            if principal_id != candidate:
                # A concurrent login won the upsert; drop the principal we just created.
                await session.execute(delete(Principal).where(Principal.id == candidate))

            claimed = 0
            if anonymous is not None and anonymous.id != principal_id:
                thread_ids = (
                    await session.scalars(
                        select(Thread.id)
                        .where(
                            Thread.owner_id == anonymous.id,
                            Thread.deleted_at.is_(None),
                            or_(Thread.expires_at.is_(None), Thread.expires_at > now()),
                        )
                        .with_for_update()
                    )
                ).all()
                if thread_ids:
                    await session.execute(
                        update(Thread)
                        .where(Thread.id.in_(thread_ids))
                        .values(
                            owner_id=principal_id,
                            expires_at=now() + timedelta(days=self.settings.thread_retention_days),
                        )
                    )
                    await session.execute(
                        update(Run)
                        .where(Run.thread_id.in_(thread_ids))
                        .values(owner_id=principal_id)
                    )
                    claimed = len(thread_ids)
                await session.execute(
                    update(AuthSession)
                    .where(
                        AuthSession.principal_id == anonymous.id,
                        AuthSession.revoked_at.is_(None),
                    )
                    .values(revoked_at=now())
                )

            session.add(
                AuthSession(
                    token_hash=token_hash,
                    principal_id=principal_id,
                    kind="user",
                    expires_at=expires_at,
                )
            )
            return LoginResult(
                session=SessionRecord(
                    token_hash=token_hash,
                    principal_id=principal_id,
                    kind="user",
                    expires_at=expires_at,
                    login=profile.login,
                    avatar_url=profile.avatar_url,
                ),
                claimed_threads=claimed,
            )

    async def cleanup_identity(self) -> int:
        """Purge dead sessions, dead OAuth states and anonymous principals with no traces."""
        removed = 0
        async with self.db.sessions.begin() as session:
            for statement in (
                delete(AuthSession).where(AuthSession.expires_at < now()),
                delete(OAuthState).where(OAuthState.expires_at < now()),
            ):
                result = cast("CursorResult[Any]", await session.execute(statement))
                removed += result.rowcount or 0
        stale_principals = (
            select(Principal.id)
            .where(
                Principal.kind == "anonymous",
                ~exists().where(
                    AuthSession.principal_id == Principal.id,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > now(),
                ),
                ~exists().where(Thread.owner_id == Principal.id),
                ~exists().where(User.principal_id == Principal.id),
            )
            .limit(100)
        )
        async with self.db.sessions.begin() as session:
            ids = (await session.scalars(stale_principals)).all()
            if ids:
                # Remove leftover dead sessions first; the foreign key has no cascade.
                await session.execute(delete(AuthSession).where(AuthSession.principal_id.in_(ids)))
                result = cast(
                    "CursorResult[Any]",
                    await session.execute(delete(Principal).where(Principal.id.in_(ids))),
                )
                removed += result.rowcount or 0
        return removed
