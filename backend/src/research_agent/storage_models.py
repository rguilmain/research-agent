"""Application-owned metadata, separate from LangGraph's versioned checkpoint schema."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
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
