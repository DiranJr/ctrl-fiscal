"""permissao_criar_workspace: permissão GLOBAL create_workspace por usuário

- users.can_create_workspace (NOT NULL, padrão false): concedida/revogada só pelo SUPER_ADMIN. Permite cadastrar
  empresas novas (o criador vira admin SÓ do workspace criado); não dá acesso aos workspaces existentes nem
  privilégios de SUPER_ADMIN. Usuários existentes ficam sem a permissão.
- Só acrescenta coluna com padrão: a versão anterior continua funcionando com o banco migrado (ignora a coluna).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-13 15:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("can_create_workspace", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("users", "can_create_workspace")
