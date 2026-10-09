from alembic import context

from ctrl_common.migrations import run_migrations
from core_api.models import Base

run_migrations(context, Base.metadata, "CORE")
