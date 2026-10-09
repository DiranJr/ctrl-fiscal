"""Identidade entre serviços: cada chamador tem chave própria e escopos permitidos.

Cabeçalhos: X-Service-Name: <nome> e Authorization: Bearer <chave>.
A rede interna do Docker não é tratada como prova de identidade.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Request

from .problems import ProblemError


@dataclass(frozen=True)
class ServiceCaller:
    name: str
    scopes: frozenset[str]


class ServiceAuthenticator:
    def __init__(self, clients: dict[str, tuple[str, set[str]]]) -> None:
        # nome -> (chave, escopos)
        for name, (key, _) in clients.items():
            if not key or len(key) < 32:
                raise ValueError(f"chave interna de {name} ausente ou curta")
        self._clients = {n: (k.encode(), frozenset(s)) for n, (k, s) in clients.items()}

    def authenticate(self, request: Request, scope: str) -> ServiceCaller:
        name = request.headers.get("x-service-name", "")
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer ") or name not in self._clients:
            raise ProblemError(401, "SERVICE_UNAUTHENTICATED", "Identidade de serviço ausente ou inválida.")
        presented = auth[7:].strip().encode()
        key, scopes = self._clients[name]
        if not hmac.compare_digest(presented, key):
            raise ProblemError(401, "SERVICE_UNAUTHENTICATED", "Identidade de serviço ausente ou inválida.")
        if scope not in scopes:
            raise ProblemError(403, "SERVICE_FORBIDDEN", "Serviço sem permissão para esta operação.")
        return ServiceCaller(name=name, scopes=scopes)


def service_headers(name: str, key: str) -> dict[str, str]:
    return {"X-Service-Name": name, "Authorization": f"Bearer {key}"}
