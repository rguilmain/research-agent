"""Daily budget ledger: reservations per paid operation and daily totals.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def money(name, nullable=False):
    return sa.Column(name, sa.Numeric(12, 6), nullable=nullable)


def timestamp(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade():
    op.create_table(
        "budget_days",
        sa.Column("day", sa.Date(), primary_key=True),
        money("reserved_usd"),
        money("spent_usd"),
        timestamp("updated_at"),
        sa.CheckConstraint("reserved_usd >= 0 AND spent_usd >= 0"),
    )
    op.create_table(
        "budget_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        money("reserved_usd"),
        money("actual_usd", nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        timestamp("created_at"),
        timestamp("expires_at"),
        timestamp("settled_at", nullable=True),
        sa.CheckConstraint("kind IN ('model', 'tool')"),
        sa.CheckConstraint("status IN ('open', 'settled', 'expired')"),
        sa.CheckConstraint("reserved_usd >= 0 AND (actual_usd IS NULL OR actual_usd >= 0)"),
    )
    op.create_index("ix_budget_reservations_open", "budget_reservations", ["status", "expires_at"])
    op.create_index("ix_budget_reservations_run", "budget_reservations", ["run_id"])


def downgrade():
    op.drop_index("ix_budget_reservations_run", table_name="budget_reservations")
    op.drop_index("ix_budget_reservations_open", table_name="budget_reservations")
    op.drop_table("budget_reservations")
    op.drop_table("budget_days")
