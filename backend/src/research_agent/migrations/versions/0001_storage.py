"""Initial owner-scoped thread and run records.

Revision ID: 0001
Revises: None
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def timestamp(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade():
    op.create_table(
        "principals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        timestamp("created_at"),
        sa.CheckConstraint("kind IN ('anonymous', 'user')"),
    )
    op.create_table(
        "threads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("demo", sa.Boolean(), nullable=False),
        timestamp("created_at"),
        timestamp("updated_at"),
        timestamp("expires_at", True),
        timestamp("deleted_at", True),
    )
    op.create_index("ix_threads_owner_updated", "threads", ["owner_id", "updated_at"])
    op.create_index("ix_threads_expires_at", "threads", ["expires_at"])
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "thread_id", sa.Uuid(), sa.ForeignKey("threads.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        *[
            sa.Column(name, sa.Integer(), nullable=False)
            for name in (
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "reasoning_tokens",
                "model_calls",
            )
        ],
        sa.Column("tool_calls", JSONB(), nullable=False),
        sa.Column("estimated", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        timestamp("created_at"),
        timestamp("heartbeat_at"),
        timestamp("finished_at", True),
        sa.CheckConstraint("status IN ('running','completed','failed','cancelled','abandoned')"),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND cached_input_tokens >= 0 "
            "AND cached_input_tokens <= input_tokens AND reasoning_tokens >= 0 "
            "AND reasoning_tokens <= output_tokens AND model_calls >= 0"
        ),
    )
    op.create_index("ix_runs_thread_created", "runs", ["thread_id", "created_at"])
    op.create_index(
        "ix_runs_one_active",
        "runs",
        ["thread_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade():
    op.drop_table("runs")
    op.drop_table("threads")
    op.drop_table("principals")
