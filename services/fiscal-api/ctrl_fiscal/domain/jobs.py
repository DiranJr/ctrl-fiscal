"""Jobs assíncronos: importação XML/ZIP e exportação XLSX (executados pelo worker)."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ctrl_common.clock import Clock

from ..clients import CompanyInfo
from ..models import Document, Job, JobError, JobFile, StoredBlob
from ..nfse_xml import Limits, XmlRejected, iter_zip_xml, sanitize_name
from .common import DomainError, audit, read_blob, store_blob
from .retencoes_doc import TP_RET_PIS_COFINS_LABELS
from .documents import DocumentFilters, document_query
from .ingest import ingest_xml
from .leases import fenced_update, lease_free, try_acquire

log = logging.getLogger("ctrl.jobs")
RUNNABLE = ("IMPORT", "EXPORT", "BUNDLE")
MAX_JOB_ATTEMPTS = 3


def create_import_job(
    s: Session, *, company: CompanyInfo, user_id: str, files: list[tuple[str, bytes]], now: datetime, trace_id: str | None
) -> Job:
    job = Job(company_id=company.id, environment=company.environment, type="IMPORT", status="QUEUED",
              params={"files": len(files)}, company_info=company.as_dict(), processed_count=0, error_count=0,
              fencing_token=0, attempts=0, created_by=user_id, created_at=now)
    s.add(job)
    s.flush()
    for seq, (name, data) in enumerate(files):
        blob = store_blob(s, company.id, data, "UPLOAD", now)
        s.add(JobFile(job_id=job.id, seq=seq, display_name=sanitize_name(name), blob_id=blob.id, processed=False))
    audit(s, now, "import.queued", company_id=company.id, actor_id=user_id, target_type="job", target_id=job.id,
          details={"files": len(files)}, trace_id=trace_id)
    return job


def create_export_job(
    s: Session, *, company: CompanyInfo, user_id: str, filters: DocumentFilters, fmt: str, now: datetime, trace_id: str | None,
    environment: str | None = None,
) -> Job:
    job = Job(company_id=company.id, environment=environment or company.environment, type="EXPORT", status="QUEUED",
              params={"filters": filters.as_dict(), "format": fmt}, company_info=company.as_dict(),
              processed_count=0, error_count=0, fencing_token=0, attempts=0, created_by=user_id, created_at=now)
    s.add(job)
    s.flush()
    audit(s, now, "export.queued", company_id=company.id, actor_id=user_id, target_type="job", target_id=job.id,
          details={"filters": filters.as_dict()}, trace_id=trace_id)
    return job


def get_job(s: Session, company: CompanyInfo, job_id: str) -> Job:
    j = s.scalar(select(Job).where(Job.id == job_id, Job.company_id == company.id))
    if j is None:
        raise DomainError(404, "JOB_NOT_FOUND", "Job não encontrado.")
    return j


def download_available(j: Job) -> bool:
    if j.output_path is None:
        return False
    if j.type == "EXPORT":
        return j.status == "SUCCEEDED"
    return j.type == "BUNDLE" and j.status in ("SUCCEEDED", "PARTIAL")


def job_out(s: Session, j: Job, errors_page: int = 1, errors_page_size: int = 50) -> dict:
    from .bundles import stale_documents

    stale = stale_documents(s, j) if j.output_path else 0
    errs = s.scalars(
        select(JobError).where(JobError.job_id == j.id).order_by(JobError.seq)
        .offset((errors_page - 1) * errors_page_size).limit(errors_page_size)
    ).all()
    stored = s.scalar(select(func.count()).select_from(JobError).where(JobError.job_id == j.id))
    return {
        "id": j.id,
        "type": j.type,
        "status": j.status,
        "environment": j.environment,
        "processed_count": j.processed_count,
        "error_count": j.error_count,
        "errors": [{"file": e.file, "code": e.code, "message": e.message} for e in errs],
        "errors_page": errors_page,
        "errors_page_size": errors_page_size,
        "errors_total_stored": stored,
        "errors_truncated": j.error_count > stored,
        "result": {k: v for k, v in (j.result or {}).items() if k != "pdf_watermarks"} if j.result else j.result,
        "download_available": download_available(j) and not stale,
        "stale_pdf_documents": stale,
        "stale_documents": stale,
        "output_expires_at": j.output_expires_at,
        "created_at": j.created_at,
        "started_at": j.started_at,
        "finished_at": j.finished_at,
    }


def acquire_job(sessions: sessionmaker, clock: Clock, owner: str, lease_seconds: int) -> tuple[str, int] | None:
    now = clock.now()
    with sessions() as s, s.begin():
        rows = s.execute(
            select(Job.id, Job.fencing_token)
            .where(Job.type.in_(RUNNABLE), Job.status.in_(("QUEUED", "RUNNING")), lease_free(Job, now))
            .order_by(Job.created_at, Job.id).limit(3).with_for_update(skip_locked=True)
        ).all()
        for jid, tok in rows:
            if try_acquire(s, Job, jid, tok, owner, now, lease_seconds, status="RUNNING"):
                j = s.get(Job, jid, populate_existing=True)
                j.attempts += 1
                if j.started_at is None:
                    j.started_at = now
                if j.attempts > MAX_JOB_ATTEMPTS:
                    # Falhas repetidas (ex.: armazenamento): encerra com erro explícito, sem laço infinito.
                    _add_error(s, j, None, "JOB_ATTEMPTS_EXCEEDED",
                               "Processamento falhou repetidamente; verifique armazenamento/logs e reenvie.", 1000)
                    j.status = "FAILED"
                    j.finished_at = now
                    j.lease_owner = None
                    j.lease_expires_at = None
                    continue
                return jid, tok + 1
    return None


def _add_error(s: Session, job: Job, file: str | None, code: str, message: str, max_stored: int) -> None:
    job.error_count += 1
    if job.error_count <= max_stored:
        s.add(JobError(job_id=job.id, seq=job.error_count, file=(file or "")[:300], code=code[:60], message=message[:500]))


def run_import(
    sessions: sessionmaker, clock: Clock, owner: str, job_id: str, token: int, limits: Limits, max_errors: int, lease_seconds: int
) -> str:
    """Processa arquivo a arquivo; cada um em transação própria e idempotente."""
    with sessions() as s:
        file_ids = [f.id for f in s.scalars(select(JobFile).where(JobFile.job_id == job_id, JobFile.processed.is_(False)).order_by(JobFile.seq))]
    for fid in file_ids:
        with sessions() as s, s.begin():
            now = clock.now()
            job = s.scalar(select(Job).where(Job.id == job_id, Job.fencing_token == token, Job.lease_owner == owner).with_for_update())
            if job is None:
                return "LOST_LEASE"
            job.lease_expires_at = now + timedelta(seconds=lease_seconds)
            company = CompanyInfo.from_dict(job.company_info)
            jf = s.get(JobFile, fid)
            data = read_blob(s.get(StoredBlob, jf.blob_id))
            counts = dict(job.result or {"created": 0, "duplicates": 0, "events": 0, "out_of_scope": 0, "rejected": 0})
            entries: list[tuple[str, bytes | XmlRejected]]
            try:
                if data[:4] == b"PK\x03\x04":
                    entries = [(f"{jf.display_name}/{n}", c) for n, c in iter_zip_xml(data, limits)]
                else:
                    entries = [(jf.display_name, data)]
            except XmlRejected as exc:
                entries = [(jf.display_name, exc)]
            for name, content in entries:
                if isinstance(content, XmlRejected):
                    counts["rejected"] += 1
                    _add_error(s, job, name, content.code, content.message, max_errors)
                    continue
                try:
                    res = ingest_xml(s, company=company, environment=job.environment, raw=content, source="IMPORT",
                                     simulated=False, now=now, limits=limits)
                except XmlRejected as exc:
                    counts["rejected"] += 1
                    _add_error(s, job, name, exc.code, exc.message, max_errors)
                    continue
                job.processed_count += 1
                if res.outcome == "CREATED":
                    counts["created"] += 1
                elif res.outcome in ("EVENT_CREATED",):
                    counts["events"] += 1
                elif res.outcome in ("DUPLICATE", "EVENT_DUPLICATE"):
                    counts["duplicates"] += 1
                    _add_error(s, job, name, "DUPLICATE_IGNORED", "Documento/evento já existente; mantido o original.", max_errors)
                else:
                    counts["out_of_scope"] += 1
                    _add_error(s, job, name, res.out_of_scope_code or "OUT_OF_SCOPE",
                               "Documento não pertence à empresa como prestadora ou tomadora.", max_errors)
            jf.processed = True
            job.result = counts
    with sessions() as s, s.begin():
        job = s.scalar(select(Job).where(Job.id == job_id).with_for_update())
        counts = job.result or {}
        status = "SUCCEEDED" if counts.get("rejected", 0) == 0 and counts.get("out_of_scope", 0) == 0 else (
            "PARTIAL" if counts.get("created", 0) or counts.get("events", 0) or counts.get("duplicates", 0) else "FAILED")
        if not fenced_update(s, Job, job_id, token, owner, status=status, finished_at=clock.now(),
                             lease_owner=None, lease_expires_at=None):
            return "LOST_LEASE"
        return status


EXPORT_COLUMNS = [
    ("Direção", lambda d: "Emitida" if d.direction == "ISSUED" else "Recebida"),
    ("Número", lambda d: d.number),
    ("Chave de acesso", lambda d: d.access_key),
    ("Situação", lambda d: d.status),
    ("Competência", lambda d: d.competence),
    ("Emissão (UTC)", lambda d: d.issued_at.isoformat() if d.issued_at else None),
    ("Prestador", lambda d: d.issuer_name),
    ("CNPJ/CPF prestador", lambda d: d.issuer_doc),
    ("Tomador", lambda d: d.recipient_name),
    ("CNPJ/CPF tomador", lambda d: d.recipient_doc),
    ("Descrição", lambda d: d.description),
    ("Valor do serviço", lambda d: d.amount),
    ("Retenção (classificação)", lambda d: {"WITH": "Com retenção", "WITHOUT": "Sem retenção"}.get(d.ret_class or "", "Informação desconhecida")),
    ("ISS retido", lambda d: d.ret_iss if d.ret_has_iss else None),
    ("IRRF retido", lambda d: d.ret_irrf),
    ("INSS/CP retido", lambda d: d.ret_inss),
    # Valor consolidado das contribuições retidas pela modalidade; o XML não separa PIS/COFINS/CSLL (sem rateio).
    # Modalidade 1: vRetCSLL + vPis + vCofins (apresentação exigida pela NT 008 v1.02).
    ("Contribuições retidas PIS/COFINS/CSLL (consolidado; NT 008 v1.02)", lambda d: d.ret_contrib_amount),
    ("Modalidade tpRetPisCofins", lambda d: (f"{d.ret_pis_cofins_code} - {TP_RET_PIS_COFINS_LABELS.get(d.ret_pis_cofins_code, '')}"
                                             if d.ret_pis_cofins_code else None)),
    ("vRetCSLL (valor do XML)", lambda d: d.ret_csll),
    ("vPis informado (XML)", lambda d: (d.retention_indicators or {}).get("pis", {}).get("informed_amount")),
    ("vCofins informado (XML)", lambda d: (d.retention_indicators or {}).get("cofins", {}).get("informed_amount")),
    ("Total retido (vTotalRet)", lambda d: d.total_retained),
    ("Conferência", lambda d: d.review_status),
    ("Origem", lambda d: d.source + (" (SIMULADO)" if d.simulated else "")),
    ("Ambiente", lambda d: d.environment),
]


def _cell(v):  # noqa: ANN001, ANN202
    # Neutraliza injeção de fórmula em planilhas.
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def run_export(
    sessions: sessionmaker, clock: Clock, owner: str, job_id: str, token: int, export_dir: str, max_rows: int,
    ttl_hours: int = 24,
) -> str:
    from openpyxl import Workbook

    with sessions() as s:
        job = s.get(Job, job_id)
        company = CompanyInfo.from_dict(job.company_info)
        filters = DocumentFilters.from_dict((job.params or {}).get("filters") or {})
        q = document_query(company.id, job.environment, filters, company.timezone)
        total = s.scalar(select(func.count()).select_from(q.subquery()))
    if total > max_rows:
        with sessions() as s, s.begin():
            job = s.get(Job, job_id)
            _add_error(s, job, None, "EXPORT_TOO_LARGE",
                       f"Consulta com {total} documentos excede o limite de {max_rows}. Refine os filtros; nada foi truncado.", 1)
            fenced_update(s, Job, job_id, token, owner, status="FAILED", finished_at=clock.now(), lease_owner=None,
                          lease_expires_at=None, result={"rows": 0, "matched": total})
        return "FAILED"
    Path(export_dir).mkdir(parents=True, exist_ok=True)
    path = os.path.join(export_dir, f"{job_id}.xlsx")  # nome interno; nunca o nome enviado pelo usuário
    tmp = path + ".tmp"
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Documentos")
    ws.append([c[0] for c in EXPORT_COLUMNS])
    rows = 0
    with sessions() as s:
        for d in s.scalars(q.order_by(Document.issued_at, Document.id).execution_options(yield_per=500)):
            ws.append([_cell(fn(d)) for _, fn in EXPORT_COLUMNS])
            rows += 1
    wb.save(tmp)
    if rows != total:
        os.remove(tmp)
        with sessions() as s, s.begin():
            job = s.get(Job, job_id)
            _add_error(s, job, None, "EXPORT_INCONSISTENT", "Quantidade mudou durante a exportação; repita.", 1)
            fenced_update(s, Job, job_id, token, owner, status="FAILED", finished_at=clock.now(), lease_owner=None, lease_expires_at=None)
        return "FAILED"
    os.replace(tmp, path)
    local_date = clock.now().astimezone(ZoneInfo(company.timezone)).strftime("%Y%m%d-%H%M")
    with sessions() as s, s.begin():
        ok = fenced_update(s, Job, job_id, token, owner, status="SUCCEEDED", finished_at=clock.now(), lease_owner=None,
                           lease_expires_at=None, processed_count=rows, output_path=path,
                           output_expires_at=clock.now() + timedelta(hours=ttl_hours),
                           output_name=f"ctrl-fiscal-documentos-{local_date}.xlsx", result={"rows": rows, "matched": total})
    return "SUCCEEDED" if ok else "LOST_LEASE"
