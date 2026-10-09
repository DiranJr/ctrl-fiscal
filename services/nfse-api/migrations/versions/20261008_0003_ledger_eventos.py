"""ledger_eventos: pedidos de registro de evento do emitente (cancelamento/análise fiscal) e autoridade simulada

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08 15:30:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

import ctrl_common.db

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

BLOB = sa.LargeBinary().with_variant(mysql.LONGBLOB(), "mysql")


def upgrade() -> None:
    op.create_table(
        "event_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("company_cnpj", sa.String(14), nullable=False),
        sa.Column("access_key", sa.String(50), nullable=False),
        sa.Column("event_code", sa.String(6), nullable=False),
        sa.Column("pre_id", sa.String(59), nullable=False),
        sa.Column("active_pre_id", sa.String(59), nullable=True),
        sa.Column("c_motivo", sa.String(2), nullable=False),
        sa.Column("x_motivo", sa.String(255), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("transmissions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sending_until", ctrl_common.db.UTCDateTime(), nullable=True),
        sa.Column("request_xml_gz", BLOB, nullable=True),
        sa.Column("event_xml_gz", BLOB, nullable=True),
        sa.Column("result", ctrl_common.db.JsonType(), nullable=True),
        sa.Column("errors", ctrl_common.db.JsonType(), nullable=True),
        sa.Column("last_error_code", sa.String(60), nullable=True),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_reconciled_at", ctrl_common.db.UTCDateTime(), nullable=True),
        sa.Column("created_at", ctrl_common.db.UTCDateTime(), nullable=False),
        sa.Column("updated_at", ctrl_common.db.UTCDateTime(), nullable=False),
        sa.UniqueConstraint("environment", "active_pre_id", name="uq_event_env_active_pre"),
    )
    op.create_index("ix_event_env_pre", "event_requests", ["environment", "pre_id"])
    op.create_table(
        "mock_event_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("access_key", sa.String(50), nullable=False),
        sa.Column("event_code", sa.String(6), nullable=False),
        sa.Column("pre_id", sa.String(59), nullable=False),
        sa.Column("c_motivo", sa.String(2), nullable=False),
        sa.Column("x_motivo", sa.String(255), nullable=False),
        sa.Column("registered_at", ctrl_common.db.UTCDateTime(), nullable=False),
        sa.UniqueConstraint("access_key", "event_code", name="uq_mock_event"),
    )


def downgrade() -> None:
    op.drop_table("mock_event_records")
    op.drop_table("event_requests")
