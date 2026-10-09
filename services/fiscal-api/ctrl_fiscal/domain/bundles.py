"""Download em lote de NFS-e (PDF do DANFSe, XML original ou ambos) por job assíncrono BUNDLE.

- O conjunto de documentos é FIXADO na criação (tabela job_documents), seja por seleção, seja por "todos os
  resultados do filtro" (além da página atual). Mudanças posteriores na base não alteram o lote.
- Isolamento: cada documento é relido com company_id + environment do job; nada de outra empresa entra.
- O ZIP é gravado em disco no worker, arquivo a arquivo (sem carregar o lote em memória). Uma pasta por nota,
  nomes saneados e únicos (sequência + número + chave). manifesto.json e manifesto.csv na raiz.
- Falha de um PDF não descarta o XML disponível: resultado PARCIAL com erro por documento.
- O arquivo expira (output_expires_at) e é apagado pelo worker; o download usa ticket de uso único via POST.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import secrets
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from ctrl_common.clock import Clock

from ..clients import CompanyInfo
from ..models import Document, DownloadTicket, Job, JobDocument, StoredBlob
from .common import DomainError, audit, read_blob
from .documents import DocumentFilters, danfse_available, danfse_marca, document_query
from .leases import fenced_update

log = logging.getLogger("ctrl.bundles")
FORMATS = ("PDF", "XML", "PDF_XML")
TICKET_TTL_SECONDS = 60
_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


def limit_for(fmt: str, max_docs_pdf: int, max_docs_xml: int) -> int:
    return max_docs_xml if fmt == "XML" else max_docs_pdf


def create_bundle_job(s: Session, *, company: CompanyInfo, environment: str, user_id: str, fmt: str,
                      document_ids: list[str] | None, filters: DocumentFilters | None, now: datetime,
                      max_docs_pdf: int, max_docs_xml: int, trace_id: str | None) -> Job:
    if fmt not in FORMATS:
        raise DomainError(422, "BUNDLE_FORMAT_INVALID", "Formato inválido (PDF, XML ou PDF_XML).")
    if (document_ids is None) == (filters is None):
        raise DomainError(422, "BUNDLE_SOURCE_INVALID", "Informe document_ids OU filters.")
    limit = limit_for(fmt, max_docs_pdf, max_docs_xml)
    base = select(Document.id).where(Document.company_id == company.id, Document.environment == environment)
    if document_ids is not None:
        ids = list(dict.fromkeys(document_ids))
        if not ids:
            raise DomainError(422, "BUNDLE_EMPTY", "Nenhum documento selecionado.")
        if len(ids) > limit:
            raise DomainError(422, "BUNDLE_TOO_LARGE", f"Máximo de {limit} documentos por arquivo neste formato.")
        found = set(s.scalars(base.where(Document.id.in_(ids))))
        if len(found) != len(ids):
            # Não informa quais existem em outra empresa/ambiente: apenas que não foram encontrados aqui.
            raise DomainError(422, "BUNDLE_DOCUMENTS_NOT_FOUND",
                              f"{len(ids) - len(found)} documento(s) não encontrado(s) nesta empresa/ambiente.")
        source = "selection"
    else:
        q = document_query(company.id, environment, filters, company.timezone)
        total = s.scalar(select(func.count()).select_from(q.subquery()))
        if total == 0:
            raise DomainError(422, "BUNDLE_EMPTY", "Nenhum documento corresponde aos filtros.")
        if total > limit:
            raise DomainError(422, "BUNDLE_TOO_LARGE",
                              f"Os filtros retornam {total} documentos; o máximo é {limit} neste formato. Refine os filtros.")
        ids = list(s.scalars(q.with_only_columns(Document.id).order_by(Document.issued_at.desc(), Document.id)))
        source = "filters"
    job = Job(company_id=company.id, environment=environment, type="BUNDLE", status="QUEUED",
              params={"format": fmt, "source": source, "filters": filters.as_dict() if filters else None,
                      "count": len(ids)},
              company_info=company.as_dict(), processed_count=0, error_count=0, fencing_token=0, attempts=0,
              created_by=user_id, created_at=now)
    s.add(job)
    s.flush()
    s.add_all([JobDocument(job_id=job.id, seq=i, document_id=d) for i, d in enumerate(ids, 1)])
    audit(s, now, "bundle.queued", company_id=company.id, actor_id=user_id, target_type="job", target_id=job.id,
          details={"format": fmt, "source": source, "count": len(ids), "environment": environment}, trace_id=trace_id)
    return job


def _folder(seq: int, number: str | None, key: str, used: set[str]) -> str:
    base = _SAFE.sub("-", f"{seq:05d}_{number or 'sem-numero'}_{key}")[:120].strip("-") or f"{seq:05d}"
    name, n = base, 1
    while name in used:
        n += 1
        name = f"{base}-{n}"
    used.add(name)
    return name


def run_bundle(sessions: sessionmaker, clock: Clock, owner: str, job_id: str, token: int, export_dir: str,
               ttl_hours: int, lease_seconds: int, max_bytes: int) -> str:
    from ..danfse import NT_VERSAO, gerar

    with sessions() as s:
        job = s.get(Job, job_id)
        fmt = (job.params or {}).get("format")
        company = CompanyInfo.from_dict(job.company_info)
        env = job.environment
        doc_ids = [r.document_id for r in s.scalars(select(JobDocument).where(JobDocument.job_id == job_id)
                                                      .order_by(JobDocument.seq))]
    Path(export_dir).mkdir(parents=True, exist_ok=True)
    path = os.path.join(export_dir, f"{job_id}.zip")  # nome interno; nunca vindo do usuário
    tmp = path + ".tmp"
    manifest: list[dict] = []
    errors: list[tuple[str, str, str]] = []
    used: set[str] = set()
    written = 0
    pdf_ok = xml_ok = 0
    pdf_marks: dict[str, str] = {}  # situação (marca d'água) de cada PDF no momento da geração
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for seq, did in enumerate(doc_ids, 1):
            with sessions() as s:
                d = s.scalar(select(Document).where(Document.id == did, Document.company_id == company.id,
                                                    Document.environment == env))
                entry = {"seq": seq, "document_id": did, "files": [], "errors": []}
                if d is None:
                    entry.update(status="ERROR")
                    entry["errors"].append({"code": "DOCUMENT_NOT_FOUND", "message": "Documento não encontrado."})
                    errors.append((did, "DOCUMENT_NOT_FOUND", "Documento não encontrado nesta empresa/ambiente."))
                    manifest.append(entry)
                    continue
                folder = _folder(seq, d.number, d.access_key, used)
                entry.update(access_key=d.access_key, number=d.number, direction=d.direction, status_nfse=d.status,
                             folder=folder)
                xml = None
                if d.xml_blob_id:
                    blob = s.get(StoredBlob, d.xml_blob_id)
                    if blob is not None and blob.company_id == company.id:
                        xml = read_blob(blob)
                marca = danfse_marca(d)
                can_pdf = danfse_available(d)
            if fmt in ("XML", "PDF_XML"):
                if xml is None:
                    entry["errors"].append({"code": "XML_UNAVAILABLE", "message": "XML original indisponível."})
                    errors.append((d.access_key, "XML_UNAVAILABLE", "XML original indisponível."))
                else:
                    name = f"{folder}/NFSe_{d.access_key}.xml"
                    zf.writestr(name, xml)
                    written += len(xml)
                    xml_ok += 1
                    entry["files"].append({"name": name, "type": "XML_ORIGINAL", "sha256": hashlib.sha256(xml).hexdigest(),
                                           "bytes": len(xml)})
            if fmt in ("PDF", "PDF_XML"):
                if not can_pdf or xml is None:
                    code = "DANFSE_SIMULATED" if d.simulated else "XML_UNAVAILABLE"
                    entry["errors"].append({"code": code, "message": "DANFSe indisponível para este documento."})
                    errors.append((d.access_key, code, "DANFSe indisponível para este documento."))
                else:
                    try:
                        pdf = gerar(xml, marca)
                    except Exception as exc:  # noqa: BLE001 — falha por documento, lote continua
                        log.warning("danfse_falhou job=%s doc=%s tipo=%s", job_id, did, type(exc).__name__)
                        entry["errors"].append({"code": "DANFSE_FAILED", "message": "Falha ao gerar o PDF desta nota."})
                        errors.append((d.access_key, "DANFSE_FAILED", "Falha ao gerar o PDF desta nota; XML preservado."))
                    else:
                        name = f"{folder}/DANFSe_{d.access_key}.pdf"
                        zf.writestr(name, pdf)
                        written += len(pdf)
                        pdf_ok += 1
                        pdf_marks[did] = marca or ""
                        entry["files"].append({"name": name, "type": "DANFSE_PDF_GERADO_PELA_PLATAFORMA",
                                               "sha256": hashlib.sha256(pdf).hexdigest(), "bytes": len(pdf),
                                               "watermark": marca})
            entry["status"] = "OK" if not entry["errors"] else ("PARTIAL" if entry["files"] else "ERROR")
            manifest.append(entry)
            if written > max_bytes:
                errors.append(("-", "BUNDLE_SIZE_LIMIT", f"Lote interrompido: tamanho acima de {max_bytes} bytes."))
                break
            if seq % 25 == 0:
                with sessions() as s, s.begin():
                    if not fenced_update(s, Job, job_id, token, owner, processed_count=seq,
                                         lease_expires_at=clock.now() + timedelta(seconds=lease_seconds)):
                        zf.close()
                        os.remove(tmp)
                        return "LOST_LEASE"
        now = clock.now()
        meta = {
            "job_id": job_id, "company_id": company.id, "environment": env, "format": fmt,
            "generated_at": now.isoformat(), "documents_requested": len(doc_ids), "documents_listed": len(manifest),
            "pdf_files": pdf_ok, "xml_files": xml_ok,
            "pdf_origin": f"DANFSe gerado pela plataforma CTRL Fiscal a partir do XML oficial ({NT_VERSAO}); "
                          "não é arquivo baixado do governo.",
            "xml_origin": "XML original da NFS-e, como recebido do Sistema Nacional (ADN/Sefin) ou importado.",
            "documents": manifest,
        }
        zf.writestr("manifesto.json", json.dumps(meta, ensure_ascii=False, indent=1))
        out = io.StringIO()
        w = csv.writer(out, delimiter=";")
        w.writerow(["seq", "document_id", "chave", "numero", "direcao", "situacao", "status_exportacao", "arquivos", "erros"])
        for e in manifest:
            w.writerow([e["seq"], e["document_id"], e.get("access_key", ""), e.get("number") or "", e.get("direction", ""),
                        e.get("status_nfse", ""), e["status"], " ".join(f["name"] for f in e["files"]),
                        " ".join(x["code"] for x in e["errors"])])
        zf.writestr("manifesto.csv", "﻿" + out.getvalue())
    files = pdf_ok + xml_ok
    status = "SUCCEEDED" if not errors else ("PARTIAL" if files else "FAILED")
    if files:
        os.replace(tmp, path)
    else:
        os.remove(tmp)
    local = now.astimezone(ZoneInfo(company.timezone)).strftime("%Y%m%d-%H%M")
    from .jobs import _add_error

    with sessions() as s, s.begin():
        job = s.scalar(select(Job).where(Job.id == job_id, Job.fencing_token == token, Job.lease_owner == owner)
                       .with_for_update())
        if job is None:
            if files:
                os.remove(path)
            return "LOST_LEASE"
        for key, code, msg in errors:
            _add_error(s, job, key, code, msg, 1000)
        job.status = status
        job.finished_at = now
        job.lease_owner = None
        job.lease_expires_at = None
        job.processed_count = len(manifest)
        job.output_path = path if files else None
        job.output_name = f"ctrl-fiscal-nfse-{fmt.lower().replace('_', '-')}-{local}.zip" if files else None
        job.output_expires_at = now + timedelta(hours=ttl_hours) if files else None
        job.result = {"documents": len(doc_ids), "pdf_files": pdf_ok, "xml_files": xml_ok, "errors": len(errors),
                      "format": fmt, "expires_at": job.output_expires_at.isoformat() if files else None,
                      "pdf_watermarks": pdf_marks}
    return status


def purge_expired(sessions: sessionmaker, clock: Clock, export_dir: str) -> int:
    """Apaga arquivos de exportação vencidos (BUNDLE e XLSX) e limpa o caminho no job."""
    now = clock.now()
    base = os.path.realpath(export_dir)
    n = 0
    with sessions() as s, s.begin():
        jobs = s.scalars(select(Job).where(Job.output_path.is_not(None), Job.output_expires_at.is_not(None),
                                           Job.output_expires_at <= now).limit(200)).all()
        for j in jobs:
            real = os.path.realpath(j.output_path)
            if real.startswith(base + os.sep) and os.path.isfile(real):
                os.remove(real)
            j.output_path = None
            j.result = {**(j.result or {}), "expired": True}
            n += 1
        s.execute(DownloadTicket.__table__.delete().where(DownloadTicket.expires_at < now - timedelta(hours=1)))
    return n


def _hash(ticket: str) -> str:
    return hashlib.sha256(ticket.encode()).hexdigest()


def issue_ticket(s: Session, job: Job, user_id: str, session_id: str, now: datetime) -> tuple[str, datetime]:
    ticket = secrets.token_urlsafe(32)
    exp = now + timedelta(seconds=TICKET_TTL_SECONDS)
    s.add(DownloadTicket(id=_hash(ticket), job_id=job.id, company_id=job.company_id, user_id=user_id,
                         session_id=session_id, expires_at=exp, created_at=now))
    return ticket, exp


def redeem_ticket(s: Session, ticket: str, company_id: str, job_id: str, now: datetime) -> DownloadTicket:
    h = _hash(ticket)
    # Consumo ATÔMICO: só um pedido consegue marcar o ticket (condição no próprio UPDATE).
    consumed = s.execute(update(DownloadTicket).where(
        DownloadTicket.id == h, DownloadTicket.company_id == company_id, DownloadTicket.job_id == job_id,
        DownloadTicket.used_at.is_(None), DownloadTicket.expires_at > now,
    ).values(used_at=now).execution_options(synchronize_session=False)).rowcount
    if consumed != 1:
        raise DomainError(403, "DOWNLOAD_TICKET_INVALID", "Ticket de download inválido, usado ou expirado.")
    return s.scalar(select(DownloadTicket).where(DownloadTicket.id == h).execution_options(populate_existing=True))


def stale_documents(s: Session, job: Job) -> int:
    """Notas do arquivo cuja situação mudou por evento oficial (cancelamento/substituição) a partir do início da
    geração. ZIP: conjunto fixo (job_documents) + marca d'água dos PDFs. XLSX: mesmos filtros, sem o de situação
    (uma nota que deixou de ser "autorizada" também torna o arquivo desatualizado)."""
    import dataclasses

    from ..models import DocumentEvent, JobDocument
    from .documents import DocumentFilters, document_query

    if job.type not in ("BUNDLE", "EXPORT") or job.started_at is None:
        return 0
    changed = select(DocumentEvent.document_id).where(
        DocumentEvent.company_id == job.company_id, DocumentEvent.environment == job.environment,
        DocumentEvent.cancels_document.is_(True), DocumentEvent.created_at >= job.started_at,
        DocumentEvent.document_id.is_not(None))
    if job.type == "BUNDLE":
        n = s.scalar(select(func.count()).select_from(JobDocument).where(
            JobDocument.job_id == job.id, JobDocument.document_id.in_(changed))) or 0
        return max(n, stale_pdf_documents(s, job))
    info = job.company_info or {}
    filters = dataclasses.replace(DocumentFilters.from_dict((job.params or {}).get("filters") or {}), status=None)
    q = document_query(job.company_id, job.environment, filters, info.get("timezone") or "America/Sao_Paulo")
    return s.scalar(select(func.count()).select_from(q.where(Document.id.in_(changed)).subquery())) or 0


def stale_pdf_documents(s: Session, job: Job) -> int:
    """Notas cujo PDF no ZIP foi gerado com outra situação (ex.: cancelada/substituída DEPOIS da geração)."""
    marks = (job.result or {}).get("pdf_watermarks") or {}
    if job.type != "BUNDLE" or not marks:
        return 0
    stale = 0
    ids = list(marks)
    for i in range(0, len(ids), 500):
        for d in s.scalars(select(Document).where(Document.id.in_(ids[i:i + 500]), Document.company_id == job.company_id)):
            if (danfse_marca(d) or "") != marks.get(d.id, ""):
                stale += 1
    return stale
