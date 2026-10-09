"""Leases com expiração, dono e fencing token (compare-and-set).

A aquisição usa SELECT ... FOR UPDATE SKIP LOCKED no MySQL para reduzir
contenção e um UPDATE condicional (token + expiração) que é a garantia real
de exclusividade, válida também em bancos sem SKIP LOCKED.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import or_, update
from sqlalchemy.orm import Session


def lease_free(model, now: datetime):  # noqa: ANN001, ANN201
    return or_(model.lease_expires_at.is_(None), model.lease_expires_at <= now)


def try_acquire(
    s: Session, model, row_id: str, old_token: int, owner: str, now: datetime, lease_seconds: int, **values  # noqa: ANN001, ANN003
) -> bool:
    res = s.execute(
        update(model)
        .where(model.id == row_id, model.fencing_token == old_token, lease_free(model, now))
        .values(
            lease_owner=owner,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            fencing_token=old_token + 1,
            **values,
        )
        .execution_options(synchronize_session=False)
    )
    return res.rowcount == 1


def fenced_update(s: Session, model, row_id: str, token: int, owner: str, **values) -> bool:  # noqa: ANN001, ANN003
    """Atualiza somente se o lease ainda pertence a este dono/token."""
    res = s.execute(
        update(model)
        .where(model.id == row_id, model.fencing_token == token, model.lease_owner == owner)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return res.rowcount == 1
