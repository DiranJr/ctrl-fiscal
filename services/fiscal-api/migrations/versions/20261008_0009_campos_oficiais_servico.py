"""campos_oficiais_servico: obra (cCIB, inscImobFisc) e infoCompl nos rascunhos e nas notas

- drafts.service_extra (JSON): obra_cib, obra_property_registration, additional_info (idDocTec, docRef, xPed,
  gItemPed, xInfComp);
- documents.service_extra (JSON): os mesmos grupos lidos do XML da nota (referência para reutilização), preenchidos
  para as notas já armazenadas com o MESMO extrator da ingestão (ctrl_fiscal.nfse_xml.servico_extra).
Nada mais é alterado nos rascunhos e nas notas.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-08 22:00:00
"""
import gzip

from alembic import op
import sqlalchemy as sa

import ctrl_common.db

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("drafts", sa.Column("service_extra", ctrl_common.db.JsonType(), nullable=True))
    op.add_column("documents", sa.Column("service_extra", ctrl_common.db.JsonType(), nullable=True))
    from defusedxml import ElementTree as SafeET

    from ctrl_fiscal.nfse_xml import _path, servico_extra

    bind = op.get_bind()
    docs = sa.table("documents", sa.column("id", sa.String), sa.column("service_extra", ctrl_common.db.JsonType()))
    for doc_id, gz in bind.execute(sa.text(
            "SELECT d.id, b.content_gz FROM documents d JOIN blobs b ON b.id = d.xml_blob_id")).all():
        try:
            root = SafeET.fromstring(gzip.decompress(gz), forbid_dtd=True, forbid_entities=True, forbid_external=True)
            dados = servico_extra(_path(root, "infNFSe", "DPS", "infDPS", "serv"))
        except Exception:  # noqa: BLE001 - XML já aceito na ingestão; falha aqui só deixa o campo vazio
            dados = None
        if dados:
            bind.execute(docs.update().where(docs.c.id == doc_id).values(service_extra=dados))


def downgrade() -> None:
    op.drop_column("documents", "service_extra")
    op.drop_column("drafts", "service_extra")
