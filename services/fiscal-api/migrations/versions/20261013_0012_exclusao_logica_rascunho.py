"""exclusao_logica_rascunho: exclusão lógica de rascunhos

- drafts.deleted_at (UTC, NULL) e drafts.deleted_by (NULL): quando e por quem o rascunho foi excluído. A linha, as
  versões, a origem e a auditoria são preservadas; o rascunho excluído some da lista e fica indisponível para edição,
  validação e emissão. Rascunhos existentes ficam NULL (não excluídos).
- Só acrescenta colunas que aceitam nulo: a versão anterior continua funcionando com o banco migrado (ignora as
  colunas; num rollback de aplicação os rascunhos excluídos voltariam a aparecer como rascunhos comuns).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-13 09:00:00
"""
from alembic import op
import sqlalchemy as sa

import ctrl_common.db

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("drafts", sa.Column("deleted_at", ctrl_common.db.UTCDateTime(), nullable=True))
    op.add_column("drafts", sa.Column("deleted_by", sa.String(36), nullable=True))


def downgrade() -> None:
    op.drop_column("drafts", "deleted_by")
    op.drop_column("drafts", "deleted_at")
