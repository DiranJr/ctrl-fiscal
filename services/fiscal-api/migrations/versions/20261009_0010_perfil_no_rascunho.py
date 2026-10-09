"""perfil_no_rascunho: perfil de serviço aplicado ao rascunho e a versão vigente na aplicação

Os rascunhos/lotes anteriores ficam sem perfil (NULL): nada é reinterpretado.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-09 11:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("drafts", sa.Column("service_profile_id", sa.String(36), nullable=True))
    op.add_column("drafts", sa.Column("service_profile_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("drafts", "service_profile_version")
    op.drop_column("drafts", "service_profile_id")
