"""Sincronização ADN (distribuição) por empresa/CNPJ/ambiente a cada 12 horas.

- Agenda e cursor (NSU) persistidos; executada somente pelo worker via lease.
- Cada página é gravada (XML bruto + item + documento/evento/quarentena) e o
  cursor avança NA MESMA transação; falha de armazenamento impede o avanço.
- Falha de parse vai para quarentena durável (bruto preservado) e é
  reprocessável; o cursor nunca é zerado em atualização/reinício.
- Sem configuração: NOT_CONFIGURED (não é "nenhuma nota").
"""
from __future__ import annotations

import base64
import binascii
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ctrl_common.clock import Clock

from ..clients import CompanyInfo, NfseClient, UpstreamError
from ..models import DfeItem, Job, StoredBlob, SyncSchedule
from ..nfse_xml import Limits, XmlRejected, safe_gunzip
from .common import DomainError, audit, read_blob, store_blob
from .ingest import ingest_xml
from .leases import fenced_update, lease_free, try_acquire

log = logging.getLogger("ctrl.sync")


def ensure_schedule(s: Session, company: CompanyInfo, now: datetime, interval_hours: int,
                    environment: str | None = None) -> SyncSchedule:
    env = environment or company.environment
    sched = s.scalar(
        select(SyncSchedule).where(SyncSchedule.company_id == company.id, SyncSchedule.environment == env)
    )
    if sched is None:
        sched = SyncSchedule(
            company_id=company.id, environment=env, cnpj=company.cnpj, enabled=True, status="PENDING",
            interval_hours=interval_hours, cursor_nsu=0, next_run_at=now, processed_count=0, received_count=0,
            consecutive_failures=0, simulated=env == "mock", fencing_token=0, created_at=now, updated_at=now,
        )
        s.add(sched)
        s.flush()
    elif sched.cnpj != company.cnpj:
        # CNPJ diferente = outra agenda; nunca misturar cursores.
        raise DomainError(409, "SYNC_CNPJ_CHANGED", "CNPJ da empresa difere do cadastrado na agenda de sincronização.")
    if not sched.enabled and sched.last_error_code == "COMPANY_OR_ENVIRONMENT_INACTIVE" and not company.is_demo:
        sched.enabled = True
        sched.status = "PENDING"
        sched.last_error_code = None
        sched.last_error_message = None
        sched.updated_at = now
    if company.is_demo and env != "mock" and (sched.enabled or sched.last_error_code != "DEMO_COMPANY_OFFICIAL_BLOCKED"):
        # Empresa de demonstração: agenda oficial desativada (o worker só adquire agendas habilitadas).
        sched.enabled = False
        sched.status = "BLOCKED"
        sched.last_error_code = "DEMO_COMPANY_OFFICIAL_BLOCKED"
        sched.last_error_message = "Empresa de demonstração: nenhuma consulta oficial ao ADN é permitida."
        sched.updated_at = now
    return sched


INACTIVE_CODE = "COMPANY_OR_ENVIRONMENT_INACTIVE"


def disable_absent(s: Session, active: set[tuple[str, str]], now: datetime) -> int:
    """Desliga agendas de empresas desativadas ou de ambientes que a empresa não lê mais (o worker só adquire
    agendas habilitadas). Reativadas por ensure_schedule quando a empresa/ambiente voltar a constar."""
    n = 0
    for sched in s.scalars(select(SyncSchedule).where(SyncSchedule.enabled.is_(True))):
        if (sched.company_id, sched.environment) not in active:
            sched.enabled = False
            sched.status = "BLOCKED"
            sched.last_error_code = INACTIVE_CODE
            sched.last_error_message = "Empresa desativada ou ambiente não habilitado para leitura: agenda suspensa."
            sched.updated_at = now
            n += 1
    return n


def coverage_message(sched: SyncSchedule) -> str:
    if sched.status == "NOT_CONFIGURED":
        return ("Integração ADN não configurada (certificado/CNPJ/ambiente). Nenhuma consulta foi feita; "
                "isto NÃO significa ausência de notas recebidas.")
    if sched.status == "BLOCKED":
        return "Integração oficial bloqueada nesta etapa; nenhuma consulta ao ADN foi feita."
    base = ("Somente documentos compartilhados no ADN para o CNPJ completo da empresa, conforme direitos do certificado; "
            "histórico limitado ao disponível no ADN.")
    if sched.environment == "mock":
        return "SIMULAÇÃO LOCAL: documentos fictícios gerados pelo adaptador mock, sem validade fiscal. " + base
    return base


def status_out(s: Session, sched: SyncSchedule) -> dict:
    pending = s.scalar(
        select(func.count()).select_from(DfeItem).where(
            DfeItem.company_id == sched.company_id, DfeItem.environment == sched.environment,
            DfeItem.parse_status.in_(("QUARANTINED", "STORED")),
        )
    )
    return {
        "enabled": sched.enabled,
        "interval_hours": sched.interval_hours,
        "last_success_at": sched.last_success_at,
        "last_attempt_at": sched.last_attempt_at,
        "next_run_at": sched.next_run_at,
        "status": sched.status,
        "environment": sched.environment,
        "simulated": sched.environment == "mock",
        "processed_count": sched.processed_count,
        "received_count": sched.received_count,
        "pending_parse_count": pending,
        "coverage_message": coverage_message(sched),
        "last_error": {"code": sched.last_error_code, "message": sched.last_error_message} if sched.last_error_code else None,
        "running": sched.lease_expires_at is not None,
    }


def request_run(s: Session, sched: SyncSchedule, company: CompanyInfo, user_id: str, now: datetime, trace_id: str | None) -> Job:
    """Solicita ciclo manual sem consultar no request e sem concorrência."""
    if not sched.enabled:
        raise DomainError(409, sched.last_error_code or "SYNC_DISABLED", "Sincronização desativada para esta empresa/ambiente.")
    if sched.pending_job_id:
        existing = s.get(Job, sched.pending_job_id)
        if existing is not None and existing.status in ("QUEUED", "RUNNING"):
            return existing
    job = Job(company_id=company.id, environment=sched.environment, type="SYNC", status="QUEUED",
              params={"schedule_id": sched.id}, company_info=company.as_dict(), processed_count=0, error_count=0,
              fencing_token=0, attempts=0, created_by=user_id, created_at=now)
    s.add(job)
    s.flush()
    sched.pending_job_id = job.id
    earliest = max(now, sched.not_before or now)
    if sched.next_run_at > earliest:
        sched.next_run_at = earliest
    sched.updated_at = now
    audit(s, now, "sync.requested", company_id=company.id, actor_id=user_id, target_type="job", target_id=job.id,
          details={"earliest": earliest.isoformat()}, trace_id=trace_id)
    return job


def acquire_schedule(sessions: sessionmaker, clock: Clock, owner: str, lease_seconds: int) -> tuple[str, int] | None:
    now = clock.now()
    with sessions() as s, s.begin():
        rows = s.execute(
            select(SyncSchedule.id, SyncSchedule.fencing_token)
            .where(SyncSchedule.enabled.is_(True), SyncSchedule.next_run_at <= now, lease_free(SyncSchedule, now))
            .order_by(SyncSchedule.next_run_at, SyncSchedule.id).limit(3).with_for_update(skip_locked=True)
        ).all()
        for sid, tok in rows:
            if try_acquire(s, SyncSchedule, sid, tok, owner, now, lease_seconds):
                return sid, tok + 1
    return None


def _company_of(sched: SyncSchedule) -> CompanyInfo:
    return CompanyInfo(id=sched.company_id, name="", cnpj=sched.cnpj, timezone="UTC",
                       environment=sched.environment, municipality_code=None)


def _ingest_raw(s: Session, sched: SyncSchedule, item: DfeItem, raw: bytes, now: datetime, limits: Limits, simulated: bool) -> None:
    try:
        res = ingest_xml(s, company=_company_of(sched), environment=sched.environment, raw=raw,
                         source="ADN", simulated=simulated, now=now, limits=limits,
                         blob=s.get(StoredBlob, item.blob_id))
    except XmlRejected as exc:
        item.parse_status = "QUARANTINED"
        item.parse_error_code = exc.code
        return
    item.access_key = res.access_key
    item.doc_kind = res.kind
    item.company_role = res.company_role
    item.document_id = res.document_id
    item.event_row_id = res.event_row_id
    item.parse_error_code = res.out_of_scope_code
    item.parse_status = "OUT_OF_SCOPE" if res.outcome == "OUT_OF_SCOPE" else "PARSED"


def _persist_page(
    sessions: sessionmaker, clock: Clock, owner: str, sched_id: str, token: int, page: dict, limits: Limits
) -> tuple[int, int] | None:
    """Grava página inteira e avança cursor atomicamente. None = lease perdido."""
    now = clock.now()
    new_items = 0
    parsed = 0
    with sessions() as s, s.begin():
        sched = s.scalar(
            select(SyncSchedule).where(SyncSchedule.id == sched_id, SyncSchedule.fencing_token == token,
                                       SyncSchedule.lease_owner == owner).with_for_update()
        )
        if sched is None:
            return None
        simulated = bool(page.get("simulated"))
        cursor = sched.cursor_nsu
        for entry in page.get("items", []):
            nsu = int(entry["nsu"])
            cursor = max(cursor, nsu)
            if s.scalar(select(DfeItem.id).where(DfeItem.company_id == sched.company_id,
                                                 DfeItem.environment == sched.environment, DfeItem.nsu == nsu)):
                continue  # NSU já gravado: replay não duplica
            payload = entry.get("xml_gzip_b64") or ""
            try:
                raw = safe_gunzip(base64.b64decode(payload, validate=True), limits.max_gzip_bytes)
                decode_error = None
            except (binascii.Error, ValueError, OSError, EOFError, XmlRejected) as exc:
                raw = payload.encode()
                decode_error = getattr(exc, "code", "PAYLOAD_DECODE_FAILED")
            blob = store_blob(s, sched.company_id, raw, "XML", now)
            item = DfeItem(company_id=sched.company_id, environment=sched.environment, nsu=nsu,
                           access_key=entry.get("access_key"), doc_kind=entry.get("doc_kind"), blob_id=blob.id,
                           parse_status="STORED", parse_attempts=1, simulated=simulated, received_at=now, updated_at=now)
            s.add(item)
            s.flush()
            new_items += 1
            if decode_error:
                item.parse_status = "QUARANTINED"
                item.parse_error_code = decode_error
            else:
                _ingest_raw(s, sched, item, raw, now, limits, simulated)
                if item.parse_status == "PARSED":
                    parsed += 1
        last_nsu = int(page.get("last_nsu") or cursor)
        sched.cursor_nsu = max(sched.cursor_nsu, cursor, last_nsu)
        sched.received_count += new_items
        sched.processed_count += parsed
        sched.lease_expires_at = now + timedelta(seconds=120)
        sched.updated_at = now
    return new_items, parsed


def reprocess_quarantine(sessions: sessionmaker, clock: Clock, sched_id: str, limits: Limits, max_attempts: int = 3) -> int:
    fixed = 0
    with sessions() as s, s.begin():
        sched = s.get(SyncSchedule, sched_id)
        rows = s.scalars(select(DfeItem).where(
            DfeItem.company_id == sched.company_id, DfeItem.environment == sched.environment,
            DfeItem.parse_status == "QUARANTINED", DfeItem.parse_attempts < max_attempts,
        ).with_for_update()).all()
        for item in rows:
            item.parse_attempts += 1
            item.updated_at = clock.now()
            raw = read_blob(s.get(StoredBlob, item.blob_id))
            _ingest_raw(s, sched, item, raw, clock.now(), limits, item.simulated)
            if item.parse_status == "PARSED":
                fixed += 1
    return fixed


def _finish_job(s: Session, sched: SyncSchedule, status: str, now: datetime, result: dict, error: tuple[str, str] | None) -> None:
    if not sched.pending_job_id:
        return
    job = s.get(Job, sched.pending_job_id)
    if (job is not None and job.status in ("QUEUED", "RUNNING") and sched.cycle_started_at is not None
            and job.created_at > sched.cycle_started_at):
        # Pedido feito DURANTE um ciclo já iniciado: este ciclo não comprova o job. Ele aguarda um ciclo próprio,
        # o próximo permitido (respeita a espera de 1 h após fila vazia no ADN).
        earliest = max(now, sched.not_before or now)
        job.status = "QUEUED"
        job.started_at = None
        job.result = {"waiting_until": earliest.isoformat(), "reason": "solicitado durante ciclo em andamento"}
        sched.next_run_at = min(sched.next_run_at, earliest)
        return
    if job is not None and job.status in ("QUEUED", "RUNNING"):
        from ..models import JobError

        job.status = status
        job.finished_at = now
        job.started_at = job.started_at or sched.cycle_started_at
        job.processed_count = sched.processed_count
        job.result = result
        if error:
            job.error_count += 1
            s.add(JobError(job_id=job.id, seq=job.error_count, file=None, code=error[0], message=error[1][:500]))
    sched.pending_job_id = None


def run_cycle(
    sessions: sessionmaker, nfse: NfseClient, clock: Clock, owner: str, sched_id: str, token: int, *,
    limits: Limits, max_pages: int, config_recheck_seconds: int, empty_cooldown_seconds: int,
    error_backoff_seconds: int,
) -> str:
    started = clock.now()
    with sessions() as s, s.begin():
        sched = s.scalar(select(SyncSchedule).where(SyncSchedule.id == sched_id, SyncSchedule.fencing_token == token,
                                                    SyncSchedule.lease_owner == owner).with_for_update())
        if sched is None:
            return "LOST_LEASE"
        if sched.status != "DRAINING":
            # Novo ciclo: contadores e início do ciclo reiniciam; continuação de drenagem os preserva.
            sched.cycle_started_at = started
            sched.processed_count = 0
            sched.received_count = 0
        else:
            started = sched.cycle_started_at or started
        sched.status = "RUNNING"
        sched.last_attempt_at = clock.now()
        sched.updated_at = clock.now()
        if sched.pending_job_id:
            job = s.get(Job, sched.pending_job_id)
            if job is not None and job.status == "QUEUED":
                job.status = "RUNNING"
                job.started_at = started
        company_id, environment, cnpj, cursor = sched.company_id, sched.environment, sched.cnpj, sched.cursor_nsu
    reprocess_quarantine(sessions, clock, sched_id, limits)

    def end(status: str, *, next_run: datetime, success: bool, error: tuple[str, str] | None = None,
            not_before: datetime | None = None) -> str:
        now = clock.now()
        with sessions() as s, s.begin():
            sched = s.scalar(select(SyncSchedule).where(SyncSchedule.id == sched_id, SyncSchedule.fencing_token == token,
                                                        SyncSchedule.lease_owner == owner).with_for_update())
            if sched is None:
                return "LOST_LEASE"
            sched.status = status
            sched.next_run_at = next_run
            sched.lease_owner = None
            sched.lease_expires_at = None
            sched.updated_at = now
            if not_before is not None:
                sched.not_before = not_before
            if success:
                sched.last_success_at = now
                sched.consecutive_failures = 0
                sched.last_error_code = None
                sched.last_error_message = None
            elif error:
                sched.last_error_code, sched.last_error_message = error[0], error[1][:500]
                if status == "ERROR":
                    sched.consecutive_failures += 1
            if status != "DRAINING":  # ciclo ainda drenando: job manual continua RUNNING
                job_status = "SUCCEEDED" if success else "FAILED"
                _finish_job(s, sched, job_status, now, {"received": sched.received_count,
                                                         "processed": sched.processed_count, "status": status},
                            error if not success else None)
            audit(s, now, "sync.cycle_finished", company_id=sched.company_id, actor_type="worker", actor_id=owner,
                  target_type="sync_schedule", target_id=sched.id,
                  details={"status": status, "received": sched.received_count, "cursor_nsu": sched.cursor_nsu})
        return status

    pages = 0
    while pages < max_pages:
        try:
            page = nfse.distribution_page(company_id, environment, cnpj, cursor)
        except UpstreamError as exc:
            with sessions() as s:
                fails = s.get(SyncSchedule, sched_id).consecutive_failures
            delay = min(12 * 3600, error_backoff_seconds * 2 ** min(fails, 6))
            return end("ERROR", next_run=clock.now() + timedelta(seconds=delay), success=False,
                       error=(exc.code, "Falha ao consultar a distribuição; nova tentativa agendada."))
        status = page.get("status")
        if status in ("NOT_CONFIGURED", "BLOCKED"):
            err = page.get("error") or {}
            return end(status, next_run=clock.now() + timedelta(seconds=config_recheck_seconds), success=False,
                       error=(err.get("code", status), err.get("message", "Integração não configurada.")))
        if status != "OK":
            err = page.get("error") or {}
            retry = page.get("retry_after_seconds") or error_backoff_seconds
            return end("ERROR", next_run=clock.now() + timedelta(seconds=retry), success=False,
                       error=(err.get("code", "DISTRIBUTION_ERROR"), err.get("message", "Erro na distribuição.")))
        try:
            persisted = _persist_page(sessions, clock, owner, sched_id, token, page, limits)
        except Exception as exc:  # falha de armazenamento: cursor NÃO avança
            log.error("falha ao persistir página de distribuição tipo=%s", type(exc).__name__)
            return end("ERROR", next_run=clock.now() + timedelta(seconds=error_backoff_seconds), success=False,
                       error=("STORAGE_FAILED", "Falha ao gravar documentos; cursor preservado."))
        if persisted is None:
            return "LOST_LEASE"
        with sessions() as s:
            cursor = s.get(SyncSchedule, sched_id).cursor_nsu
        pages += 1
        if not page.get("has_more"):
            now = clock.now()
            cooldown = max(empty_cooldown_seconds, int(page.get("retry_after_seconds") or 0))
            with sessions() as s:
                interval = s.get(SyncSchedule, sched_id).interval_hours
            return end("OK", next_run=max(started + timedelta(hours=interval), now + timedelta(seconds=cooldown)),
                       success=True, not_before=now + timedelta(seconds=cooldown))
    # Ainda há páginas: continua drenando em seguida (ciclo pode ter várias chamadas).
    return end("DRAINING", next_run=clock.now() + timedelta(seconds=30), success=False)
