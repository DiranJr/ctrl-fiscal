"""DANFSe gerado PELA PLATAFORMA a partir do XML oficial armazenado (NT SE/CGNFS-e nº 008 v1.02, 14/07/2026).

Não é um arquivo baixado do governo: a API nacional de DANFSe foi suspensa em 03/08/2026 (NT 008 v1.02, item 1) e a
geração passou a ser responsabilidade dos sistemas emissores. Rascunhos nunca geram DANFSe.
"""
from __future__ import annotations

from .campos import Danfse, XmlNaoNfse, extrair
from .fontes import FontSet, resolve as resolve_fontes
from .layout import render

NT_VERSAO = "NT SE/CGNFS-e 008 v1.02 (14/07/2026)"
MARCAS = ("CANCELADA", "SUBSTITUÍDA")


def gerar(xml: bytes, marca: str | None = None, fontes: FontSet | None = None) -> bytes:
    if marca is not None and marca not in MARCAS:
        raise ValueError(f"marca d'água inválida: {marca!r}")
    d = extrair(xml)
    return render(d, marca, title=f"DANFSe {d.chave}", subject=f"NFS-e {d.numero} - {NT_VERSAO}", fontes=fontes)


__all__ = ["Danfse", "FontSet", "MARCAS", "NT_VERSAO", "XmlNaoNfse", "extrair", "gerar", "resolve_fontes"]
