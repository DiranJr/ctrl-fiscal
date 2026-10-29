"""Reconciliação do ISS e dos totais: ESTIMATIVA congelada na emissão × valores OFICIAIS da NFS-e autorizada.

Antes da autorização, retenção e líquido são estimativas (retencoes.resumo, basis=ESTIMATE): usam o pAliq enviado ou,
sem ele, a alíquota de REFERÊNCIA interna. Depois da autorização, valem os valores devolvidos pela Sefin no XML
(pAliqAplic, vISSQN, vTotalRet, vLiq). Esta função só compara — não altera documento emitido nem estimativa.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from defusedxml import ElementTree as SafeET

from ..models import Document
from .common import money

NS = "{http://www.sped.fazenda.gov.br/nfse}"
ISS_RETIDO = ("RETAINED_BY_TOMADOR", "RETAINED_BY_INTERMEDIARY")


def _dec(v: object) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def aliquota_aplicada(xml: bytes | None) -> str | None:
    """infNFSe/valores/pAliqAplic da NFS-e oficial (alíquota que a Sefin aplicou)."""
    if not xml:
        return None
    try:
        root = SafeET.fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except SafeET.ParseError:
        return None
    el = root.find(f"{NS}infNFSe/{NS}valores/{NS}pAliqAplic")
    v = _dec(el.text if el is not None else None)
    return None if v is None else str(v.quantize(Decimal("0.01")))


def reconciliar(snapshot: dict | None, doc: Document, xml: bytes | None) -> dict | None:
    """None quando o documento não veio de uma emissão pela plataforma (não há estimativa para comparar)."""
    if doc.source != "EMISSION" or not snapshot:
        return None
    est = snapshot.get("retention_summary") or {}
    tax = snapshot.get("tax_settings") or {}
    iss = est.get("iss") or {}
    retido = tax.get("iss_retention") in ISS_RETIDO
    estimado_iss = iss.get("estimated_amount", iss.get("amount"))  # snapshots antigos não têm estimated_amount
    completo = bool(est.get("complete"))
    linhas = [
        ("iss_rate", "Alíquota do ISS (%)", iss.get("rate"), aliquota_aplicada(xml)),
        ("iss_amount", "ISSQN (vISSQN)", estimado_iss, money(doc.iss_amount)),
        ("iss_retained", "ISS retido", iss.get("amount") if retido else "0.00",
         money(doc.ret_iss) if retido else "0.00"),
        ("total_withheld", "Total retido (vTotalRet)", est.get("total_withheld") if completo else None,
         money(doc.total_retained)),
        ("net_amount", "Valor líquido (vLiq)", est.get("net_amount") if completo else None, money(doc.net_amount)),
    ]
    out = []
    for key, label, e, o in linhas:
        de, do = _dec(e), _dec(o)
        status = ("OFFICIAL_MISSING" if do is None else "ESTIMATE_MISSING" if de is None else
                  "MATCH" if de == do else "DIFFERENT")
        out.append({"key": key, "label": label, "estimated": e, "official": o, "status": status,
                    "difference": None if de is None or do is None else str((do - de).quantize(Decimal("0.01")))})
    estados = {x["status"] for x in out}
    geral = "DIFFERENT" if "DIFFERENT" in estados else "MATCH" if estados == {"MATCH"} else "PARTIAL"
    return {
        "status": geral,
        "dps_rate": tax.get("iss_rate") or None,
        "reference_rate": tax.get("iss_reference_rate") or None,
        "rate_source": iss.get("rate_source") or ("DPS_PALIQ" if tax.get("iss_rate") else None),
        "lines": out,
        "note": ("Valores oficiais da NFS-e autorizada (Sefin) prevalecem; a estimativa é a congelada no agendamento. "
                 + ("Sem pAliq na DPS, a alíquota aplicada é a determinada pelo município."
                    if not tax.get("iss_rate") else "")).strip(),
    }
