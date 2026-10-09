"""parametros_ibs_cbs: alíquotas-padrão de IBS/CBS com vigência, fonte oficial e autorização (2026)

Autorização do titular (08/10/2026): usar as alíquotas oficiais de TRANSIÇÃO de 2026 como padrão, SOMENTE para
fatos geradores de 2026. Nada é cadastrado para 2027 em diante (IT 2025.002 v1.60, tabela 05: "Aguardar Legislação").
Fontes: EC 132/2023 (ADCT art. 125); LC 214/2025, arts. 343 (IBS 0,1% ESTADUAL; não há alíquota municipal em 2026)
e 346 (CBS 0,9%); IT 2025.002 v1.60 (22/06/2026), tabela 05 (pIBSUF 0,1 | pIBSMun 0 | pCBS 0,9).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-08 16:20:00
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from alembic import op
import sqlalchemy as sa

import ctrl_common.db

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

FONTE = ("EC 132/2023 (ADCT art. 125); LC 214/2025, arts. 343 e 346; IT 2025.002 v1.60 (22/06/2026), tabela 05 "
         "- alíquotas padrão 2026 (pIBSUF 0,1; pIBSMun 0; pCBS 0,9). Consultado em 08/10/2026.")
AUTORIZACAO = "Autorizado pelo titular (Junior) em 08/10/2026 como padrão do CTRL Fiscal, somente para fatos geradores de 2026."
LINHAS = [("IBS", "UF", Decimal("0.1000")), ("IBS", "MUN", Decimal("0.0000")), ("CBS", "FED", Decimal("0.9000"))]


def upgrade() -> None:
    t = op.create_table(
        "tax_rate_parameters",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tributo", sa.String(3), nullable=False),
        sa.Column("esfera", sa.String(3), nullable=False),
        sa.Column("aliquota", sa.Numeric(7, 4), nullable=False),
        sa.Column("vigencia_inicio", sa.Date(), nullable=False),
        sa.Column("vigencia_fim", sa.Date(), nullable=False),
        sa.Column("fonte", sa.String(600), nullable=False),
        sa.Column("autorizacao", sa.String(300), nullable=False),
        sa.Column("created_at", ctrl_common.db.UTCDateTime(), nullable=False),
        sa.UniqueConstraint("tributo", "esfera", "vigencia_inicio", name="uq_tax_rate_vigencia"),
    )
    now = datetime.now(timezone.utc)
    op.bulk_insert(t, [{"id": str(uuid.uuid4()), "tributo": tr, "esfera": es, "aliquota": al,
                        "vigencia_inicio": date(2026, 1, 1), "vigencia_fim": date(2026, 12, 31), "fonte": FONTE,
                        "autorizacao": AUTORIZACAO, "created_at": now} for tr, es, al in LINHAS])
    audit = sa.table("audit_events", sa.column("id", sa.String), sa.column("occurred_at", ctrl_common.db.UTCDateTime()),
                     sa.column("actor_type", sa.String), sa.column("actor_id", sa.String), sa.column("company_id", sa.String),
                     sa.column("action", sa.String), sa.column("target_type", sa.String), sa.column("target_id", sa.String),
                     sa.column("details", ctrl_common.db.JsonType()), sa.column("trace_id", sa.String))
    op.bulk_insert(audit, [{"id": str(uuid.uuid4()), "occurred_at": now, "actor_type": "system",
                            "actor_id": "migration 0007", "company_id": None, "action": "tax_parameters.created",
                            "target_type": "tax_rate_parameters", "target_id": "2026",
                            "details": {"vigencia": ["2026-01-01", "2026-12-31"], "ibs_uf": "0.10", "ibs_mun": "0.00",
                                        "cbs": "0.90", "fonte": FONTE, "autorizacao": AUTORIZACAO},
                            "trace_id": None}])


def downgrade() -> None:
    op.drop_table("tax_rate_parameters")
