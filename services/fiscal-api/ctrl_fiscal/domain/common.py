from __future__ import annotations

import gzip
import hashlib
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from ctrl_common.hashing import canonical_hash  # noqa: F401 (reexportado)

from ..models import AuditEvent, StoredBlob


class DomainError(Exception):
    """Erro de regra de negócio, convertido em problem+json pela API."""

    def __init__(self, status: int, code: str, detail: str, errors: list[dict] | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.errors = errors


def audit(
    s: Session,
    now: datetime,
    action: str,
    *,
    company_id: str | None,
    actor_type: str = "user",
    actor_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict | None = None,
    trace_id: str | None = None,
) -> None:
    s.add(
        AuditEvent(
            occurred_at=now, actor_type=actor_type, actor_id=actor_id, company_id=company_id, action=action,
            target_type=target_type, target_id=target_id, details=details, trace_id=trace_id,
        )
    )


def store_blob(s: Session, company_id: str, raw: bytes, kind: str, now: datetime) -> StoredBlob:
    b = StoredBlob(
        company_id=company_id,
        sha256=hashlib.sha256(raw).hexdigest(),
        size=len(raw),
        content_gz=gzip.compress(raw, compresslevel=6),
        kind=kind,
        created_at=now,
    )
    s.add(b)
    s.flush()
    return b


def read_blob(b: StoredBlob) -> bytes:
    return gzip.decompress(b.content_gz)


def money(v: Decimal | None) -> str | None:
    return None if v is None else f"{v.quantize(Decimal('0.01'))}"
