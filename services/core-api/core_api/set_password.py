"""Troca LOCAL de senha de usuário existente, a partir de arquivo de segredo.

Uso (dentro da imagem do core-api, com o arquivo montado como segredo):
  python -m core_api.set_password --email <email> --password-file /run/secrets/<arquivo>

- A senha nunca é aceita por argumento, nunca é impressa e só o hash Argon2id é gravado.
- Mantém usuário, empresas, associações e dados; revoga todas as sessões de refresh
  do usuário (tokens de acesso emitidos deixam de ser aceitos na próxima checagem).
- Registra auditoria sem o valor da senha.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update

from ctrl_common.config import database_url
from ctrl_common.db import make_engine, make_sessionmaker

from .models import AuditEvent, RefreshSession, User
from .security import hash_password

MIN_LENGTH = 16


def rotate(url: str, email: str, new_password: str) -> dict:
    if len(new_password) < MIN_LENGTH:
        raise SystemExit(f"A nova senha deve ter ao menos {MIN_LENGTH} caracteres")
    now = datetime.now(timezone.utc)
    sessions = make_sessionmaker(make_engine(url))
    with sessions() as s, s.begin():
        user = s.scalar(select(User).where(User.email == email.strip().lower()).with_for_update())
        if user is None:
            raise SystemExit("Usuário não encontrado")
        user.password_hash = hash_password(new_password)
        user.updated_at = now
        revoked = s.execute(
            update(RefreshSession)
            .where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason="PASSWORD_ROTATED")
        ).rowcount
        s.add(AuditEvent(occurred_at=now, actor_type="system", actor_id="core_api.set_password", action="auth.password_rotated",
                         target_type="user", target_id=user.id, details={"sessions_revoked": revoked}))
        return {"user_id": user.id, "sessions_revoked": revoked}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--password-file", required=True)
    a = ap.parse_args(argv)
    secret = Path(a.password_file).read_text(encoding="utf-8").strip()
    result = rotate(database_url("CORE"), a.email, secret)
    sys.stdout.write(f"Senha trocada (valor não exibido). Sessões revogadas: {result['sessions_revoked']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
