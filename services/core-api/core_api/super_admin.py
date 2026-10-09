"""Concede SUPER_ADMIN a uma conta EXISTENTE (bootstrap do primeiro super admin). Uso, dentro da imagem do core-api:
  python -m core_api.super_admin --email <email>

- Não cria usuário nem senha: a conta já deve existir, estar ativa e ter senha definida pelo próprio titular.
- Recusa contas marcadas como de teste/homologação na criação (auditoria; os CLIs que as criavam foram removidos).
- Idempotente; registra auditoria (actor_type=system). Não imprime dados além do resultado.
- SUPER_ADMIN é gestão global de usuários: NÃO cria vínculos com empresas nem acesso a documentos.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from sqlalchemy import func, select

from ctrl_common.config import database_url
from ctrl_common.db import make_engine, make_sessionmaker

HOMOLOG_MARK = "auth.homolog_user_created"  # marcas históricas (CLIs de contas de teste removidos)
from .models import AuditEvent, User
from .routes_admin import PENDING_HASH
TEST_MARK = "auth.test_user_created"


def grant(url: str, email: str) -> str:
    now = datetime.now(timezone.utc)
    sessions = make_sessionmaker(make_engine(url))
    with sessions() as s, s.begin():
        u = s.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
        if u is None:
            return "NOT_FOUND"
        test = s.scalar(select(AuditEvent.id).where(AuditEvent.action.in_((TEST_MARK, HOMOLOG_MARK)),
                                                    AuditEvent.target_id == u.id))
        if test:
            return "TEST_ACCOUNT_REFUSED"
        if not u.is_active or u.password_hash == PENDING_HASH:
            return "NOT_ACTIVE"
        if u.is_super_admin:
            return "ALREADY"
        u.is_super_admin = True
        u.version = (u.version or 1) + 1
        u.updated_at = now
        s.add(AuditEvent(occurred_at=now, actor_type="system", actor_id="core_api.super_admin",
                         action="admin.super_admin_granted", target_type="user", target_id=u.id,
                         details={"bootstrap": True}))
        return "GRANTED"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    a = ap.parse_args(argv)
    result = grant(database_url("CORE"), a.email)
    print(result)
    return 0 if result in ("GRANTED", "ALREADY") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
