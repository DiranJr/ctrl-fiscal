"""Senhas (Argon2id), JWT de acesso (EdDSA), refresh opaco e CSRF."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from ctrl_common.tokens import ALGORITHM, AUDIENCE, ISSUER

_hasher = PasswordHasher()  # Argon2id com parâmetros padrão da biblioteca
# Hash fixo para igualar tempo de resposta quando o e-mail não existe.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    # E-mail inexistente ou conta sem senha (PENDING, hash "!pending"): verifica contra o hash fictício para que o
    # tempo de resposta não revele o estado da conta; o resultado é sempre False.
    usable = bool(password_hash) and password_hash.startswith("$argon2")
    try:
        return _hasher.verify(password_hash if usable else _DUMMY_HASH, password) and usable
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_for_session(secret: str, session_id: str) -> str:
    return hmac.new(secret.encode(), ("csrf:" + session_id).encode(), hashlib.sha256).hexdigest()


def csrf_matches(secret: str, session_id: str, presented: str | None) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(csrf_for_session(secret, session_id), presented)


class AccessTokenIssuer:
    def __init__(self, private_key_pem: str, kid: str, ttl_seconds: int) -> None:
        self._key = private_key_pem
        self._kid = kid
        self.ttl_seconds = ttl_seconds

    def issue(self, user_id: str, session_id: str, now: datetime) -> str:
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": user_id,
            "sid": session_id,
            "jti": uuid.uuid4().hex,
            "typ": "access",
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=self.ttl_seconds)).timestamp()),
        }
        # Permissões não vão no token: cada serviço confere a autorização atual.
        return jwt.encode(claims, self._key, algorithm=ALGORITHM, headers={"kid": self._kid})
