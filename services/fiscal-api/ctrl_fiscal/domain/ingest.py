"""Ingestão de XML NFS-e/evento (importação manual e distribuição ADN).

Regras de papel: RECEIVED somente quando o CNPJ COMPLETO do tomador é o da
empresa; ISSUED quando é o prestador. Intermediário e filiais com a mesma raiz
não viram documentos listáveis (ficam fora de escopo, sem perda do bruto).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ctrl_common.fiscal_types import cnpj_root

from ..clients import CompanyInfo
from ..models import Document, DocumentEvent, StoredBlob
from ..nfse_xml import Limits, ParsedEvent, ParsedNfse, XmlRejected, detect_kind, parse_event, parse_nfse, parse_xml_root
from . import retencoes_doc
from .common import store_blob

# cStat que indicam NFS-e GERADA (válida até evento de cancelamento). 100/102/103/107 constam do Anexo I e da
# enumeração TStat dos XSD v1.01. 101 = "NFS-e de Substituição Gerada": não está na enumeração dos XSD v1.01,
# mas é emitido pelo Sistema Nacional (DANFSe do portal nacional: "Situação da NFS-e: 101 - NFS-e de
# Substituição Gerada"; 17 NFS-e reais da empresa no ADN, todas com DPS/subst). O estado ATUAL vem dos eventos.
AUTHORIZED_CSTAT = {"100", "101", "102", "103", "107"}
CSTAT_LABELS = {
    "100": "NFS-e Gerada",
    "101": "NFS-e de Substituição Gerada",
    "102": "NFS-e de Decisão Judicial ou Administrativa",
    "103": "NFS-e Avulsa",
    "107": "NFS-e MEI",
}


@dataclass
class IngestResult:
    kind: str  # NFSE | EVENT
    outcome: str  # CREATED | DUPLICATE | EVENT_CREATED | EVENT_DUPLICATE | OUT_OF_SCOPE
    access_key: str | None
    company_role: str | None = None
    document_id: str | None = None
    event_row_id: str | None = None
    out_of_scope_code: str | None = None


def company_role(parsed: ParsedNfse, company_cnpj: str) -> tuple[str | None, str | None]:
    """Retorna (papel, código fora de escopo)."""
    cnpj = company_cnpj.upper()
    if parsed.issuer and parsed.issuer.doc == cnpj:
        return "PRESTADOR", None
    if parsed.recipient and parsed.recipient.doc == cnpj:
        return "TOMADOR", None
    if parsed.intermediary and parsed.intermediary.doc == cnpj:
        return "INTERMEDIARIO", "INTERMEDIARY_ROLE_NOT_LISTED"
    root = cnpj_root(cnpj)
    for p in (parsed.issuer, parsed.recipient, parsed.intermediary):
        if p and p.doc_type == "CNPJ" and p.doc and cnpj_root(p.doc) == root:
            return None, "OTHER_BRANCH_SAME_ROOT"
    return None, "NOT_COMPANY_DOCUMENT"


def ingest_xml(
    s: Session,
    *,
    company: CompanyInfo,
    environment: str,
    raw: bytes,
    source: str,
    simulated: bool,
    now: datetime,
    limits: Limits,
    blob: StoredBlob | None = None,
) -> IngestResult:
    """Persiste NFS-e/evento. Levanta XmlRejected em XML inválido/inseguro.

    Deve ser chamado dentro de uma transação do chamador; usa savepoint para
    tratar corrida de duplicidade sem abortar a transação externa.
    """
    root = parse_xml_root(raw, limits)
    kind = detect_kind(root)
    if kind == "EVENT":
        return _ingest_event(s, company, environment, parse_event(root), raw, source, simulated, now, blob)
    parsed = parse_nfse(root)
    role, oos = company_role(parsed, company.cnpj)
    if role not in ("PRESTADOR", "TOMADOR"):
        return IngestResult("NFSE", "OUT_OF_SCOPE", parsed.access_key, role, out_of_scope_code=oos)
    existing = s.scalar(
        select(Document.id).where(
            Document.company_id == company.id, Document.environment == environment, Document.access_key == parsed.access_key
        )
    )
    if existing:
        return IngestResult("NFSE", "DUPLICATE", parsed.access_key, role, document_id=existing)
    if blob is None:
        blob = store_blob(s, company.id, raw, "XML", now)
    doc = _document_from(parsed, company, environment, role, source, simulated, now, blob.id)
    retencoes_doc.apply(doc)
    try:
        with s.begin_nested():
            s.add(doc)
            s.flush()
    except IntegrityError:
        existing = s.scalar(
            select(Document.id).where(
                Document.company_id == company.id, Document.environment == environment, Document.access_key == parsed.access_key
            )
        )
        return IngestResult("NFSE", "DUPLICATE", parsed.access_key, role, document_id=existing)
    # Eventos que chegaram antes da NFS-e (ordem do NSU segue a recepção no ADN).
    pending = s.scalars(
        select(DocumentEvent).where(
            DocumentEvent.company_id == company.id,
            DocumentEvent.environment == environment,
            DocumentEvent.access_key == parsed.access_key,
            DocumentEvent.document_id.is_(None),
        )
    ).all()
    for ev in pending:
        ev.document_id = doc.id
        if ev.cancels_document:
            doc.status = "CANCELLED"
            doc.cancellation_event_type = ev.event_type
            if ev.substitute_access_key:
                doc.substituted_by_access_key = ev.substitute_access_key
    _link_substitution(s, company, environment, doc, now)
    return IngestResult("NFSE", "CREATED", parsed.access_key, role, document_id=doc.id)


def _link_substitution(s: Session, company: CompanyInfo, environment: str, doc: Document, now: datetime) -> None:
    """Vínculos de substituição nos dois sentidos, no mesmo ambiente. Não altera o status de nenhuma nota:
    a substituída só é cancelada pelo evento e105102 (ou outro evento de cancelamento)."""
    if doc.substitutes_access_key:
        old = s.scalar(select(Document).where(
            Document.company_id == company.id, Document.environment == environment,
            Document.access_key == doc.substitutes_access_key).with_for_update())
        if old is not None and not old.substituted_by_access_key:
            old.substituted_by_access_key = doc.access_key
            old.version += 1
            old.updated_at = now
    if not doc.substituted_by_access_key:
        newer = s.scalar(select(Document.access_key).where(
            Document.company_id == company.id, Document.environment == environment,
            Document.substitutes_access_key == doc.access_key))
        if newer:
            doc.substituted_by_access_key = newer


def _document_from(
    p: ParsedNfse, company: CompanyInfo, environment: str, role: str, source: str, simulated: bool, now: datetime, blob_id: str
) -> Document:
    received = role == "TOMADOR"
    return Document(
        company_id=company.id,
        environment=environment,
        direction="RECEIVED" if received else "ISSUED",
        company_role=role,
        access_key=p.access_key,
        nfse_id=p.nfse_id,
        number=p.number,
        dps_id=p.dps_id,
        status="AUTHORIZED" if p.status_code in AUTHORIZED_CSTAT else "UNKNOWN",
        status_code=p.status_code,
        substitutes_access_key=p.substitutes_key,
        substitution_reason=p.substitution_reason,
        layout_version=p.layout_version,
        tp_amb=p.tp_amb,
        competence=p.competence,
        service_date=p.service_date,
        issued_at=p.issued_at,
        processed_at=p.processed_at,
        issuer_doc=p.issuer.doc,
        issuer_name=p.issuer.name,
        recipient_doc=p.recipient.doc if p.recipient else None,
        recipient_name=p.recipient.name if p.recipient else None,
        intermediary_doc=p.intermediary.doc if p.intermediary else None,
        intermediary_name=p.intermediary.name if p.intermediary else None,
        description=p.description,
        service_national_code=p.service_national_code,
        service_municipal_code=p.service_municipal_code,
        nbs_code=p.nbs_code,
        service_location_code=p.service_location_code,
        obra_code=p.obra_code,
        amount=p.amount,
        deductions=p.deductions,
        net_amount=p.net_amount,
        iss_amount=p.iss_amount,
        ret_iss=p.retentions["iss"],
        ret_irrf=p.retentions["irrf"],
        ret_inss=p.retentions["inss"],
        ret_pis=p.retentions["pis"],
        ret_cofins=p.retentions["cofins"],
        ret_csll=p.retentions["csll"],
        ret_pcc_total=p.retentions["pcc_total"],
        total_retained=p.total_retained,
        retention_indicators=p.indicators,
        review_hints=p.review_hints,
        tax_settings=tax_settings_from_indicators(p) if not received else None,
        ibs_cbs_official=p.ibs_cbs_official,
        service_extra=p.service_extra,
        review_status="PENDING" if received else None,
        version=1,
        xml_blob_id=blob_id,
        source=source,
        simulated=simulated,
        created_at=now,
        updated_at=now,
    )


def tax_settings_from_indicators(p: ParsedNfse) -> dict:
    """Referência editável para cópia de nota emitida (revalidada no rascunho).

    Valores de totTrib não são copiados (dependem do valor da nova nota); só o modo de informação.
    """
    iss = p.indicators.get("iss", {}).get("status")
    src = p.indicators.get("pis", {}).get("source") or ""
    return {
        "issqn_taxation": {"1": "TAXABLE", "2": "IMMUNE", "3": "EXPORT", "4": "NOT_INCIDENT"}.get(p.issqn_taxation_code or ""),
        "iss_retention": iss if iss in ("NOT_RETAINED", "RETAINED_BY_TOMADOR", "RETAINED_BY_INTERMEDIARY") else None,
        "iss_rate": None,
        "pis_cofins_retention": src.split("=", 1)[1] if src.startswith("tpRetPisCofins=") else None,
        "pis_cofins_cst": p.pis_cofins_cst,
        "irrf_withheld": _m(p.retentions.get("irrf")),
        "inss_withheld": _m(p.retentions.get("inss")),
        "csll_withheld": _m(p.retentions.get("csll")),
        "approx_tax": {"mode": p.approx_tax_mode} if p.approx_tax_mode else None,
        "ibs_cbs": p.ibs_cbs,
    }


def _m(v) -> str | None:  # noqa: ANN001
    return None if v is None else f"{v:.2f}"


def _ingest_event(
    s: Session, company: CompanyInfo, environment: str, ev: ParsedEvent, raw: bytes, source: str, simulated: bool,
    now: datetime, blob: StoredBlob | None,
) -> IngestResult:
    existing = s.scalar(
        select(DocumentEvent.id).where(
            DocumentEvent.company_id == company.id, DocumentEvent.environment == environment, DocumentEvent.event_id == ev.event_id
        )
    )
    if existing:
        return IngestResult("EVENT", "EVENT_DUPLICATE", ev.access_key, event_row_id=existing)
    doc = s.scalar(
        select(Document).where(
            Document.company_id == company.id, Document.environment == environment, Document.access_key == ev.access_key
        ).with_for_update()
    )
    if blob is None:
        blob = store_blob(s, company.id, raw, "XML", now)
    row = DocumentEvent(
        company_id=company.id, environment=environment, access_key=ev.access_key, document_id=doc.id if doc else None,
        event_id=ev.event_id, event_type=ev.event_type, sequence=ev.sequence, description=ev.description,
        occurred_at=ev.occurred_at, cancels_document=ev.cancels_document, substitute_access_key=ev.substitute_key,
        blob_id=blob.id, source=source,
        simulated=simulated, created_at=now,
    )
    try:
        with s.begin_nested():
            s.add(row)
            s.flush()
    except IntegrityError:
        return IngestResult("EVENT", "EVENT_DUPLICATE", ev.access_key)
    if doc is not None and ev.cancels_document and doc.status != "CANCELLED":
        doc.status = "CANCELLED"
        doc.cancellation_event_type = ev.event_type
        if ev.substitute_key:
            doc.substituted_by_access_key = ev.substitute_key
        doc.version += 1
        doc.updated_at = now
    return IngestResult("EVENT", "EVENT_CREATED", ev.access_key, document_id=doc.id if doc else None, event_row_id=row.id)
