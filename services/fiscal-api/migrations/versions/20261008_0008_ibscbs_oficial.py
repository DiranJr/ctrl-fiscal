"""ibscbs_oficial: valores de IBS/CBS calculados pela Sefin (infNFSe/IBSCBS) preservados em cada NFS-e

- documents.ibs_cbs_official (JSON): folhas do grupo oficial com o texto exato do XML + códigos da DPS;
- preenchimento das notas JÁ armazenadas a partir do XML original guardado (blobs), com o MESMO extrator usado na
  ingestão (ctrl_fiscal.nfse_xml.ibs_cbs_oficial); nenhum outro campo das notas é alterado.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-08 21:00:00
"""
import gzip

from alembic import op
import sqlalchemy as sa

import ctrl_common.db

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("ibs_cbs_official", ctrl_common.db.JsonType(), nullable=True))
    from defusedxml import ElementTree as SafeET

    from ctrl_fiscal.nfse_xml import _child, ibs_cbs_oficial

    bind = op.get_bind()
    docs = sa.table("documents", sa.column("id", sa.String), sa.column("ibs_cbs_official", ctrl_common.db.JsonType()))
    rows = bind.execute(sa.text(
        "SELECT d.id, b.content_gz FROM documents d JOIN blobs b ON b.id = d.xml_blob_id")).all()
    for doc_id, gz in rows:
        try:
            root = SafeET.fromstring(gzip.decompress(gz), forbid_dtd=True, forbid_entities=True, forbid_external=True)
            dados = ibs_cbs_oficial(_child(root, "infNFSe"))
        except Exception:  # noqa: BLE001 - XML já aceito na ingestão; falha aqui só deixa o campo vazio
            dados = None
        if dados:
            bind.execute(docs.update().where(docs.c.id == doc_id).values(ibs_cbs_official=dados))


def downgrade() -> None:
    op.drop_column("documents", "ibs_cbs_official")
