"""revisao_perfis_servico: perfis de serviço com revisão (pendente/aprovado/inativo), origem e versões

- service_profiles: status, origin_key (único por empresa — preparação idempotente), evidence, field_sources,
  suggestions, vigência (valid_from/valid_to), aprovação (approved_by/approved_at) e updated_by;
- perfis existentes passam a PENDING_REVIEW (ativos) ou INACTIVE (inativos): nada é aprovado automaticamente;
- service_profile_versions: conteúdo completo de cada versão (a v1 dos perfis existentes é registrada aqui).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-09 10:00:00
"""
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

import ctrl_common.db

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

CAMPOS = ("name", "national_code", "municipal_code", "nbs_code", "description", "issqn_taxation", "pis_cofins_cst",
          "ibs_cbs", "source", "is_active")


def upgrade() -> None:
    J = ctrl_common.db.JsonType
    op.add_column("service_profiles", sa.Column("status", sa.String(16), nullable=False, server_default="PENDING_REVIEW"))
    op.add_column("service_profiles", sa.Column("origin_key", sa.String(80), nullable=True))
    op.add_column("service_profiles", sa.Column("evidence", J(), nullable=True))
    op.add_column("service_profiles", sa.Column("field_sources", J(), nullable=True))
    op.add_column("service_profiles", sa.Column("suggestions", J(), nullable=True))
    op.add_column("service_profiles", sa.Column("valid_from", sa.Date(), nullable=True))
    op.add_column("service_profiles", sa.Column("valid_to", sa.Date(), nullable=True))
    op.add_column("service_profiles", sa.Column("approved_by", sa.String(36), nullable=True))
    op.add_column("service_profiles", sa.Column("approved_at", ctrl_common.db.UTCDateTime(), nullable=True))
    op.add_column("service_profiles", sa.Column("updated_by", sa.String(36), nullable=True))
    op.create_index("uq_service_profile_origin", "service_profiles", ["company_id", "origin_key"], unique=True)
    op.create_table(
        "service_profile_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("profile_id", sa.String(36), sa.ForeignKey("service_profiles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("company_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("snapshot", J(), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=True),
        sa.Column("created_at", ctrl_common.db.UTCDateTime(), nullable=False),
        sa.UniqueConstraint("profile_id", "version", name="uq_service_profile_version"),
    )
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    bind.execute(sa.text("UPDATE service_profiles SET status = CASE WHEN is_active = :t THEN 'PENDING_REVIEW' "
                         "ELSE 'INACTIVE' END"), {"t": True})
    sp = sa.table("service_profiles", *(sa.column(c) for c in ("id", "company_id", "version", "status") + CAMPOS))
    vers = sa.table("service_profile_versions", sa.column("id", sa.String), sa.column("profile_id", sa.String),
                    sa.column("company_id", sa.String), sa.column("version", sa.Integer), sa.column("action", sa.String),
                    sa.column("snapshot", J()), sa.column("actor_type", sa.String), sa.column("actor_id", sa.String),
                    sa.column("created_at", ctrl_common.db.UTCDateTime()))
    for row in bind.execute(sa.select(sp)).mappings().all():
        snap = {k: row[k] for k in CAMPOS + ("status",)}
        bind.execute(vers.insert().values(id=str(uuid.uuid4()), profile_id=row["id"], company_id=row["company_id"],
                                          version=row["version"], action="migrated", snapshot=snap,
                                          actor_type="system", actor_id="migration core 0006", created_at=now))


def downgrade() -> None:
    op.drop_table("service_profile_versions")
    op.drop_index("uq_service_profile_origin", table_name="service_profiles")
    for c in ("updated_by", "approved_at", "approved_by", "valid_to", "valid_from", "suggestions", "field_sources",
              "evidence", "origin_key", "status"):
        op.drop_column("service_profiles", c)
