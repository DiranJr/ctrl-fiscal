"""Execução Alembic comum. Migrações usam credencial própria (DDL), distinta
da credencial de runtime dos serviços (somente DML)."""
from __future__ import annotations

from urllib.parse import quote

from sqlalchemy import MetaData, create_engine, pool

from .config import ConfigError, env, secret


def migration_url(prefix: str) -> str:
    direct = secret(prefix + "_MIGRATE_DATABASE_URL", required=False)
    if direct:
        return direct
    host = env(prefix + "_DB_HOST", "mysql")
    port = env(prefix + "_DB_PORT", "3306")
    name = env(prefix + "_DB_NAME")
    user = env(prefix + "_MIGRATE_DB_USER")
    password = secret(prefix + "_MIGRATE_DB_PASSWORD")
    if not name or not user:
        raise ConfigError(f"{prefix}_DB_NAME/{prefix}_MIGRATE_DB_USER não configurados")
    return f"mysql+pymysql://{quote(user)}:{quote(password or '')}@{host}:{port}/{name}?charset=utf8mb4"


def run_migrations(context, metadata: MetaData, prefix: str) -> None:  # noqa: ANN001
    url = context.config.get_main_option("sqlalchemy.url") or migration_url(prefix)
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
