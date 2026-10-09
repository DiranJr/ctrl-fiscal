"""substituicao_nfse: cStat 101 + vínculos de substituição + evento que cancelou

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-07 17:45:00
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("substitutes_access_key", sa.String(length=50), nullable=True))
    op.add_column("documents", sa.Column("substitution_reason", sa.String(length=2), nullable=True))
    op.add_column("documents", sa.Column("substituted_by_access_key", sa.String(length=50), nullable=True))
    op.add_column("documents", sa.Column("cancellation_event_type", sa.String(length=10), nullable=True))
    op.add_column("document_events", sa.Column("substitute_access_key", sa.String(length=50), nullable=True))
    # Estado atual derivado de cStat + eventos (sem reprocessar XML): cStat 101 = NFS-e de Substituição Gerada.
    op.execute("UPDATE documents SET status = 'AUTHORIZED' WHERE status = 'UNKNOWN' AND status_code = '101' "
               "AND id NOT IN (SELECT document_id FROM document_events WHERE cancels_document = 1 AND document_id IS NOT NULL)")
    op.execute("UPDATE documents SET status = 'CANCELLED' WHERE status_code = '101' "
               "AND id IN (SELECT document_id FROM document_events WHERE cancels_document = 1 AND document_id IS NOT NULL)")


def downgrade() -> None:
    op.drop_column("document_events", "substitute_access_key")
    op.drop_column("documents", "cancellation_event_type")
    op.drop_column("documents", "substituted_by_access_key")
    op.drop_column("documents", "substitution_reason")
    op.drop_column("documents", "substitutes_access_key")
