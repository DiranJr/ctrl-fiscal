"""edicao_rascunho: descontos (vDescCondIncond) e estado da validação do rascunho

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08 10:10:00
"""
from alembic import op
import sqlalchemy as sa

import ctrl_common.db

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("drafts", sa.Column("discount_unconditional", sa.Numeric(15, 2), nullable=True))
    op.add_column("drafts", sa.Column("discount_conditional", sa.Numeric(15, 2), nullable=True))
    op.add_column("drafts", sa.Column("validated_version", sa.Integer(), nullable=True))
    op.add_column("drafts", sa.Column("validated_at", ctrl_common.db.UTCDateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("drafts", "validated_at")
    op.drop_column("drafts", "validated_version")
    op.drop_column("drafts", "discount_conditional")
    op.drop_column("drafts", "discount_unconditional")
