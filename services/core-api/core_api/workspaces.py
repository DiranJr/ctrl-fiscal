"""Garantia do invariante 1 empresa : 1 workspace (mesmo identificador) em todo ponto que cria empresa."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Company, Workspace


def ensure_workspace(s: Session, company: Company, now: datetime, status: str = "PREPARING",
                     created_by: str | None = None) -> Workspace:
    w = s.scalar(select(Workspace).where(Workspace.company_id == company.id))
    if w is None:
        s.flush()
        w = Workspace(id=company.id, company_id=company.id, name=company.name or company.cnpj, status=status,
                      created_by=created_by, created_at=now, updated_at=now)
        s.add(w)
        s.flush()
    return w
