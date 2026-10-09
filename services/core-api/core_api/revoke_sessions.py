"""Encerra todas as sessões (famílias de refresh) ativas de uma conta. Uso:

  python -m core_api.revoke_sessions --email usuario@exemplo.com [--reason ADMIN_REVOKED]

Não altera senha, associações nem dados. Access tokens já emitidos deixam de valer na próxima autorização
(a fiscal-api confere a sessão no core a cada requisição). Registra auditoria com a contagem.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from sqlalchemy import select, update

from ctrl_common.config import database_url
from ctrl_common.db import make_engine, make_sessionmaker

from .models import AuditEvent, RefreshSession, User


def revoke(url: str, email: str, reason: str) -> int:
    now = datetime.now(timezone.utc)
    with make_sessionmaker(make_engine(url))() as s, s.begin():
        user = s.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            raise SystemExit("Conta inexistente")
        n = s.execute(update(RefreshSession).where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
                      .values(revoked_at=now, revoked_reason=reason[:40])).rowcount
        s.add(AuditEvent(occurred_at=now, actor_type="system", actor_id="core_api.revoke_sessions",
                         action="auth.sessions_revoked", target_type="user", target_id=user.id,
                         details={"count": n, "reason": reason[:40]}))
        return n


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--reason", default="ADMIN_REVOKED")
    a = ap.parse_args(argv)
    sys.stdout.write(f"sessões encerradas: {revoke(database_url('CORE'), a.email, a.reason)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
