"""certificado_unico: um certificado ativo por workspace, histórico sem material secreto e certificado por operação

- certificates.active_slot ("empresa:ambiente" enquanto ativo; NULL depois) com restrição ÚNICA;
- certificates.replaced_at / replaced_by_fingerprint / material_purged_at (histórico);
- certificados INATIVOS têm o PFX e a senha cifrados EXPURGADOS (nada de material secreto desnecessário);
- certificate_locks: linha-trava por workspace para ativação/renovação concorrente;
- submissions/event_requests.certificate_fingerprint: certificado do próprio workspace usado na transmissão.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08 18:30:00
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

import ctrl_common.db

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("certificates", sa.Column("active_slot", sa.String(64), nullable=True))
    op.add_column("certificates", sa.Column("replaced_at", ctrl_common.db.UTCDateTime(), nullable=True))
    op.add_column("certificates", sa.Column("replaced_by_fingerprint", sa.String(64), nullable=True))
    op.add_column("certificates", sa.Column("material_purged_at", ctrl_common.db.UTCDateTime(), nullable=True))
    op.create_index("uq_certificate_active_slot", "certificates", ["active_slot"], unique=True)
    op.create_table("certificate_locks", sa.Column("company_id", sa.String(36), primary_key=True))
    op.add_column("submissions", sa.Column("certificate_fingerprint", sa.String(64), nullable=True))
    op.add_column("event_requests", sa.Column("certificate_fingerprint", sa.String(64), nullable=True))
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    # Ativos: ocupa o slot (se houvesse 2 ativos no mesmo empresa:ambiente, a restrição única falharia aqui).
    for cid, cert_id, env in bind.execute(sa.text(
            "SELECT company_id, id, environment FROM certificates WHERE is_active = :t ORDER BY created_at"),
            {"t": True}).all():
        bind.execute(sa.text("UPDATE certificates SET active_slot = :s WHERE id = :i"), {"s": f"{cid}:{env}", "i": cert_id})
    bind.execute(sa.text("UPDATE certificates SET encrypted_pfx = :e, encrypted_password = :e, material_purged_at = :n "
                         "WHERE is_active = :f AND material_purged_at IS NULL"), {"e": b"", "n": now, "f": False})


def downgrade() -> None:
    op.drop_column("event_requests", "certificate_fingerprint")
    op.drop_column("submissions", "certificate_fingerprint")
    op.drop_table("certificate_locks")
    op.drop_index("uq_certificate_active_slot", table_name="certificates")
    for c in ("material_purged_at", "replaced_by_fingerprint", "replaced_at", "active_slot"):
        op.drop_column("certificates", c)
