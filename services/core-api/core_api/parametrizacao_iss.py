"""Consulta oficial (somente leitura) da Parametrização para a alíquota do ISSQN, a pedido da fiscal-api.

O core conhece o CNPJ e o ambiente do workspace e já tem a chave para o conector (nfse-api); a fiscal-api não fala
com o conector fora do worker. Devolve a situação do CONVÊNIO do município de incidência no ambiente (decide se o
pAliq é proibido/obrigatório — Anexo I, E0617/E0619) e a alíquota PARAMETRIZADA para o serviço na competência.

Falha de consulta nunca vira "convênio inativo" nem alíquota zero: o resultado é UNAVAILABLE com o motivo.
Resultados obtidos ficam em cache por 10 minutos, isolados por empresa, CNPJ, ambiente, município, serviço e
competência (nenhum resultado é compartilhado entre contextos); falhas não entram no cache. O cache devolve cópias.
"""
from __future__ import annotations

import copy
import threading
from datetime import datetime, timedelta

import httpx

from ctrl_common import aliquota_iss
from ctrl_common.service_auth import service_headers

CACHE_TTL = timedelta(minutes=10)
_cache: dict[tuple, tuple[datetime, dict]] = {}
_lock = threading.Lock()


def _get(http: httpx.Client, key: str, path: str, params: dict) -> tuple[dict, str | None]:
    try:
        r = http.get(path, headers=service_headers("core-api", key), params=params)
    except httpx.HTTPError:
        return {}, "INTEGRATION_UNAVAILABLE"
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200:
        return body, body.get("code") or f"HTTP_{r.status_code}"
    return body, None


def consultar(http: httpx.Client | None, key: str | None, *, company_id: str, cnpj: str, environment: str,
              municipio: str, servico: str | None, competencia: str | None, now: datetime) -> dict:
    out: dict = {"environment": environment, "municipio": municipio, "consulted_at": now.isoformat(),
                 "convenio": {"status": "UNAVAILABLE", "detail": None},
                 "aliquota": {"status": "NOT_REQUESTED", "rate": None, "servico": servico, "competencia": competencia}}
    if http is None or not key:
        out["convenio"]["detail"] = "INTEGRATION_UNAVAILABLE"
        if servico:
            out["aliquota"].update(status="UNAVAILABLE", message="INTEGRATION_UNAVAILABLE")
        return out
    ck = (company_id, cnpj, environment, municipio, servico, competencia)
    with _lock:
        hit = _cache.get(ck)
        if hit and now - hit[0] < CACHE_TTL:
            return copy.deepcopy(hit[1])
    base = f"/internal/v1/companies/{company_id}/municipal-parameters/{municipio}"
    q = {"environment": environment, "cnpj": cnpj}
    body, err = _get(http, key, base, q)
    ativo = aliquota_iss.convenio_ativo(body.get("http_status"), body.get("body")) if err is None else None
    out["convenio"] = {"status": "UNAVAILABLE" if ativo is None else "ACTIVE" if ativo else "INACTIVE",
                       "detail": err or (None if ativo is not None else f"HTTP_{body.get('http_status')}"),
                       "message": (body.get("body") or {}).get("mensagem") if err is None else None}
    if servico and competencia:
        body, err = _get(http, key, base + "/aliquota", {**q, "servico": servico, "competencia": competencia})
        res = (aliquota_iss.aliquota_parametrizada(body.get("http_status"), body.get("body"), servico, competencia)
               if err is None else {"status": "UNAVAILABLE", "rate": None, "message": err})
        out["aliquota"] = {**res, "servico": servico, "competencia": competencia}
    if out["convenio"]["status"] != "UNAVAILABLE" and out["aliquota"]["status"] != "UNAVAILABLE":
        with _lock:
            for k in [k for k, (t, _) in _cache.items() if now - t >= CACHE_TTL]:
                del _cache[k]  # descarta vencidos
            _cache[ck] = (now, copy.deepcopy(out))
    return out
