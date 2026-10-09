"""Pedido de Registro de Evento (pedRegEvento v1.01) do EMITENTE: cancelamento (101101) e solicitação de análise
fiscal para cancelamento (101103).

Fontes (consultadas em 08/10/2026; arquivos oficiais e SHA-256 em docs/oficial/eventos-20261008/):
- XSD v1.01 (pacotes de Produção 20260209 e Produção Restrita 20260727, idênticos para eventos):
  pedRegEvento_v1.01.xsd, tiposEventos_v1.01.xsd (TCInfPedReg, TE101101, TE101103), tiposSimples_v1.01.xsd.
- Anexo II v1.01-20260122, aba LEIAUTE l.14 e regra E1827: Id = "PRE" + chave de acesso (50) + código do evento (6).
- O cancelamento por substituição (105102) NÃO é enviado pelo contribuinte: a Sefin o gera ao autorizar uma DPS
  substituta (infDPS/subst). Os eventos 105104/105105 (deferido/indeferido) são do município.
Nada aqui é presumido de implementação antiga: códigos, textos fixos e limites vêm do XSD/Anexo II citados.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from lxml import etree

NS = "http://www.sped.fazenda.gov.br/nfse"
VERSAO = "1.01"
VER_APLIC = "CTRLFiscal-0.6"  # TSVerAplic: 1 a 20 caracteres
XSD_DIR = Path(__file__).resolve().parent / "xsd"
XSD_BY_ENV = {
    "restricted": XSD_DIR / "prodrest-v1.01-20260727" / "pedRegEvento_v1.01.xsd",
    "production": XSD_DIR / "producao-v1.01-20260209" / "pedRegEvento_v1.01.xsd",
}

# código → (elemento do grupo, xDesc fixo do XSD, cMotivo aceitos pelo XSD)
EVENTOS_EMITENTE = {
    "101101": ("e101101", "Cancelamento de NFS-e", {"1", "2", "9"}),  # TSCodJustCanc
    "101103": ("e101103", "Solicitação de Análise Fiscal para Cancelamento de NFS-e", {"1", "2", "9"}),  # enum = 9
}
MOTIVOS = {"1": "Erro na Emissão", "2": "Serviço não Prestado", "9": "Outros"}
# TSString (tiposSimples l.13-21) + TSMotivo (l.348-356): 15 a 255, sem espaço nas pontas, sem quebra de linha,
# somente caracteres de U+0020 a U+00FF.
_TSSTRING = re.compile(r"^(?:[!-ÿ][ -ÿ]*[!-ÿ]|[!-ÿ])$")
BRT = timezone(timedelta(hours=-3))


class EventoInvalido(Exception):
    def __init__(self, erros: list[str]) -> None:
        super().__init__("; ".join(erros))
        self.erros = erros


@dataclass
class PedidoEvento:
    pre_id: str
    xml: bytes  # sem assinatura
    dh_evento: str


def validar_motivo(texto: str) -> list[str]:
    erros = []
    if not 15 <= len(texto) <= 255:
        erros.append("A justificativa deve ter de 15 a 255 caracteres.")
    if not _TSSTRING.match(texto or ""):
        erros.append("A justificativa não pode começar/terminar com espaço, ter quebra de linha nem caracteres fora "
                     "do conjunto aceito pelo leiaute (Latin-1).")
    return erros


def dh_evento(agora: datetime) -> str:
    """TSDateTimeUTC: AAAA-MM-DDThh:mm:ss-03:00, sem fração e sem 'Z'. Recuado 30 s: E1843 recusa dhEvento
    posterior ao recebimento e não há tolerância de relógio documentada (recomendação, não regra oficial)."""
    return (agora.astimezone(BRT) - timedelta(seconds=30)).replace(microsecond=0).isoformat()


def montar(*, environment: str, access_key: str, cnpj_autor: str, codigo: str, c_motivo: str, x_motivo: str,
           agora: datetime) -> PedidoEvento:
    if environment not in XSD_BY_ENV:
        raise EventoInvalido([f"Ambiente sem leiaute oficial de eventos: {environment}."])
    if codigo not in EVENTOS_EMITENTE:
        raise EventoInvalido([f"Evento {codigo} não é enviado pelo emitente."])
    grupo, x_desc, motivos = EVENTOS_EMITENTE[codigo]
    erros = []
    if c_motivo not in motivos:
        erros.append("Código de motivo inválido para o evento.")
    erros += validar_motivo(x_motivo)
    if not re.fullmatch(r"[0-9]{50}|[0-9]{6}[0-9A-Z]{14}[0-9]{30}", access_key or ""):
        erros.append("Chave de acesso inválida.")
    if erros:
        raise EventoInvalido(erros)
    pre_id = "PRE" + access_key + codigo
    dh = dh_evento(agora)
    root = etree.Element(f"{{{NS}}}pedRegEvento", nsmap={None: NS}, versao=VERSAO)
    inf = etree.SubElement(root, f"{{{NS}}}infPedReg", Id=pre_id)

    def sub(parent: etree._Element, tag: str, text: str | None = None) -> etree._Element:
        el = etree.SubElement(parent, f"{{{NS}}}{tag}")
        if text is not None:
            el.text = text
        return el

    sub(inf, "tpAmb", "1" if environment == "production" else "2")
    sub(inf, "verAplic", VER_APLIC)
    sub(inf, "dhEvento", dh)
    sub(inf, "CNPJAutor", cnpj_autor)
    sub(inf, "chNFSe", access_key)
    g = sub(inf, grupo)
    sub(g, "xDesc", x_desc)
    sub(g, "cMotivo", c_motivo)
    sub(g, "xMotivo", x_motivo)
    return PedidoEvento(pre_id=pre_id, xml=etree.tostring(root, xml_declaration=True, encoding="UTF-8"), dh_evento=dh)


class _NoNet(etree.Resolver):
    def resolve(self, url, pubid, context):  # noqa: ANN001, ANN201
        if url.startswith(("http://", "https://")):
            raise ValueError("resolução remota de schema bloqueada")
        return None


@lru_cache(maxsize=4)
def _schema(environment: str) -> etree.XMLSchema:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    parser.resolvers.add(_NoNet())
    return etree.XMLSchema(etree.parse(str(XSD_BY_ENV[environment]), parser))


def validar_xsd(xml: bytes, environment: str) -> None:
    schema = _schema(environment)
    doc = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
    if not schema.validate(etree.ElementTree(doc)):
        raise EventoInvalido([f"linha {e.line}: {e.message}"[:300] for e in list(schema.error_log)[:20]])


def resumo_evento(xml: bytes) -> dict:
    """Campos do XML `evento` devolvido pela Sefin (TCEvento/TCInfEvento), para conferência e registro."""
    from defusedxml import ElementTree as SafeET

    root = SafeET.fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    n = "{" + NS + "}"
    inf = root.find(f"{n}infEvento")
    if root.tag != f"{n}evento" or inf is None:
        raise EventoInvalido(["XML devolvido não é um evento NFS-e."])
    ped = inf.find(f"{n}pedRegEvento/{n}infPedReg")

    def t(el, path):  # noqa: ANN001, ANN202
        x = el.find(path) if el is not None else None
        return x.text.strip() if x is not None and x.text else None

    grupo = next((c for c in (list(ped) if ped is not None else []) if c.tag.startswith(f"{n}e")), None)
    return {
        "event_id": inf.get("Id"), "n_seq_evento": t(inf, f"{n}nSeqEvento"), "dh_proc": t(inf, f"{n}dhProc"),
        "pre_id": ped.get("Id") if ped is not None else None, "access_key": t(ped, f"{n}chNFSe"),
        "event_code": grupo.tag[len(n):] if grupo is not None else None,
        "c_motivo": t(grupo, f"{n}cMotivo"), "x_motivo": t(grupo, f"{n}xMotivo"),
    }
