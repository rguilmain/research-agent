"""Application-owned metadata, separate from LangGraph's versioned checkpoint schema."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Principal(Base):
    __tablename__ = "principals"
    __table_args__ = (CheckConstraint("kind IN ('anonymous', 'user')"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Thread(Base):
    __tablename__ = "threads"
    __table_args__ = (Index("ix_threads_owner_updated", "owner_id", "updated_at"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("principals.id"))
    title: Mapped[str] = mapped_column(String(200), default="New research")
    model_id: Mapped[str] = mapped_column(String(100))
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class User(Base):
    """A signed-in principal. Only public GitHub profile fields are stored."""

    __tablename__ = "users"
    principal_id: Mapped[UUID] = mapped_column(ForeignKey("principals.id"), primary_key=True)
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    login: Mapped[str] = mapped_column(String(64))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuthSession(Base):
    """Opaque session. Only an HMAC of the cookie token is stored."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("kind IN ('anonymous', 'user')"),
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    principal_id: Mapped[UUID] = mapped_column(ForeignKey("principals.id"))
    kind: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set after a successful Turnstile check; never persisted for signed-in principals.
    human_verified_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthState(Base):
    """Single-use, browser-bound OAuth state with its PKCE verifier."""

    __tablename__ = "oauth_states"
    __table_args__ = (Index("ix_oauth_states_expires_at", "expires_at"),)
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    code_verifier: Mapped[str] = mapped_column(String(128))
    next_path: Mapped[str] = mapped_column(String(512), default="/")
    browser_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RateLimitCounter(Base):
    """One counter per scope, hashed key and fixed window.

    Keys are HMACs of a client address, principal or token: raw IPs are never stored.
    Rows are incremented atomically with a single upsert, so limits hold across processes.
    """

    __tablename__ = "rate_limit_counters"
    __table_args__ = (
        CheckConstraint("count >= 0"),
        Index("ix_rate_limit_counters_window", "window_start"),
    )
    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class QuotaUsage(Base):
    """One daily weighted-unit counter per scope and hashed key.

    Keys are HMACs of a principal or client address: raw identities are never stored.
    A single conditional upsert adds units only when they still fit, so parallel workers
    can neither lose an increment nor over-admit. It is deliberately separate from
    ``rate_limit_counters`` because hourly rate-limit cleanup would erase a live day.
    """

    __tablename__ = "quota_usage"
    __table_args__ = (
        CheckConstraint("units >= 0"),
        Index("ix_quota_usage_day", "day"),
    )
    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    units: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BudgetDay(Base):
    """One row per UTC day holding the reserved and settled spend totals.

    ``reserved_usd`` is the sum of open reservations; ``spent_usd`` is the sum of settled
    and expired charges. A daily admission check reads the difference, and a single conditional
    upsert raises ``reserved_usd`` only while the day still has room.
    """

    __tablename__ = "budget_days"
    __table_args__ = (CheckConstraint("reserved_usd >= 0 AND spent_usd >= 0"),)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    spent_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BudgetReservation(Base):
    """One paid operation's worst-case hold, settled or expired exactly once.

    Money is stored to six decimal places and always rounded up. A reservation whose worker
    died stays ``open`` until its expiry passes, so the day can be reconciled conservatively.
    """

    __tablename__ = "budget_reservations"
    __table_args__ = (
        CheckConstraint("kind IN ('model', 'tool')"),
        CheckConstraint("status IN ('open', 'settled', 'expired')"),
        CheckConstraint("reserved_usd >= 0 AND (actual_usd IS NULL OR actual_usd >= 0)"),
        Index("ix_budget_reservations_open", "status", "expires_at"),
        Index("ix_budget_reservations_run", "run_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    day: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(16))
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    actual_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint("status IN ('running','completed','failed','cancelled','abandoned')"),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND cached_input_tokens >= 0 "
            "AND cached_input_tokens <= input_tokens AND reasoning_tokens >= 0 "
            "AND reasoning_tokens <= output_tokens AND model_calls >= 0"
        ),
        Index("ix_runs_thread_created", "thread_id", "created_at"),
        Index(
            "ix_runs_one_active",
            "thread_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    thread_id: Mapped[UUID] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("principals.id"))
    model_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="running")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[dict] = mapped_column(JSONB, default=dict)
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
