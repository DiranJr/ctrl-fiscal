"""pedidos_cancelamento: pedidos de evento do emitente (cancelamento 101101 / análise fiscal 101103)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-08 15:30:00
"""
from alembic import op
import sqlalchemy as sa

import ctrl_common.db

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cancellation_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("active_document_id", sa.String(36), nullable=True),
        sa.Column("access_key", sa.String(50), nullable=False),
        sa.Column("company_cnpj", sa.String(14), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("event_code", sa.String(6), nullable=False),
        sa.Column("reason_code", sa.String(2), nullable=False),
        sa.Column("reason_text", sa.String(255), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("submit_phase", sa.String(12), nullable=False, server_default="NONE"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", ctrl_common.db.UTCDateTime(), nullable=False),
        sa.Column("lease_owner", sa.String(80), nullable=True),
        sa.Column("lease_expires_at", ctrl_common.db.UTCDateTime(), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("result", ctrl_common.db.JsonType(), nullable=True),
        sa.Column("errors", ctrl_common.db.JsonType(), nullable=True),
        sa.Column("last_error_code", sa.String(60), nullable=True),
        sa.Column("last_error_message", sa.String(500), nullable=True),
        sa.Column("event_row_id", sa.String(36), nullable=True),
        sa.Column("requested_by", sa.String(36), nullable=False),
        sa.Column("requested_at", ctrl_common.db.UTCDateTime(), nullable=False),
        sa.Column("finished_at", ctrl_common.db.UTCDateTime(), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("created_at", ctrl_common.db.UTCDateTime(), nullable=False),
        sa.Column("updated_at", ctrl_common.db.UTCDateTime(), nullable=False),
        sa.UniqueConstraint("company_id", "active_document_id", name="uq_cancellation_active"),
    )
    op.create_index("ix_cancellation_due", "cancellation_requests", ["status", "next_attempt_at"])
    op.create_index("ix_cancellation_document", "cancellation_requests", ["document_id"])


def downgrade() -> None:
    op.drop_table("cancellation_requests")
