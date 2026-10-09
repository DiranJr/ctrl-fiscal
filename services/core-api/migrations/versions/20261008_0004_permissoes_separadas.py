"""permissoes_separadas: sync, emit, cancel e certificates separadas das permissões antigas

Mapeamento (preserva o que cada vínculo já podia fazer, sem ampliar):
- "import"   também sincronizava com o ADN      -> ganha "sync"
- "schedule" também pedia a transmissão          -> ganha "emit"
- "admin"    também gerenciava o certificado     -> ganha "certificates"
- "cancel"   é operação NOVA: não é concedida a ninguém; o SUPER_ADMIN concede explicitamente.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08 15:00:00
"""
import json
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

ORDER = ["view", "import", "sync", "review", "prepare", "schedule", "emit", "cancel", "certificates", "admin"]
GAINS = {"import": "sync", "schedule": "emit", "admin": "certificates"}


def _perms(raw) -> list[str]:  # noqa: ANN001
    if raw is None:
        return []
    return list(json.loads(raw) if isinstance(raw, (str, bytes)) else raw)


def upgrade() -> None:
    bind = op.get_bind()
    m = sa.table("memberships", sa.column("id", sa.String), sa.column("permissions", sa.JSON),
                 sa.column("updated_at", sa.DateTime))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for row in bind.execute(sa.select(m.c.id, m.c.permissions)).all():
        old = _perms(row.permissions)
        new = set(old) | {GAINS[p] for p in old if p in GAINS}
        if new != set(old):
            ordered = [p for p in ORDER if p in new] + sorted(p for p in new if p not in ORDER)
            bind.execute(sa.update(m).where(m.c.id == row.id).values(permissions=ordered, updated_at=now))


def downgrade() -> None:
    bind = op.get_bind()
    m = sa.table("memberships", sa.column("id", sa.String), sa.column("permissions", sa.JSON))
    for row in bind.execute(sa.select(m.c.id, m.c.permissions)).all():
        old = _perms(row.permissions)
        kept = [p for p in old if p not in ("sync", "emit", "cancel", "certificates")]
        if kept != old:
            bind.execute(sa.update(m).where(m.c.id == row.id).values(permissions=kept))
