"""Validação de JWT de acesso (EdDSA/Ed25519) nos serviços.

A chave privada existe somente no core-api; os demais serviços recebem a pública.
Assinatura, emissor e audiência são validados pelo PyJWT; exp/nbf/iat são
conferidos contra o relógio injetável do serviço (testável com relógio falso).
"""
from __future__ import annotations

from dataclasses import dataclass

import jwt

from .clock import Clock, SystemClock

ISSUER = "ctrl-core"
AUDIENCE = "ctrl-api"
ALGORITHM = "EdDSA"


class TokenError(Exception):
    pass


@dataclass(frozen=True)
class AccessClaims:
    user_id: str
    session_id: str
    token_id: str


class AccessTokenVerifier:
    def __init__(self, public_key_pem: str, leeway_seconds: int = 10, clock: Clock | None = None) -> None:
        self._key = public_key_pem
        self._leeway = leeway_seconds
        self._clock = clock or SystemClock()

    def verify(self, token: str) -> AccessClaims:
        try:
            claims = jwt.decode(
                token,
                self._key,
                algorithms=[ALGORITHM],
                audience=AUDIENCE,
                issuer=ISSUER,
                options={
                    "require": ["exp", "iat", "iss", "aud", "sub", "sid", "jti"],
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                },
            )
        except jwt.PyJWTError as exc:
            raise TokenError("token inválido") from exc
        now = self._clock.now().timestamp()
        try:
            exp, iat = float(claims["exp"]), float(claims["iat"])
            nbf = float(claims.get("nbf", iat))
        except (TypeError, ValueError) as exc:
            raise TokenError("claims de tempo inválidas") from exc
        if now >= exp + self._leeway or now + self._leeway < nbf or now + self._leeway < iat:
            raise TokenError("token expirado ou ainda não válido")
        if claims.get("typ") != "access":
            raise TokenError("tipo de token inválido")
        return AccessClaims(user_id=str(claims["sub"]), session_id=str(claims["sid"]), token_id=str(claims["jti"]))
