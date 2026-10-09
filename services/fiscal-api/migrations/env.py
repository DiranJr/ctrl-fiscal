from alembic import context

from ctrl_common.migrations import run_migrations
from ctrl_fiscal.models import Base

run_migrations(context, Base.metadata, "FISCAL")
