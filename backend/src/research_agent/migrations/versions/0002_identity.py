"""Users, hashed sessions and single-use OAuth states.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def timestamp(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade():
    op.create_table(
        "users",
        sa.Column("principal_id", sa.Uuid(), sa.ForeignKey("principals.id"), primary_key=True),
        sa.Column("github_id", sa.BigInteger(), nullable=False),
        sa.Column("login", sa.String(64), nullable=False),
        sa.Column("avatar_url", sa.String(500)),
        timestamp("created_at"),
        timestamp("last_login_at"),
    )
    op.create_unique_constraint("uq_users_github_id", "users", ["github_id"])
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("principal_id", sa.Uuid(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        timestamp("created_at"),
        timestamp("last_seen_at"),
        timestamp("expires_at"),
        timestamp("revoked_at", True),
        sa.CheckConstraint("kind IN ('anonymous', 'user')"),
    )
    op.create_unique_constraint("uq_auth_sessions_token_hash", "auth_sessions", ["token_hash"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table(
        "oauth_states",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("code_verifier", sa.String(128), nullable=False),
        sa.Column("next_path", sa.String(512), nullable=False),
        sa.Column("browser_hash", sa.String(64), nullable=False),
        timestamp("created_at"),
        timestamp("expires_at"),
        timestamp("consumed_at", True),
    )
    op.create_index("ix_oauth_states_expires_at", "oauth_states", ["expires_at"])


def downgrade():
    op.drop_index("ix_oauth_states_expires_at", table_name="oauth_states")
    op.drop_table("oauth_states")
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_constraint("uq_auth_sessions_token_hash", "auth_sessions", type_="unique")
    op.drop_table("auth_sessions")
    op.drop_constraint("uq_users_github_id", "users", type_="unique")
    op.drop_table("users")
