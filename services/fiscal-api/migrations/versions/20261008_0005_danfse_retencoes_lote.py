"""danfse_retencoes_lote: colunas normalizadas de retenção (filtros), conjunto fixo de documentos do download em
lote, expiração do arquivo exportado e tickets de download de uso único.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-08 12:30:00

O backfill usa uma cópia CONGELADA da regra de domain/retencoes_doc.classify (mudanças futuras da regra não
alteram o resultado desta migração).
"""
from decimal import Decimal

from alembic import op
import sqlalchemy as sa

import ctrl_common.db

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_RETAINED_CODES = {"1", "3", "4", "5", "6", "7", "8", "9"}


def _d(v):  # noqa: ANN001, ANN202
    if v is None or v == "":
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _classify(ind, ret_iss, ret_inss, ret_irrf, ret_csll, total):  # noqa: ANN001, ANN202
    ind = ind or {}
    iss_status = (ind.get("iss") or {}).get("status") or "NOT_INFORMED"
    src = (ind.get("pis") or {}).get("source") or (ind.get("csll") or {}).get("source") or ""
    code = src.split("=", 1)[1] or None if src.startswith("tpRetPisCofins=") else None
    code = code or ind.get("pis_cofins_retention_code")
    v_pis = _d((ind.get("pis") or {}).get("informed_amount"))
    v_cofins = _d((ind.get("cofins") or {}).get("informed_amount"))
    inss, irrf, csll, iss = _d(ret_inss), _d(ret_irrf), _d(ret_csll), _d(ret_iss)
    if code == "1":
        parts = [v for v in (csll, v_pis, v_cofins) if v is not None]
        contrib = sum(parts, Decimal("0")) if parts else None
    else:
        contrib = csll
    has_iss = iss_status in ("RETAINED_BY_TOMADOR", "RETAINED_BY_INTERMEDIARY")
    has_inss, has_irrf, has_contrib = bool(inss and inss > 0), bool(irrf and irrf > 0), bool(contrib and contrib > 0)
    total = _d(total)
    missing = (has_iss and iss is None) or (code in _RETAINED_CODES and code != "1" and not has_contrib)
    if iss_status == "NOT_INFORMED" or missing:
        klass = "UNKNOWN"
    elif has_iss or has_inss or has_irrf or has_contrib or (total is not None and total > 0):
        klass = "WITH"
    else:
        klass = "WITHOUT"
    effective = total if total is not None else (Decimal("0") if klass == "WITHOUT" else None)
    return {"ret_class": klass, "ret_has_iss": has_iss, "ret_has_inss": has_inss, "ret_has_irrf": has_irrf,
            "ret_has_contrib": has_contrib, "ret_pis_cofins_code": code, "ret_contrib_amount": contrib,
            "ret_total_effective": effective}


def upgrade() -> None:
    op.add_column("documents", sa.Column("ret_class", sa.String(8), nullable=True))
    for c in ("ret_has_iss", "ret_has_inss", "ret_has_irrf", "ret_has_contrib"):
        op.add_column("documents", sa.Column(c, sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("documents", sa.Column("ret_pis_cofins_code", sa.String(1), nullable=True))
    op.add_column("documents", sa.Column("ret_contrib_amount", sa.Numeric(15, 2), nullable=True))
    op.add_column("documents", sa.Column("ret_total_effective", sa.Numeric(15, 2), nullable=True))
    op.create_index("ix_documents_retention", "documents", ["company_id", "environment", "ret_class"])
    op.add_column("jobs", sa.Column("output_expires_at", ctrl_common.db.UTCDateTime(), nullable=True))
    op.create_table(
        "job_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("job_id", "seq", name="uq_job_document_seq"),
    )
    op.create_index("ix_job_documents_job_id", "job_documents", ["job_id"])
    op.create_table(
        "download_tickets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("expires_at", ctrl_common.db.UTCDateTime(), nullable=False),
        sa.Column("used_at", ctrl_common.db.UTCDateTime(), nullable=True),
        sa.Column("created_at", ctrl_common.db.UTCDateTime(), nullable=False),
    )
    op.create_index("ix_download_tickets_job_id", "download_tickets", ["job_id"])

    # Backfill das colunas de retenção a partir do que já está gravado (valores do XML oficial / snapshot).
    bind = op.get_bind()
    docs = sa.table("documents", sa.column("id"), sa.column("retention_indicators", ctrl_common.db.JsonType()),
                    sa.column("ret_iss"), sa.column("ret_inss"), sa.column("ret_irrf"), sa.column("ret_csll"),
                    sa.column("total_retained"), sa.column("ret_class"), sa.column("ret_has_iss"),
                    sa.column("ret_has_inss"), sa.column("ret_has_irrf"), sa.column("ret_has_contrib"),
                    sa.column("ret_pis_cofins_code"), sa.column("ret_contrib_amount"), sa.column("ret_total_effective"))
    rows = bind.execute(sa.select(docs.c.id, docs.c.retention_indicators, docs.c.ret_iss, docs.c.ret_inss,
                                  docs.c.ret_irrf, docs.c.ret_csll, docs.c.total_retained)).all()
    for r in rows:
        vals = _classify(r.retention_indicators, r.ret_iss, r.ret_inss, r.ret_irrf, r.ret_csll, r.total_retained)
        bind.execute(docs.update().where(docs.c.id == r.id).values(**vals))


def downgrade() -> None:
    op.drop_index("ix_download_tickets_job_id", table_name="download_tickets")
    op.drop_table("download_tickets")
    op.drop_index("ix_job_documents_job_id", table_name="job_documents")
    op.drop_table("job_documents")
    op.drop_column("jobs", "output_expires_at")
    op.drop_index("ix_documents_retention", table_name="documents")
    for c in ("ret_total_effective", "ret_contrib_amount", "ret_pis_cofins_code", "ret_has_contrib", "ret_has_irrf",
              "ret_has_inss", "ret_has_iss", "ret_class"):
        op.drop_column("documents", c)
