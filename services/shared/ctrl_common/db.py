"""Tipos e utilitários SQLAlchemy comuns (UTC, UUID em texto, JSON, blobs)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, LargeBinary, create_engine, event
from sqlalchemy.dialects.mysql import DATETIME as MYSQL_DATETIME
from sqlalchemy.dialects.mysql import LONGBLOB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Grava instantes como DATETIME(6) em UTC e devolve datetime com fuso UTC."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: ANN001, ANN201
        if dialect.name == "mysql":
            return dialect.type_descriptor(MYSQL_DATETIME(fsp=6))
        return dialect.type_descriptor(DateTime())

    def process_bind_param(self, value: datetime | None, dialect):  # noqa: ANN001, ANN201
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime sem fuso não pode ser persistido")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):  # noqa: ANN001, ANN201
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


# Blob grande: LONGBLOB no MySQL.
Blob = LargeBinary().with_variant(LONGBLOB(), "mysql")
JsonType = JSON


def new_id() -> str:
    return str(uuid.uuid4())


def make_engine(url: str, **kwargs: Any) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}, **kwargs)

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return engine
    import os

    # Pool por processo (padrões do SQLAlchemy); ajustável na implantação para caber no max_connections do MySQL.
    pool = {"pool_size": int(os.environ.get("DB_POOL_SIZE", "5")),
            "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", "10")),
            "pool_timeout": int(os.environ.get("DB_POOL_TIMEOUT", "30"))}
    pool.update({k: v for k, v in kwargs.items() if k in pool})
    kwargs = {k: v for k, v in kwargs.items() if k not in pool}
    return create_engine(
        url,
        **pool,
        pool_pre_ping=True,
        pool_recycle=1800,
        isolation_level="READ COMMITTED",
        json_serializer=lambda o: json.dumps(o, ensure_ascii=False),
        **kwargs,
    )


def make_sessionmaker(engine: Engine) -> sessionmaker:
    return sessionmaker(engine, expire_on_commit=False)


def is_mysql(engine: Engine) -> bool:
    return engine.dialect.name == "mysql"
