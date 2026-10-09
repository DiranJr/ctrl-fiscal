"""super_admin_usuarios: papel global SUPER_ADMIN, versão do usuário e tokens de ativação/redefinição

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08 13:20:00
"""
from alembic import op
import sqlalchemy as sa

import ctrl_common.db

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_super_admin", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("password_set_at", ctrl_common.db.UTCDateTime(), nullable=True))
    op.add_column("users", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.create_table(
        "credential_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purpose", sa.String(10), nullable=False),
        sa.Column("expires_at", ctrl_common.db.UTCDateTime(), nullable=False),
        sa.Column("used_at", ctrl_common.db.UTCDateTime(), nullable=True),
        sa.Column("revoked_at", ctrl_common.db.UTCDateTime(), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", ctrl_common.db.UTCDateTime(), nullable=False),
    )
    op.create_index("ix_credential_tokens_user_id", "credential_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_credential_tokens_user_id", table_name="credential_tokens")
    op.drop_table("credential_tokens")
    op.drop_column("users", "version")
    op.drop_column("users", "password_set_at")
    op.drop_column("users", "is_super_admin")
