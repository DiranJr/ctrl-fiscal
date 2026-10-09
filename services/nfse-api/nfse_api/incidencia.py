"""Localidade de incidência do ISSQN por código de tributação nacional (Anexo I oficial, aba MUN.INCID_INFO.SERV.,
extraída por scripts/extrair_incidencia_servicos.py). EP = estabelecimento do prestador (regra geral),
LP = local da prestação, ET = estabelecimento do tomador."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data" / "incidencia_servicos.json"


@lru_cache(maxsize=1)
def _table() -> dict[str, str]:
    return json.loads(_DATA.read_text(encoding="utf-8"))["incidencia"]


def regra(c_trib_nac: str | None) -> str | None:
    """Fonte única: pacote de domínios oficiais compartilhado (ctrl_common.dominios, mesmo Anexo I)."""
    from ctrl_common import dominios

    return dominios.incidencia(c_trib_nac)


def municipio_incidencia(snapshot: dict) -> str | None:
    """Município de incidência para os serviços prestados no país (sem importação/exportação)."""
    service = snapshot.get("service") or {}
    r = regra(service.get("national_code"))
    if r == "LP":
        return service.get("location_code")
    if r == "ET":
        return (snapshot.get("customer") or {}).get("municipality_code")
    if r == "EP":
        return (snapshot.get("company") or {}).get("municipality_code")
    return None
