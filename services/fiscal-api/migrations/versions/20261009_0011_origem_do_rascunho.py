"""origem_do_rascunho: origem do rascunho, configuração da operação e modo do lote

- drafts.origin (JSON, NULL): nota de referência (campos lidos do XML oficial, o que não foi copiado, pendências) ou
  preenchimento manual, e a resolução da configuração tributária da operação. Rascunhos anteriores ficam NULL.
- batches.mode (NOT NULL, padrão SCHEDULED): "Emitir agora" (NOW) ou "Agendar emissão" (SCHEDULED). Lotes anteriores
  ficam SCHEDULED, que é o que eram.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-09 14:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("drafts", sa.Column("origin", sa.JSON(), nullable=True))
    op.add_column("batches", sa.Column("mode", sa.String(16), nullable=False, server_default="SCHEDULED"))


def downgrade() -> None:
    op.drop_column("batches", "mode")
    op.drop_column("drafts", "origin")
