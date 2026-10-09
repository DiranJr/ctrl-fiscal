"""Domínios OFICIAIS do Sistema Nacional NFS-e (Anexos A, B, C e I), gerados por scripts/gerar_dominios_oficiais.py.

Um código só é aceito se EXISTIR na tabela oficial correspondente — tamanho correto não basta.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "dados.json"
AGUAS_MARITIMAS = "0000000"
# E0370/E0372 (Anexo I): subitens em que o grupo "obra" é OBRIGATÓRIO; fora deles é PROIBIDO (exceto 99.01.01).
OBRA_EXCECAO = "990101"


@lru_cache(maxsize=1)
def dados() -> dict:
    return json.loads(_DATA.read_text(encoding="utf-8"))


def municipio(code: str | None) -> dict | None:
    return dados()["municipios"].get(code or "")


def municipio_ou_local_especial(code: str | None) -> bool:
    return municipio(code) is not None or code == AGUAS_MARITIMAS


def servico_nacional(code: str | None) -> str | None:
    return dados()["servicos_nacionais"].get(code or "")


def nbs(code: str | None) -> str | None:
    return dados()["nbs"].get(code or "")


def cindop_valido(code: str | None) -> bool:
    return (code or "") in set(dados()["cindop"])


def cindop_info(code: str | None) -> dict | None:
    """Anexo C: tipo de operação, local da operação (art. 11 da LC 214/2025) e local a identificar no DF-e."""
    return dados().get("cindop_info", {}).get(code or "")


def incidencia(c_trib_nac: str | None) -> str | None:
    """EP (estabelecimento do prestador), LP (local da prestação) ou ET (estabelecimento do tomador)."""
    return dados()["incidencia"].get((c_trib_nac or "").zfill(6)) if c_trib_nac else None


def grupo_obrigatorio(c_trib_nac: str | None) -> str | None:
    """"obra" ou "atvEvento" quando o Anexo I exige o grupo para o subitem; None caso contrário."""
    return dados()["grupo_obrigatorio"].get((c_trib_nac or "").zfill(6)) if c_trib_nac else None


def fontes() -> dict:
    return dados()["fontes_sha256"]
