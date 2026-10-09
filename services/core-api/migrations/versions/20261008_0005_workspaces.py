"""workspaces: um workspace por empresa (mesmo identificador), série da DPS por workspace e perfis de serviço

- workspaces: id = companies.id (1:1; company_id único) — preserva TODOS os identificadores existentes; cada empresa
  existente ganha o seu workspace (no operacional, só a empresa real), status ACTIVE se a empresa estiver ativa.
- companies.dps_series: a série deixa de ser configuração global do worker (DPS_SERIES, efetiva "1" no compose).
  Valor migrado = "1" para as empresas existentes (mesmo comportamento anterior; numeração não é reiniciada).
- companies: endereço do prestador (cadastro do workspace; preenchimento explícito).
- service_profiles: perfis tributários de serviço por workspace (vazio na migração: nada é presumido).
Nenhuma permissão é ampliada (memberships não são alteradas).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-08 18:00:00
"""
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

import ctrl_common.db

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("dps_series", sa.String(5), nullable=True))
    op.add_column("companies", sa.Column("address", ctrl_common.db.JsonType(), nullable=True))
    op.add_column("companies", sa.Column("postal_code", sa.String(8), nullable=True))
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="PREPARING"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", ctrl_common.db.UTCDateTime(), nullable=False),
        sa.Column("updated_at", ctrl_common.db.UTCDateTime(), nullable=False),
        sa.UniqueConstraint("company_id", name="uq_workspace_company"),
    )
    op.create_table(
        "service_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("national_code", sa.String(6), nullable=False),
        sa.Column("municipal_code", sa.String(20), nullable=True),
        sa.Column("nbs_code", sa.String(9), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("issqn_taxation", sa.String(16), nullable=True),
        sa.Column("pis_cofins_cst", sa.String(2), nullable=True),
        sa.Column("ibs_cbs", ctrl_common.db.JsonType(), nullable=True),
        sa.Column("source", sa.String(300), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", ctrl_common.db.UTCDateTime(), nullable=False),
        sa.Column("updated_at", ctrl_common.db.UTCDateTime(), nullable=False),
    )
    op.create_index("ix_service_profiles_company", "service_profiles", ["company_id", "is_active"])
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    ws = sa.table("workspaces", sa.column("id", sa.String), sa.column("company_id", sa.String), sa.column("name", sa.String),
                  sa.column("status", sa.String), sa.column("created_at", ctrl_common.db.UTCDateTime()),
                  sa.column("updated_at", ctrl_common.db.UTCDateTime()))
    audit = sa.table("audit_events", sa.column("id", sa.String), sa.column("occurred_at", ctrl_common.db.UTCDateTime()),
                     sa.column("actor_type", sa.String), sa.column("actor_id", sa.String), sa.column("company_id", sa.String),
                     sa.column("action", sa.String), sa.column("target_type", sa.String), sa.column("target_id", sa.String),
                     sa.column("details", ctrl_common.db.JsonType()), sa.column("trace_id", sa.String))
    for cid, name, active in bind.execute(sa.text("SELECT id, name, is_active FROM companies")).all():
        status = "ACTIVE" if active else "SUSPENDED"
        bind.execute(ws.insert().values(id=cid, company_id=cid, name=name, status=status, created_at=now, updated_at=now))
        bind.execute(sa.text("UPDATE companies SET dps_series = '1' WHERE id = :i AND dps_series IS NULL"), {"i": cid})
        bind.execute(audit.insert().values(
            id=str(uuid.uuid4()), occurred_at=now, actor_type="system", actor_id="migration core 0005", company_id=cid,
            action="workspace.migrated", target_type="workspace", target_id=cid,
            details={"status": status, "dps_series": "1", "origem_serie": "configuração global DPS_SERIES efetiva (1)"},
            trace_id=None))


def downgrade() -> None:
    op.drop_index("ix_service_profiles_company", table_name="service_profiles")
    op.drop_table("service_profiles")
    op.drop_table("workspaces")
    op.drop_column("companies", "postal_code")
    op.drop_column("companies", "address")
    op.drop_column("companies", "dps_series")
