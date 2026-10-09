from alembic import context

from ctrl_common.migrations import run_migrations
from nfse_api.models import Base

run_migrations(context, Base.metadata, "NFSE")
