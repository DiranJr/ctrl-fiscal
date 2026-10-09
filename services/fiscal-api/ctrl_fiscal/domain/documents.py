from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from ..models import Document, DocumentEvent
from . import retencoes_doc
from .common import DomainError, audit, money


@dataclass
class DocumentFilters:
    direction: str | None = None
    query: str | None = None
    status: str | None = None
    review_status: str | None = None
    competence: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    # Retenções (colunas normalizadas por domain/retencoes_doc): WITH | WITHOUT | UNKNOWN
    retention: str | None = None
    retention_types: list[str] = field(default_factory=list)  # ISS | INSS | IRRF | CONTRIB
    retention_match: str = "ANY"  # ANY ("qualquer um") | ALL ("todos")
    withheld_min: Decimal | None = None
    withheld_max: Decimal | None = None

    def as_dict(self) -> dict:
        return {
            "direction": self.direction, "query": self.query, "status": self.status,
            "review_status": self.review_status, "competence": self.competence,
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "retention": self.retention, "retention_types": list(self.retention_types),
            "retention_match": self.retention_match,
            "withheld_min": str(self.withheld_min) if self.withheld_min is not None else None,
            "withheld_max": str(self.withheld_max) if self.withheld_max is not None else None,
        }

    @staticmethod
    def from_dict(d: dict) -> "DocumentFilters":
        return DocumentFilters(
            direction=d.get("direction"), query=d.get("query"), status=d.get("status"),
            review_status=d.get("review_status"), competence=d.get("competence"),
            date_from=date.fromisoformat(d["date_from"]) if d.get("date_from") else None,
            date_to=date.fromisoformat(d["date_to"]) if d.get("date_to") else None,
            retention=d.get("retention"), retention_types=list(d.get("retention_types") or []),
            retention_match=d.get("retention_match") or "ANY",
            withheld_min=Decimal(d["withheld_min"]) if d.get("withheld_min") else None,
            withheld_max=Decimal(d["withheld_max"]) if d.get("withheld_max") else None,
        )


RETENTION_TYPE_COLUMNS = {"ISS": Document.ret_has_iss, "INSS": Document.ret_has_inss, "IRRF": Document.ret_has_irrf,
                          "CONTRIB": Document.ret_has_contrib}


def document_query(company_id: str, environment: str, f: DocumentFilters, tz: str) -> Select:
    q = select(Document).where(Document.company_id == company_id, Document.environment == environment)
    if f.direction:
        q = q.where(Document.direction == f.direction)
    if f.status:
        q = q.where(Document.status == f.status)
    if f.review_status:
        q = q.where(Document.review_status == f.review_status)
    if f.competence:
        q = q.where(Document.competence == f.competence)
    zone = ZoneInfo(tz)
    if f.date_from:
        q = q.where(Document.issued_at >= datetime.combine(f.date_from, time.min, zone))
    if f.date_to:
        q = q.where(Document.issued_at < datetime.combine(f.date_to + timedelta(days=1), time.min, zone))
    if f.retention:
        q = q.where(Document.ret_class == f.retention)
    if f.retention_types:
        conds = [RETENTION_TYPE_COLUMNS[k].is_(True) for k in f.retention_types]
        q = q.where(and_(*conds) if f.retention_match == "ALL" else or_(*conds))
    if f.withheld_min is not None:
        q = q.where(Document.ret_total_effective >= f.withheld_min)
    if f.withheld_max is not None:
        q = q.where(Document.ret_total_effective <= f.withheld_max)
    if f.query:
        term = f.query.strip()
        like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        q = q.where(
            or_(
                Document.number == term,
                Document.access_key == term,
                Document.issuer_doc == term,
                Document.recipient_doc == term,
                Document.issuer_name.like(like, escape="\\"),
                Document.recipient_name.like(like, escape="\\"),
                Document.description.like(like, escape="\\"),
            )
        )
    return q


def list_documents(s: Session, company_id: str, environment: str, f: DocumentFilters, tz: str, offset: int, limit: int):  # noqa: ANN201
    q = document_query(company_id, environment, f, tz)
    total = s.scalar(select(func.count()).select_from(q.subquery()))
    rows = s.scalars(q.order_by(Document.issued_at.desc(), Document.id).offset(offset).limit(limit)).all()
    return rows, total


def get_document(s: Session, company_id: str, document_id: str, lock: bool = False) -> Document:
    q = select(Document).where(Document.id == document_id, Document.company_id == company_id)
    if lock:
        q = q.with_for_update()
    d = s.scalar(q)
    if d is None:
        raise DomainError(404, "DOCUMENT_NOT_FOUND", "Documento não encontrado.")
    return d


def review_document(
    s: Session, doc: Document, review_status: str, note: str | None, expected_version: int, user_id: str, now: datetime,
    trace_id: str | None,
) -> Document:
    if doc.direction != "RECEIVED":
        raise DomainError(409, "REVIEW_ONLY_RECEIVED", "Conferência disponível apenas para serviços tomados.")
    if doc.version != expected_version:
        raise DomainError(409, "VERSION_CONFLICT", "Documento alterado por outra pessoa. Recarregue antes de salvar.")
    previous = doc.review_status
    doc.review_status = review_status
    doc.review_note = note
    doc.reviewed_by = user_id
    doc.reviewed_at = now
    doc.version += 1
    doc.updated_at = now
    audit(s, now, "document.reviewed", company_id=doc.company_id, actor_id=user_id, target_type="document",
          target_id=doc.id, details={"from": previous, "to": review_status}, trace_id=trace_id)
    return doc


def events_for(s: Session, doc: Document) -> list[DocumentEvent]:
    return s.scalars(
        select(DocumentEvent).where(
            DocumentEvent.company_id == doc.company_id,
            DocumentEvent.environment == doc.environment,
            DocumentEvent.access_key == doc.access_key,
        ).order_by(DocumentEvent.occurred_at, DocumentEvent.id)
    ).all()


def _cstat_label(code: str | None) -> str | None:
    from .ingest import CSTAT_LABELS

    return CSTAT_LABELS.get(code or "")


def danfse_available(d: Document) -> bool:
    """DANFSe só de NFS-e com XML oficial guardado; documento de simulação nunca gera DANFSe."""
    return d.xml_blob_id is not None and not d.simulated


def danfse_marca(d: Document) -> str | None:
    """Marca d'água da NT 008 (item 2.5) a partir dos eventos oficiais já ingeridos: e105102 / substituta → SUBSTITUÍDA."""
    if d.cancellation_event_type == "e105102" or d.substituted_by_access_key:
        return "SUBSTITUÍDA"
    if d.status == "CANCELLED":
        return "CANCELADA"
    return None


def document_out(d: Document) -> dict:
    return {
        "id": d.id,
        "direction": d.direction,
        "company_role": d.company_role,
        "number": d.number,
        "access_key": d.access_key,
        "dps_id": d.dps_id,
        "status": d.status,
        "status_code": d.status_code,
        "status_code_label": _cstat_label(d.status_code),
        "cancellation_event_type": d.cancellation_event_type,
        "substitution": ({"substitutes_access_key": d.substitutes_access_key, "reason_code": d.substitution_reason,
                          "substituted_by_access_key": d.substituted_by_access_key}
                         if (d.substitutes_access_key or d.substituted_by_access_key) else None),
        "competence": d.competence,
        "service_date": d.service_date,
        "issued_at": d.issued_at,
        "processed_at": d.processed_at,
        "issuer": {"name": d.issuer_name, "cnpj": d.issuer_doc},
        "recipient": {"name": d.recipient_name, "cnpj": d.recipient_doc} if (d.recipient_doc or d.recipient_name) else None,
        "intermediary": {"name": d.intermediary_name, "cnpj": d.intermediary_doc} if (d.intermediary_doc or d.intermediary_name) else None,
        "description": d.description,
        "service": {
            "national_code": d.service_national_code,
            "municipal_code": d.service_municipal_code,
            "nbs_code": d.nbs_code,
        },
        "amount": money(d.amount),
        "deductions": money(d.deductions),
        "net_amount": money(d.net_amount),
        "iss_amount": money(d.iss_amount),
        "total_retained": money(d.total_retained),
        "retentions": {
            "iss": money(d.ret_iss),
            "irrf": money(d.ret_irrf),
            "inss": money(d.ret_inss),
            "pis": money(d.ret_pis),
            "cofins": money(d.ret_cofins),
            "csll": money(d.ret_csll),
            "pcc_total": money(d.ret_pcc_total),
        },
        "retention_indicators": d.retention_indicators or {},
        "review_hints": d.review_hints or [],
        "review_status": d.review_status,
        "review_note": d.review_note,
        "reviewed_by": d.reviewed_by,
        "reviewed_at": d.reviewed_at,
        "version": d.version,
        "xml_available": d.xml_blob_id is not None,
        "danfse_available": danfse_available(d),
        "danfse_watermark": danfse_marca(d),
        "retention_summary": retencoes_doc.out(d),
        "source": d.source,
        "simulated": d.simulated,
        "environment": d.environment,
        "customer_id": d.customer_id,
    }


def event_out(e: DocumentEvent) -> dict:
    return {
        "id": e.id,
        "event_id": e.event_id,
        "event_type": e.event_type,
        "description": e.description,
        "sequence": e.sequence,
        "occurred_at": e.occurred_at,
        "cancels_document": e.cancels_document,
        "substitute_access_key": e.substitute_access_key,
        "source": e.source,
        "simulated": e.simulated,
    }
