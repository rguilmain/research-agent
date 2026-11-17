"""Human verification window recorded on anonymous sessions.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "auth_sessions",
        sa.Column("human_verified_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("auth_sessions", "human_verified_until")
