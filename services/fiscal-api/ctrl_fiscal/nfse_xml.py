"""Leitura segura de XML NFS-e nacional (NFSe/evento) e de ZIP/GZip.

Mapeamento conforme XSD oficial v1.01 (pacote NFSe-ESQUEMAS_XSD-v1.01-20260209,
namespace http://www.sped.fazenda.gov.br/nfse); contratos oficiais em docs/oficial/.
Regras:
- DTD, entidades e referências externas são bloqueados (defusedxml).
- Valores ausentes permanecem None; nada é convertido para zero.
- Retenções: indicação (tpRetISSQN / tpRetPisCofins) e valor ficam separados.
- PCC consolidado não existe no leiaute nacional: pcc_total fica None e
  nunca é derivado nem rateado.
"""
from __future__ import annotations

import gzip
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

NS = "http://www.sped.fazenda.gov.br/nfse"
_DEC_RE = re.compile(r"^(0|0\.[0-9]{2}|[1-9][0-9]{0,14}(\.[0-9]{2})?)$")
_KEY_RE = re.compile(r"^[0-9]{6}[0-9A-Z]{14}[0-9]{30}$")
_DOC_RE = re.compile(r"^[0-9A-Z]{11,14}$")

CANCELLING_EVENTS = {
    "e101101": "Cancelamento de NFS-e",
    "e105102": "Cancelamento de NFS-e por Substituição",
    "e105104": "Cancelamento de NFS-e Deferido por Análise Fiscal",
    "e305101": "Cancelamento de NFS-e por Ofício",
}
KNOWN_EVENTS = {
    **CANCELLING_EVENTS,
    "e101103": "Solicitação de Análise Fiscal para Cancelamento de NFS-e",
    "e105105": "Cancelamento de NFS-e Indeferido por Análise Fiscal",
    "e202201": "Confirmação do Prestador",
    "e203202": "Confirmação do Tomador",
    "e204203": "Confirmação do Intermediário",
    "e205204": "Confirmação Tácita",
    "e202205": "Rejeição do Prestador",
    "e203206": "Rejeição do Tomador",
    "e204207": "Rejeição do Intermediário",
    "e205208": "Anulação da Rejeição",
    "e305102": "Bloqueio de NFS-e por Ofício",
    "e305103": "Desbloqueio de NFS-e por Ofício",
}

# TSTipoRetISSQN (XSD v1.01)
ISS_RETENTION = {"1": "NOT_RETAINED", "2": "RETAINED_BY_TOMADOR", "3": "RETAINED_BY_INTERMEDIARY"}
# TSTipoRetPISCofins (XSD v1.01): (PIS, COFINS, CSLL); None = código não trata o tributo.
PIS_COFINS_CSLL = {
    "0": (False, False, False),
    "1": (True, True, None),
    "2": (False, False, None),
    "3": (True, True, True),
    "4": (True, True, False),
    "5": (True, False, False),
    "6": (False, True, False),
    "7": (False, True, True),
    "8": (False, False, True),
    "9": (True, False, True),
}


class XmlRejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Limits:
    max_xml_bytes: int = 2 * 1024 * 1024
    max_zip_entries: int = 500
    max_zip_total_uncompressed: int = 200 * 1024 * 1024
    max_zip_ratio: int = 100
    max_gzip_bytes: int = 5 * 1024 * 1024


@dataclass
class Party:
    doc: str | None
    doc_type: str | None  # CNPJ | CPF | NIF
    name: str | None


@dataclass
class ParsedNfse:
    access_key: str
    nfse_id: str
    layout_version: str | None
    number: str | None
    status_code: str | None
    processed_at: datetime | None
    dps_id: str | None
    tp_amb: str | None
    issued_at: datetime | None
    service_date: str | None
    competence: str | None
    issuer: Party
    recipient: Party | None
    intermediary: Party | None
    description: str | None
    service_national_code: str | None
    service_municipal_code: str | None
    nbs_code: str | None
    service_location_code: str | None
    obra_code: str | None
    issqn_taxation_code: str | None
    pis_cofins_cst: str | None
    approx_tax_mode: str | None
    amount: Decimal | None
    deductions: Decimal | None
    net_amount: Decimal | None
    iss_amount: Decimal | None
    total_retained: Decimal | None
    retentions: dict[str, Decimal | None]
    indicators: dict
    review_hints: list[dict] = field(default_factory=list)
    ibs_cbs: dict | None = None  # IBSCBS (somente tomador = destinatário)
    ibs_cbs_official: dict | None = None  # infNFSe/IBSCBS: valores CALCULADOS pela Sefin, preservados como vieram
    service_extra: dict | None = None  # serv/obra (cCIB, inscImobFisc, end) e serv/infoCompl
    substitutes_key: str | None = None  # DPS/infDPS/subst/chSubstda (esta NFS-e substitui outra)
    substitution_reason: str | None = None  # subst/cMotivo


@dataclass
class ParsedEvent:
    event_id: str
    access_key: str
    event_type: str
    description: str | None
    sequence: str | None
    occurred_at: datetime | None
    tp_amb: str | None
    cancels_document: bool
    substitute_key: str | None = None  # e105102/chSubstituta (NFS-e que substituiu a do evento)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el, name: str):  # noqa: ANN001, ANN202
    if el is None:
        return None
    for ch in el:
        if _local(ch.tag) == name:
            return ch
    return None


def _path(el, *names: str):  # noqa: ANN001, ANN202
    for n in names:
        el = _child(el, n)
        if el is None:
            return None
    return el


def _text(el, *names: str) -> str | None:  # noqa: ANN001
    node = _path(el, *names) if names else el
    if node is None or node.text is None:
        return None
    t = node.text.strip()
    return t or None


def _money(el, *names: str) -> Decimal | None:  # noqa: ANN001
    t = _text(el, *names)
    if t is None:
        return None
    if not _DEC_RE.match(t):
        raise XmlRejected("INVALID_DECIMAL", f"Valor decimal inválido em {'/'.join(names)}")
    try:
        return Decimal(t)
    except InvalidOperation as exc:
        raise XmlRejected("INVALID_DECIMAL", f"Valor decimal inválido em {'/'.join(names)}") from exc


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        d = datetime.fromisoformat(value)
    except ValueError as exc:
        raise XmlRejected("INVALID_DATETIME", "Data/hora inválida") from exc
    if d.tzinfo is None:
        raise XmlRejected("INVALID_DATETIME", "Data/hora sem fuso (TZD obrigatório no leiaute)")
    return d


def _party(el) -> Party | None:  # noqa: ANN001
    if el is None:
        return None
    for kind in ("CNPJ", "CPF", "NIF"):
        v = _text(el, kind)
        if v:
            return Party(doc=v.upper()[:14], doc_type=kind, name=_text(el, "xNome"))
    return Party(doc=None, doc_type=None, name=_text(el, "xNome"))


def parse_xml_root(raw: bytes, limits: Limits):  # noqa: ANN201
    if len(raw) > limits.max_xml_bytes:
        raise XmlRejected("XML_TOO_LARGE", "XML excede o tamanho máximo configurado")
    try:
        return SafeET.fromstring(raw, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        raise XmlRejected("XML_FORBIDDEN_CONSTRUCT", "XML com DTD/entidade/referência externa bloqueado") from exc
    except SafeET.ParseError as exc:
        raise XmlRejected("XML_MALFORMED", "XML mal formado") from exc


def detect_kind(root) -> str:  # noqa: ANN001
    name = _local(root.tag)
    if name in ("NFe", "nfeProc"):
        raise XmlRejected("NFE_NOT_SUPPORTED", "NF-e de mercadorias está fora do escopo (somente NFS-e)")
    if root.tag.startswith("{") and not root.tag.startswith("{" + NS + "}"):
        raise XmlRejected("UNSUPPORTED_NAMESPACE", "Namespace diferente do leiaute NFS-e nacional")
    if name == "NFSe":
        return "NFSE"
    if name == "evento":
        return "EVENT"
    raise XmlRejected("UNSUPPORTED_LAYOUT", "Leiaute não reconhecido como NFS-e nacional")


def parse_nfse(root) -> ParsedNfse:  # noqa: ANN001, C901
    inf = _child(root, "infNFSe")
    if inf is None:
        raise XmlRejected("MISSING_INFNFSE", "Grupo infNFSe ausente")
    nfse_id = inf.attrib.get("Id", "")
    if not nfse_id.startswith("NFS") or len(nfse_id) != 53:
        raise XmlRejected("INVALID_NFSE_ID", "Id da NFS-e ausente ou fora do padrão")
    access_key = nfse_id[3:]
    if not _KEY_RE.match(access_key):
        raise XmlRejected("INVALID_ACCESS_KEY", "Chave de acesso fora do padrão")
    inf_dps = _path(inf, "DPS", "infDPS")
    if inf_dps is None:
        raise XmlRejected("MISSING_DPS", "Grupo DPS/infDPS ausente")

    prest = _party(_child(inf_dps, "prest"))
    emit = _party(_child(inf, "emit"))
    issuer = prest or emit
    if issuer is not None and issuer.name is None and emit is not None and emit.doc == issuer.doc:
        issuer.name = emit.name
    if issuer is None or issuer.doc is None:
        raise XmlRejected("MISSING_PROVIDER", "Prestador sem identificação")

    d_compet = _text(inf_dps, "dCompet")
    competence = d_compet[:7] if d_compet and re.match(r"^\d{4}-\d{2}-\d{2}$", d_compet) else None

    vals = _child(inf_dps, "valores")
    trib = _child(vals, "trib")
    trib_mun = _child(trib, "tribMun")
    trib_fed = _child(trib, "tribFed")
    piscofins = _child(trib_fed, "piscofins")
    nfse_vals = _child(inf, "valores")

    amount = _money(vals, "vServPrest", "vServ")
    deductions = _money(vals, "vDedRed", "vDR")
    iss_amount = _money(nfse_vals, "vISSQN")
    total_retained = _money(nfse_vals, "vTotalRet")
    net_amount = _money(nfse_vals, "vLiq")
    v_ret_cp = _money(trib_fed, "vRetCP")
    v_ret_irrf = _money(trib_fed, "vRetIRRF")
    v_ret_csll = _money(trib_fed, "vRetCSLL")
    v_pis = _money(piscofins, "vPis")
    v_cofins = _money(piscofins, "vCofins")

    hints: list[dict] = []
    indicators: dict = {}

    tp_ret_iss = _text(trib_mun, "tpRetISSQN")
    iss_status = ISS_RETENTION.get(tp_ret_iss or "", "NOT_INFORMED")
    ret_iss = None
    if iss_status in ("RETAINED_BY_TOMADOR", "RETAINED_BY_INTERMEDIARY"):
        ret_iss = iss_amount
        if iss_amount is None:
            hints.append({"code": "ISS_RETAINED_AMOUNT_NOT_INFORMED", "message": "ISS indicado como retido sem vISSQN."})
    indicators["iss"] = {"status": iss_status, "source": f"tpRetISSQN={tp_ret_iss}" if tp_ret_iss else None}

    tp_pc = _text(piscofins, "tpRetPisCofins")
    pcc = PIS_COFINS_CSLL.get(tp_pc or "")

    def _flag(v: bool | None) -> str:
        return "NOT_INFORMED" if v is None else ("RETAINED" if v else "NOT_RETAINED")

    src = f"tpRetPisCofins={tp_pc}" if tp_pc else None
    # vPis/vCofins são descritos no XSD como "débito ... apuração própria": não são
    # tratados como valor retido; ficam como valor informado ao lado da indicação.
    indicators["pis"] = {"status": _flag(pcc[0] if pcc else None), "source": src, "informed_amount": _s(v_pis), "informed_field": "vPis" if v_pis is not None else None}
    indicators["cofins"] = {"status": _flag(pcc[1] if pcc else None), "source": src, "informed_amount": _s(v_cofins), "informed_field": "vCofins" if v_cofins is not None else None}
    csll_flag = pcc[2] if pcc else None
    if csll_flag is None and v_ret_csll is not None:
        indicators["csll"] = {"status": "INFORMED", "source": "vRetCSLL"}
    else:
        indicators["csll"] = {"status": _flag(csll_flag), "source": src}
    indicators["irrf"] = {"status": "INFORMED" if v_ret_irrf is not None else "NOT_INFORMED", "source": "vRetIRRF" if v_ret_irrf is not None else None}
    indicators["inss"] = {"status": "INFORMED" if v_ret_cp is not None else "NOT_INFORMED", "source": "vRetCP" if v_ret_cp is not None else None}
    indicators["pcc_total"] = {"status": "NOT_IN_LAYOUT", "source": None}
    indicators["total_retained"] = _s(total_retained)

    if pcc and pcc[0] and v_pis is None:
        hints.append({"code": "PIS_RETAINED_AMOUNT_NOT_INFORMED", "message": "PIS indicado como retido sem valor informado."})
    if pcc and pcc[1] and v_cofins is None:
        hints.append({"code": "COFINS_RETAINED_AMOUNT_NOT_INFORMED", "message": "COFINS indicado como retido sem valor informado."})
    if csll_flag and v_ret_csll is None:
        hints.append({"code": "CSLL_RETAINED_AMOUNT_NOT_INFORMED", "message": "CSLL indicada como retida sem vRetCSLL."})
    if total_retained is not None:
        parts = [v for v in (v_ret_cp, v_ret_irrf, v_ret_csll, ret_iss) if v is not None]
        if sum(parts, Decimal("0")) != total_retained:
            hints.append({
                "code": "TOTAL_RETAINED_DIFFERS",
                "message": "vTotalRet difere da soma de vRetCP+vRetIRRF+vRetCSLL+ISS retido informados; conferir.",
            })

    return ParsedNfse(
        access_key=access_key,
        nfse_id=nfse_id,
        layout_version=root.attrib.get("versao"),
        number=_text(inf, "nNFSe"),
        status_code=_text(inf, "cStat"),
        processed_at=_dt(_text(inf, "dhProc")),
        dps_id=inf_dps.attrib.get("Id"),
        tp_amb=_text(inf_dps, "tpAmb"),
        issued_at=_dt(_text(inf_dps, "dhEmi")),
        service_date=d_compet,
        competence=competence,
        issuer=issuer,
        recipient=_party(_child(inf_dps, "toma")),
        intermediary=_party(_child(inf_dps, "interm")),
        description=_text(inf_dps, "serv", "cServ", "xDescServ"),
        service_national_code=_text(inf_dps, "serv", "cServ", "cTribNac"),
        service_municipal_code=_text(inf_dps, "serv", "cServ", "cTribMun"),
        nbs_code=_text(inf_dps, "serv", "cServ", "cNBS"),
        service_location_code=_text(inf_dps, "serv", "locPrest", "cLocPrestacao"),
        obra_code=_text(inf_dps, "serv", "obra", "cObra"),
        issqn_taxation_code=_text(trib_mun, "tribISSQN"),
        pis_cofins_cst=_text(piscofins, "CST"),
        approx_tax_mode=_approx_mode(_child(trib, "totTrib")),
        ibs_cbs=_ibs_cbs(_child(inf_dps, "IBSCBS")),
        ibs_cbs_official=ibs_cbs_oficial(inf),
        service_extra=servico_extra(_child(inf_dps, "serv")),
        substitutes_key=_text(inf_dps, "subst", "chSubstda"),
        substitution_reason=_text(inf_dps, "subst", "cMotivo"),
        amount=amount,
        deductions=deductions,
        net_amount=net_amount,
        iss_amount=iss_amount,
        total_retained=total_retained,
        retentions={
            "iss": ret_iss,
            "irrf": v_ret_irrf,
            "inss": v_ret_cp,
            "pis": None,
            "cofins": None,
            "csll": v_ret_csll,
            "pcc_total": None,
        },
        indicators=indicators,
        review_hints=hints,
    )


def _approx_mode(tot) -> str | None:  # noqa: ANN001
    if tot is None:
        return None
    first = next(iter(tot), None)
    return {"vTotTrib": "VALUES", "pTotTrib": "PERCENT", "indTotTrib": "NOT_INFORMED",
            "pTotTribSN": "SIMPLES_PERCENT"}.get(_local(first.tag)) if first is not None else None


def parse_event(root) -> ParsedEvent:  # noqa: ANN001
    inf = _child(root, "infEvento")
    if inf is None:
        raise XmlRejected("MISSING_INFEVENTO", "Grupo infEvento ausente")
    event_id = inf.attrib.get("Id", "")
    if not event_id.startswith("EVT") or len(event_id) != 62:
        raise XmlRejected("INVALID_EVENT_ID", "Id do evento ausente ou fora do padrão")
    ped = _path(inf, "pedRegEvento", "infPedReg")
    if ped is None:
        raise XmlRejected("MISSING_PEDREG", "Grupo pedRegEvento/infPedReg ausente")
    key = _text(ped, "chNFSe")
    if not key or not _KEY_RE.match(key):
        raise XmlRejected("INVALID_ACCESS_KEY", "Chave da NFS-e do evento inválida")
    etype = None
    desc = None
    substitute = None
    for ch in ped:
        n = _local(ch.tag)
        if re.match(r"^e\d{6}$", n):
            etype = n
            desc = _text(ch, "xDesc")
            substitute = _text(ch, "chSubstituta") if n == "e105102" else None
            break
    if etype is None:
        raise XmlRejected("UNKNOWN_EVENT_TYPE", "Tipo de evento não identificado")
    return ParsedEvent(
        event_id=event_id,
        access_key=key,
        event_type=etype,
        description=desc or KNOWN_EVENTS.get(etype),
        sequence=_text(inf, "nSeqEvento"),
        occurred_at=_dt(_text(inf, "dhProc")),
        tp_amb=_text(ped, "tpAmb"),
        cancels_document=etype in CANCELLING_EVENTS,
        substitute_key=substitute,
    )


def _s(v: Decimal | None) -> str | None:
    return None if v is None else f"{v.quantize(Decimal('0.01'))}"


def safe_gunzip(data: bytes, max_bytes: int) -> bytes:
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
        out = gz.read(max_bytes + 1)
    if len(out) > max_bytes:
        raise XmlRejected("GZIP_TOO_LARGE", "Conteúdo GZip descompactado excede o limite")
    return out


def sanitize_name(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return (base or "arquivo")[:160]


def iter_zip_xml(data: bytes, limits: Limits):  # noqa: ANN201
    """Gera (nome, bytes|XmlRejected) por entrada, aplicando limites anti zip-bomb."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise XmlRejected("ZIP_INVALID", "Arquivo ZIP inválido") from exc
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > limits.max_zip_entries:
            raise XmlRejected("ZIP_TOO_MANY_ENTRIES", "ZIP excede o número máximo de arquivos")
        declared_total = sum(i.file_size for i in infos)
        if declared_total > limits.max_zip_total_uncompressed:
            raise XmlRejected("ZIP_TOO_LARGE", "ZIP excede o tamanho descompactado máximo")
        consumed = 0
        for info in infos:
            name = sanitize_name(info.filename)
            if info.flag_bits & 0x1:
                yield name, XmlRejected("ZIP_ENCRYPTED", "Entrada criptografada não suportada")
                continue
            lower = info.filename.lower()
            if lower.endswith((".zip", ".gz", ".rar", ".7z")):
                yield name, XmlRejected("NESTED_ARCHIVE", "Arquivo compactado aninhado não é processado")
                continue
            if not lower.endswith(".xml"):
                yield name, XmlRejected("UNSUPPORTED_ENTRY", "Somente arquivos .xml são processados")
                continue
            if info.file_size > limits.max_xml_bytes:
                yield name, XmlRejected("XML_TOO_LARGE", "XML excede o tamanho máximo configurado")
                continue
            if info.compress_size and info.file_size / max(info.compress_size, 1) > limits.max_zip_ratio:
                yield name, XmlRejected("ZIP_RATIO_EXCEEDED", "Taxa de compressão suspeita (possível zip-bomb)")
                continue
            with zf.open(info) as fh:
                content = fh.read(limits.max_xml_bytes + 1)
            if len(content) > limits.max_xml_bytes:
                yield name, XmlRejected("XML_TOO_LARGE", "XML excede o tamanho máximo configurado")
                continue
            consumed += len(content)
            if consumed > limits.max_zip_total_uncompressed:
                raise XmlRejected("ZIP_TOO_LARGE", "ZIP excede o tamanho descompactado máximo")
            yield name, content


def ibs_cbs_oficial(inf) -> dict | None:  # noqa: ANN001
    """Grupo infNFSe/IBSCBS (TCRTCIBSCBS) — o que a Sefin CALCULOU e autorizou: localidade de incidência, base,
    alíquotas (nominal, redução e efetiva por esfera), totais, diferimento, crédito presumido, tributação regular e
    compras governamentais. Cada folha é guardada com o caminho relativo ao grupo e o TEXTO exato do XML (sem
    reformatar nem recalcular), mais os códigos informados na DPS. Nunca se confunde com a prévia local."""
    g = _child(inf, "IBSCBS")
    if g is None:
        return None
    valores: dict[str, str] = {}

    def walk(el, prefix: str) -> None:  # noqa: ANN001
        for ch in el:
            if not isinstance(ch.tag, str):
                continue
            path = f"{prefix}{_local(ch.tag)}"
            if len(ch):
                walk(ch, path + "/")
            elif ch.text and ch.text.strip() and len(valores) < 80:
                valores[path] = ch.text.strip()[:600]

    walk(g, "")
    dps = _child(_path(inf, "DPS", "infDPS"), "IBSCBS")
    codigos = {k: _text(dps, *p) for k, p in (
        ("finNFSe", ("finNFSe",)), ("indFinal", ("indFinal",)), ("cIndOp", ("cIndOp",)), ("indDest", ("indDest",)),
        ("CST", ("valores", "trib", "gIBSCBS", "CST")), ("cClassTrib", ("valores", "trib", "gIBSCBS", "cClassTrib")),
    )} if dps is not None else {}
    return {"valores": valores, "dps": {k: v for k, v in codigos.items() if v}, "fonte": "infNFSe/IBSCBS (Sefin)"}


def _end_obra(e) -> dict | None:  # noqa: ANN001
    """TCEnderObraEvento nacional (CEP); endereço no exterior (endExt) não é suportado na emissão."""
    if e is None or _text(e, "CEP") is None:
        return None
    out = {"postal_code": _text(e, "CEP"), "street": _text(e, "xLgr"), "number": _text(e, "nro"),
           "complement": _text(e, "xCpl"), "district": _text(e, "xBairro")}
    return {k: v for k, v in out.items() if v is not None}


def servico_extra(serv) -> dict | None:  # noqa: ANN001
    obra = _child(serv, "obra")
    comp = _child(serv, "infoCompl")
    out: dict = {}
    if obra is not None:
        out.update(obra_cib=_text(obra, "cCIB"), obra_property_registration=_text(obra, "inscImobFisc"),
                   obra_address=_end_obra(_child(obra, "end")))
    if comp is not None:
        itens = [(_text(i) or "") for i in (_child(comp, "gItemPed") or []) if _local(i.tag) == "xItemPed"]
        info = {"technical_doc": _text(comp, "idDocTec"), "document_reference": _text(comp, "docRef"),
                "order_number": _text(comp, "xPed"), "order_items": [i for i in itens if i] or None,
                "notes": _text(comp, "xInfComp")}
        out["additional_info"] = {k: v for k, v in info.items() if v} or None
    out = {k: v for k, v in out.items() if v}
    return out or None


def _ibs_cbs(g):  # noqa: ANN001, ANN202
    """IBSCBS da DPS para referência de cópia (NFS-e regular, finNFSe=0): códigos e grupos condicionais que a
    plataforma emite. Operações não suportadas (tpOper/gRefNFSe/tpEnteGov, reembolso) não são copiadas."""
    if g is None or _text(g, "finNFSe") != "0":
        return None
    if any(_child(g, k) is not None for k in ("tpOper", "gRefNFSe", "tpEnteGov")) or \
            _path(g, "valores", "gReeRepRes") is not None:
        return None
    gi = _path(g, "valores", "trib", "gIBSCBS")
    out = {
        "operation_indicator": _text(g, "cIndOp"),
        "cst": _text(gi, "CST"),
        "class_code": _text(gi, "cClassTrib"),
        "personal_use": _text(g, "indFinal"),
    }
    if not (out["operation_indicator"] and out["cst"] and out["class_code"]):
        return None
    if _text(gi, "cCredPres"):
        out["presumed_credit"] = _text(gi, "cCredPres")
    reg = _child(gi, "gTribRegular")
    if reg is not None:
        out["regular"] = {"cst": _text(reg, "CSTReg"), "class_code": _text(reg, "cClassTribReg")}
    dif = _child(gi, "gDif")
    if dif is not None:
        out["deferral"] = {"uf": _text(dif, "pDifUF"), "mun": _text(dif, "pDifMun"), "cbs": _text(dif, "pDifCBS")}
    im = _child(g, "imovel")
    if im is not None:
        out["real_estate"] = {k: v for k, v in (("registration", _text(im, "inscImobFisc")), ("cib", _text(im, "cCIB")),
                                                ("address", _end_obra(_child(im, "end")))) if v}
    dest = _child(g, "dest") if _text(g, "indDest") == "1" else None
    if dest is not None:
        kind = next((k for k in ("CNPJ", "CPF", "NIF") if _text(dest, k)), None)
        r = {"document_type": kind or "NO_NIF", "document": _text(dest, kind) if kind else None,
             "no_nif_reason": None if kind else _text(dest, "cNaoNIF"), "name": _text(dest, "xNome"),
             "phone": _text(dest, "fone"), "email": _text(dest, "email")}
        en = _path(dest, "end", "endNac")
        if en is not None:
            r["address"] = {"municipality_code": _text(en, "cMun"), "postal_code": _text(en, "CEP"),
                            "street": _text(dest, "end", "xLgr"), "number": _text(dest, "end", "nro"),
                            "complement": _text(dest, "end", "xCpl"), "district": _text(dest, "end", "xBairro")}
            r["address"] = {k: v for k, v in r["address"].items() if v is not None}
        out["recipient"] = {k: v for k, v in r.items() if v is not None}
    return out
