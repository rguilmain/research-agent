"""Owner-scoped storage, sessions and cross-process run exclusion."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, delete, exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from research_agent.budget import BudgetExhausted
from research_agent.config import Settings
from research_agent.db import Database
from research_agent.history import History, public_messages
from research_agent.storage_models import (
    AuthSession,
    BudgetDay,
    BudgetReservation,
    OAuthState,
    Principal,
    QuotaUsage,
    RateLimitCounter,
    Run,
    Thread,
    User,
    now,
)

SESSION_TOUCH_INTERVAL = timedelta(minutes=1)
OAUTH_STATE_TTL = timedelta(minutes=10)
# One aligned window per hour, and one extra window kept for Retry-After accuracy.
RATE_LIMIT_WINDOW = timedelta(hours=1)
RATE_LIMIT_RETENTION_WINDOWS = 2
# A run whose heartbeat is older than this is treated as gone for concurrency purposes.
ACTIVE_RUN_FRESHNESS = timedelta(minutes=2)
# Transaction-scoped advisory lock that serializes run admission against the concurrency caps.
RUN_ADMISSION_LOCK = 0x52_41_44_4D
# Session advisory lock so only one process runs retention cleanup at a time.
MAINTENANCE_LOCK = 0x43_4C_45_41_4E
BUDGET_RESERVATION_RETENTION_DAYS = 30
BUDGET_CLEANUP_BATCH = 1000


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
    human_verified_until: datetime | None = None


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


class _QuotaRefused(Exception):
    """Internal marker: a quota key is full, so the whole consume must roll back."""

    pass


class ConcurrencyExceeded(Exception):
    """Too many live runs are already in progress for the service or the caller."""


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

    @asynccontextmanager
    async def maintenance_lock(self):
        """Session advisory lock so only one process runs retention cleanup at a time.

        Shares the rules of ``thread_lock``: a dedicated connection owns the lock for the
        whole pass, connection close or process death releases it, and it requires
        direct/session-mode connections rather than a transaction-mode DB proxy.
        """
        async with self.db.engine.connect() as connection:
            locked = await connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": MAINTENANCE_LOCK}
            )
            # The query autobegins a transaction, so commit it; the lock is session-scoped
            # and survives. Then hold an open transaction: an idle connection could be handed
            # to the cleanup pass itself, and a PostgreSQL session advisory lock is re-entrant
            # for the session that already holds it.
            await connection.commit()
            if not locked:
                raise Conflict("Maintenance is already running")
            root_transaction = await connection.begin()
            try:
                yield
            finally:
                try:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": MAINTENANCE_LOCK}
                    )
                except BaseException:
                    # Closing the connection releases the session lock; never pool a
                    # connection that may still hold it.
                    await connection.invalidate()
                    raise
                finally:
                    try:
                        await root_transaction.rollback()
                        await connection.commit()
                    except BaseException:
                        await connection.invalidate()
                        raise

    async def start_run(
        self,
        owner: Identity,
        thread_id: UUID,
        model_id: str,
        max_active: int | None = None,
        max_per_principal: int | None = None,
    ) -> UUID:
        async with self.db.sessions.begin() as session:
            thread = await session.scalar(self.owned(owner, thread_id).with_for_update())
            if thread is None:
                raise NotFound
            if thread.model_id != model_id or thread.demo != self.settings.demo_mode:
                raise Conflict("Thread model or mode differs; start a new thread")
            if max_active is not None or max_per_principal is not None:
                # A transaction-scoped lock serializes admission, so parallel workers cannot
                # both read a count below the cap and then both start a run.
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"), {"key": RUN_ADMISSION_LOCK}
                )
                live = (Run.status == "running", Run.heartbeat_at > now() - ACTIVE_RUN_FRESHNESS)
                active = await session.scalar(select(func.count()).select_from(Run).where(*live))
                mine = await session.scalar(
                    select(func.count()).select_from(Run).where(*live, Run.owner_id == owner.id)
                )
                if (max_active is not None and (active or 0) >= max_active) or (
                    max_per_principal is not None and (mine or 0) >= max_per_principal
                ):
                    raise ConcurrencyExceeded("Too many active runs")
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
        # Charge abandoned budget holds before anything else so the day stays accurate.
        await self.expire_stale_reservations()
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

    async def consume_rate_limit(
        self, scope: str, key: str, window_start: datetime, limit: int
    ) -> tuple[bool, int]:
        """Atomically count one attempt and report whether the window still allows it.

        The counter is incremented before any work is admitted, so an attempt that later
        fails still consumes budget: this never over-admits. One statement means parallel
        workers cannot lose an increment.
        """
        async with self.db.sessions.begin() as session:
            count = (
                await session.execute(
                    insert(RateLimitCounter)
                    .values(
                        scope=scope,
                        key_hash=key,
                        window_start=window_start,
                        count=1,
                        updated_at=now(),
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            RateLimitCounter.scope,
                            RateLimitCounter.key_hash,
                            RateLimitCounter.window_start,
                        ],
                        set_={"count": RateLimitCounter.count + 1, "updated_at": now()},
                    )
                    .returning(RateLimitCounter.count)
                )
            ).scalar_one()
        expires = window_start + RATE_LIMIT_WINDOW
        retry_after = max(1, int((expires - now()).total_seconds()))
        return count <= limit, retry_after

    async def cleanup_rate_limits(self) -> int:
        """Delete counter rows outside the retention windows; safe to run repeatedly."""
        cutoff = now() - RATE_LIMIT_WINDOW * RATE_LIMIT_RETENTION_WINDOWS
        async with self.db.sessions.begin() as session:
            result = cast(
                "CursorResult[Any]",
                await session.execute(
                    delete(RateLimitCounter).where(RateLimitCounter.window_start < cutoff)
                ),
            )
            return result.rowcount or 0

    async def consume_quota(
        self, day: date, keys: list[tuple[str, str]], units: int, limit: int
    ) -> int | None:
        """Add units to every key atomically, or change nothing when any key is full.

        Returns the smallest remaining allowance across the keys, or ``None`` when the
        request is refused. A refused request leaves every counter untouched, so a caller
        who runs out of quota is never charged for it. Keys are processed in sorted order
        so concurrent requests sharing keys cannot deadlock.
        """
        if units > limit:
            return None
        try:
            async with self.db.sessions.begin() as session:
                remaining = limit
                for scope, key in sorted(keys):
                    statement = (
                        insert(QuotaUsage)
                        .values(scope=scope, key_hash=key, day=day, units=units, updated_at=now())
                        .on_conflict_do_update(
                            index_elements=[
                                QuotaUsage.scope,
                                QuotaUsage.key_hash,
                                QuotaUsage.day,
                            ],
                            set_={"units": QuotaUsage.units + units, "updated_at": now()},
                            where=QuotaUsage.units + units <= limit,
                        )
                        .returning(QuotaUsage.units)
                    )
                    used = (await session.execute(statement)).scalar_one_or_none()
                    if used is None:
                        # Roll back increments already made to earlier keys in this block.
                        raise _QuotaRefused
                    remaining = min(remaining, limit - used)
        except _QuotaRefused:
            return None
        return remaining

    async def refund_quota(self, day: date, keys: list[tuple[str, str]], units: int) -> None:
        """Return units after a request failed before it produced any work."""
        async with self.db.sessions.begin() as session:
            for scope, key in sorted(keys):
                await session.execute(
                    update(QuotaUsage)
                    .where(
                        QuotaUsage.scope == scope,
                        QuotaUsage.key_hash == key,
                        QuotaUsage.day == day,
                    )
                    .values(units=func.greatest(QuotaUsage.units - units, 0), updated_at=now())
                )

    async def cleanup_quotas(self) -> int:
        """Delete quota days older than yesterday (UTC); safe to run repeatedly."""
        cutoff = now().date() - timedelta(days=1)
        async with self.db.sessions.begin() as session:
            result = cast(
                "CursorResult[Any]",
                await session.execute(delete(QuotaUsage).where(QuotaUsage.day < cutoff)),
            )
            return result.rowcount or 0

    async def reserve_budget(
        self,
        run_id: UUID | None,
        kind: str,
        amount: Decimal,
        limit: Decimal,
        ttl: timedelta,
    ) -> UUID:
        """Atomically hold ``amount`` for one paid operation, or raise BudgetExhausted.

        The day total is raised only while ``reserved + spent + amount`` still fits the limit,
        so parallel workers cannot over-reserve. A refusal changes nothing at all.
        """
        if amount > limit:
            raise BudgetExhausted("Reservation exceeds the whole daily budget")
        day = now().date()
        async with self.db.sessions.begin() as session:
            raised = (
                await session.execute(
                    insert(BudgetDay)
                    .values(day=day, reserved_usd=amount, spent_usd=Decimal(0), updated_at=now())
                    .on_conflict_do_update(
                        index_elements=[BudgetDay.day],
                        set_={
                            "reserved_usd": BudgetDay.reserved_usd + amount,
                            "updated_at": now(),
                        },
                        where=BudgetDay.reserved_usd + BudgetDay.spent_usd + amount <= limit,
                    )
                    .returning(BudgetDay.day)
                )
            ).scalar_one_or_none()
            if raised is None:
                raise BudgetExhausted("Daily budget exhausted")
            reservation = BudgetReservation(
                run_id=run_id,
                day=day,
                kind=kind,
                reserved_usd=amount,
                status="open",
                created_at=now(),
                expires_at=now() + ttl,
            )
            session.add(reservation)
            await session.flush()
            return reservation.id

    async def settle_budget(
        self, reservation_id: UUID, actual: Decimal, status: str = "settled"
    ) -> None:
        """Charge a held reservation once; a second call is a no-op.

        The hold is released and ``actual`` is added to the day's spent total. ``actual`` may
        exceed the hold (an upstream bill can outgrow our estimate); the overflow is recorded.
        """
        if status not in {"settled", "expired"}:
            raise ValueError("Invalid budget settlement status")
        async with self.db.sessions.begin() as session:
            row = (
                await session.execute(
                    update(BudgetReservation)
                    .where(
                        BudgetReservation.id == reservation_id, BudgetReservation.status == "open"
                    )
                    .values(status=status, actual_usd=actual, settled_at=now())
                    .returning(BudgetReservation.day, BudgetReservation.reserved_usd)
                )
            ).first()
            if row is None:
                return
            await session.execute(
                update(BudgetDay)
                .where(BudgetDay.day == row.day)
                .values(
                    reserved_usd=func.greatest(BudgetDay.reserved_usd - row.reserved_usd, 0),
                    spent_usd=BudgetDay.spent_usd + actual,
                    updated_at=now(),
                )
            )

    async def settle_open_for_run(self, run_id: UUID) -> int:
        """Charge every still-open reservation for a run at its full held amount."""
        async with self.db.sessions() as session:
            rows = (
                await session.execute(
                    select(BudgetReservation.id, BudgetReservation.reserved_usd).where(
                        BudgetReservation.run_id == run_id,
                        BudgetReservation.status == "open",
                    )
                )
            ).all()
        for reservation_id, reserved in rows:
            await self.settle_budget(reservation_id, reserved)
        return len(rows)

    async def expire_stale_reservations(self) -> int:
        """Charge abandoned holds whose worker died, newest first, at most one batch."""
        async with self.db.sessions() as session:
            rows = (
                await session.execute(
                    select(BudgetReservation.id, BudgetReservation.reserved_usd)
                    .where(
                        BudgetReservation.status == "open",
                        BudgetReservation.expires_at < now(),
                    )
                    .limit(100)
                )
            ).all()
        for reservation_id, reserved in rows:
            await self.settle_budget(reservation_id, reserved, status="expired")
        return len(rows)

    async def budget_headroom(self, limit: Decimal) -> Decimal:
        """Remaining spendable amount for today; a missing day row means the full budget."""
        async with self.db.sessions() as session:
            row = await session.scalar(
                select(BudgetDay.reserved_usd + BudgetDay.spent_usd).where(
                    BudgetDay.day == now().date()
                )
            )
        committed = row if isinstance(row, Decimal) else Decimal(0)
        return max(limit - committed, Decimal(0))

    async def cleanup_budget_reservations(self) -> int:
        """Delete closed reservation rows older than the retention window, one bounded batch.

        Day totals stay: they are the spending ledger. Only the per-operation detail expires.
        """
        cutoff = now() - timedelta(days=BUDGET_RESERVATION_RETENTION_DAYS)
        async with self.db.sessions.begin() as session:
            ids = (
                await session.scalars(
                    select(BudgetReservation.id)
                    .where(
                        BudgetReservation.status != "open",
                        BudgetReservation.created_at < cutoff,
                    )
                    .limit(BUDGET_CLEANUP_BATCH)
                )
            ).all()
            if not ids:
                return 0
            result = cast(
                "CursorResult[Any]",
                await session.execute(
                    delete(BudgetReservation).where(BudgetReservation.id.in_(ids))
                ),
            )
            return result.rowcount or 0

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
                human_verified_until=row.human_verified_until,
            )

    async def mark_human_verified(self, token_hash: str, until: datetime) -> bool:
        """Record a successful Turnstile check on a live anonymous session only.

        The stored window never outlives the session itself. Returns False when the session
        is missing, revoked, expired, or belongs to a signed-in principal.
        """
        async with self.db.sessions.begin() as session:
            result = cast(
                "CursorResult[Any]",
                await session.execute(
                    update(AuthSession)
                    .where(
                        AuthSession.token_hash == token_hash,
                        AuthSession.kind == "anonymous",
                        AuthSession.revoked_at.is_(None),
                        AuthSession.expires_at > now(),
                    )
                    .values(human_verified_until=func.least(until, AuthSession.expires_at))
                ),
            )
            return (result.rowcount or 0) == 1

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
        # Counters expire on their own schedule and must never accumulate without bound.
        removed += await self.cleanup_rate_limits()
        removed += await self.cleanup_quotas()
        removed += await self.cleanup_budget_reservations()
        return removed
