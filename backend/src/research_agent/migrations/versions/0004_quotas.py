"""Daily weighted question quotas per principal and hashed address.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "quota_usage",
        sa.Column("scope", sa.String(32), primary_key=True),
        sa.Column("key_hash", sa.String(64), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("units >= 0"),
    )
    op.create_index("ix_quota_usage_day", "quota_usage", ["day"])


def downgrade():
    op.drop_index("ix_quota_usage_day", table_name="quota_usage")
    op.drop_table("quota_usage")
