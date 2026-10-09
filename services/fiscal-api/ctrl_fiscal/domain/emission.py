"""Processamento das tarefas de transmissão (executado pelo fiscal-worker).

Garantias:
- lease com expiração + fencing: commit local só com o token vigente;
- identidade DPS (série/número) reservada transacionalmente e reutilizada;
- antes de reenviar após falha/queda, consulta o resultado (reconciliação);
- timeout => AWAITING_CONFIRMATION (nunca REJECTED);
- não há promessa de exactly-once externo: deduplicação interna por
  submission_id/DPS no conector + reconciliação;
- antes de CADA envio, a autorização atual de quem agendou é conferida no core (ativo, vínculo, permissão "emit").
  Revogada sem nada transmitido => item retirado (WITHDRAWN); revogada com envio possível => só reconsulta, nunca
  reenvia; core indisponível => adia sem transmitir.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ctrl_common.clock import Clock

from ..clients import NfseClient, UpstreamError
from ..models import Batch, BatchItem, Document, DpsSequence, Draft
from . import retencoes, retencoes_doc
from .common import audit
from .leases import fenced_update, lease_free, try_acquire

log = logging.getLogger("ctrl.emission")

ACTIVE = ("SCHEDULED", "PROCESSING", "AWAITING_CONFIRMATION")
MOCK_MUNICIPALITY = "0000000"  # somente ambiente mock (simulação local)


@dataclass
class LeasedItem:
    id: str
    token: int
    submission_id: str
    submit_phase: str
    environment: str
    company_id: str
    draft_id: str
    dps_id: str | None
    dps_series: str | None
    dps_number: int | None
    snapshot: dict
    snapshot_hash: str
    attempts: int
    requested_by: str | None = None


# Autorizador: (item) -> True (pode transmitir) | False (revogado) | None (indisponível: adiar sem transmitir)
Authorizer = Callable[[LeasedItem], "bool | None"]
EMIT_PERMISSION = "emit"


def core_authorizer(core) -> Authorizer:  # noqa: ANN001
    def check(it: LeasedItem) -> bool | None:
        if not it.requested_by:
            return False
        try:
            perms = core.job_permissions(it.requested_by, it.company_id)
        except UpstreamError:
            return None
        return perms is not None and EMIT_PERMISSION in perms
    return check


def build_dps_id(municipality: str, cnpj: str, series: str, number: int) -> str:
    """Id DPS: "DPS"+cMun(7)+tpInsc(1)+inscrição(14)+série(5)+nDPS(15) (XSD TSIdDPS).

    O preenchimento com zeros de série/número no Id decorre dos tamanhos fixos
    do padrão oficial; não há instrução textual explícita (XSD TSIdDPS em nfse_api/xsd).
    """
    return "DPS" + municipality.zfill(7) + "2" + cnpj.zfill(14) + series.zfill(5) + str(number).zfill(15)


def _reserve_dps(s: Session, item: BatchItem, series: str) -> None:
    seq = s.scalar(
        select(DpsSequence).where(
            DpsSequence.company_id == item.company_id, DpsSequence.environment == item.environment, DpsSequence.series == series
        ).with_for_update()
    )
    if seq is None:
        try:
            with s.begin_nested():
                seq = DpsSequence(company_id=item.company_id, environment=item.environment, series=series, next_number=1)
                s.add(seq)
                s.flush()
        except IntegrityError:
            seq = s.scalar(
                select(DpsSequence).where(
                    DpsSequence.company_id == item.company_id, DpsSequence.environment == item.environment,
                    DpsSequence.series == series,
                ).with_for_update()
            )
    number = seq.next_number
    seq.next_number = number + 1
    company = item.snapshot["company"]
    municipality = company.get("municipality_code")
    if not municipality and item.environment == "mock":
        municipality = MOCK_MUNICIPALITY
    item.dps_series = series
    item.dps_number = number
    item.dps_id = build_dps_id(municipality, company["cnpj"], series, number) if municipality else None


def acquire_items(
    sessions: sessionmaker, clock: Clock, owner: str, *, limit: int, lease_seconds: int, max_in_flight: int, series: str,
) -> list[LeasedItem]:
    now = clock.now()
    leased: list[LeasedItem] = []
    with sessions() as s, s.begin():
        in_flight = s.scalar(
            select(func.count()).select_from(BatchItem).where(
                BatchItem.status.in_(ACTIVE), BatchItem.lease_expires_at.is_not(None), BatchItem.lease_expires_at > now
            )
        )
        capacity = min(limit, max(0, max_in_flight - in_flight))
        if capacity == 0:
            return []
        candidates = s.execute(
            select(BatchItem.id, BatchItem.fencing_token)
            .where(BatchItem.status.in_(ACTIVE), BatchItem.next_attempt_at <= now, lease_free(BatchItem, now))
            .order_by(BatchItem.next_attempt_at, BatchItem.id)
            .limit(capacity)
            .with_for_update(skip_locked=True)
        ).all()
        for item_id, token in candidates:
            if not try_acquire(s, BatchItem, item_id, token, owner, now, lease_seconds):
                continue
            item = s.get(BatchItem, item_id, populate_existing=True)
            item.attempts += 1
            item.updated_at = now
            if item.status == "SCHEDULED":
                item.status = "PROCESSING"
                if item.started_at is None:
                    item.started_at = now
                    # Atraso registrado explicitamente (ex.: worker parado); a data de
                    # emissão será a da transmissão real, nunca "reescrita" sem registro.
                    item.late_seconds = max(0, int((now - item.scheduled_at).total_seconds()))
                d = s.get(Draft, item.draft_id)
                if d is not None and d.current_item_id == item.id:
                    d.status = "PROCESSING"
                    d.updated_at = now
            if item.dps_number is None:
                # Série DO WORKSPACE congelada no lote (snapshot da empresa); a global só vale para lotes antigos.
                _reserve_dps(s, item, (item.snapshot.get("company") or {}).get("dps_series") or series)
            requested_by = s.scalar(select(Batch.created_by).where(Batch.id == item.batch_id))
            leased.append(
                LeasedItem(
                    id=item.id, token=token + 1, submission_id=item.submission_id, submit_phase=item.submit_phase,
                    environment=item.environment, company_id=item.company_id, draft_id=item.draft_id,
                    dps_id=item.dps_id, dps_series=item.dps_series, dps_number=item.dps_number,
                    snapshot=item.snapshot, snapshot_hash=item.snapshot_hash, attempts=item.attempts,
                    requested_by=requested_by,
                )
            )
    return leased


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(3600, 30 * 2 ** min(max(attempts - 1, 0), 7)))


def submission_payload(it: LeasedItem) -> dict:
    return {
        "submission_id": it.submission_id,
        "company_id": it.company_id,
        "environment": it.environment,
        "company_cnpj": it.snapshot["company"]["cnpj"],
        "dps": {"id": it.dps_id, "series": it.dps_series, "number": it.dps_number},
        "payload": it.snapshot,
        "payload_hash": it.snapshot_hash,
    }


def process_item(
    sessions: sessionmaker, nfse: NfseClient, clock: Clock, owner: str, it: LeasedItem,
    after_external: Callable[[LeasedItem, dict | None], None] | None = None,
    authorize: Authorizer | None = None,
) -> str:
    """Executa uma tentativa. Retorna o estado final local aplicado (ou LOST_LEASE)."""
    company = (it.snapshot or {}).get("company") or {}
    if company.get("is_demo") and it.environment != "mock":
        # Empresa de demonstração: bloqueio local, o conector não é chamado (nenhuma conexão oficial).
        return _finish(sessions, clock, owner, it, {"state": "BLOCKED", "error": {
            "code": "DEMO_COMPANY_OFFICIAL_BLOCKED", "message": "Empresa de demonstração não emite em ambiente oficial."}})
    if it.dps_id is None:
        return _finish(sessions, clock, owner, it, {"state": "BLOCKED", "error": {
            "code": "MUNICIPALITY_NOT_CONFIGURED", "message": "Município da empresa não configurado para compor o Id DPS."}})
    resp: dict | None = None
    try:
        if it.submit_phase == "SUBMITTING":
            # Sucessor (ou nova tentativa): consulta antes de qualquer reenvio.
            resp = nfse.get_submission(it.submission_id, reconcile=True)
        if resp is None or resp.get("state") in ("RECEIVED", "BLOCKED", "NOT_FOUND"):
            allowed = authorize(it) if authorize is not None else True
            if allowed is not True:
                if allowed is None:
                    return _finish(sessions, clock, owner, it, {"state": "DEFER", "error": {
                        "code": "AUTHZ_UNAVAILABLE", "message": "Autorização não conferida (core indisponível); nada transmitido."}})
                return _finish(sessions, clock, owner, it, {"state": "AUTH_REVOKED", "error": {
                    "code": "AUTHORIZATION_REVOKED",
                    "message": "Quem agendou não tem mais permissão de emissão nesta empresa; nada foi transmitido."}})
            if it.submit_phase != "SUBMITTING":
                with sessions() as s, s.begin():
                    if not fenced_update(s, BatchItem, it.id, it.token, owner, submit_phase="SUBMITTING"):
                        return "LOST_LEASE"
                it.submit_phase = "SUBMITTING"
            resp = nfse.submit(submission_payload(it))
    except UpstreamError as exc:
        if exc.status == 409:
            resp = {"state": "CONFLICT", "error": {"code": exc.code, "message": "Conflito de identidade/conteúdo no conector."}}
        else:
            resp = {"state": "TRANSPORT_ERROR", "error": {"code": exc.code, "message": "Falha de comunicação com o conector."}}
    if after_external is not None:
        after_external(it, resp)  # ganchos de teste (ex.: simular queda antes do commit)
    return _finish(sessions, clock, owner, it, resp or {"state": "TRANSPORT_ERROR"})


def _finish(sessions: sessionmaker, clock: Clock, owner: str, it: LeasedItem, resp: dict) -> str:
    now = clock.now()
    state = resp.get("state")
    with sessions() as s, s.begin():
        item = s.scalar(
            select(BatchItem).where(BatchItem.id == it.id, BatchItem.fencing_token == it.token, BatchItem.lease_owner == owner)
            .with_for_update()
        )
        if item is None:
            return "LOST_LEASE"
        draft = s.scalar(select(Draft).where(Draft.id == item.draft_id).with_for_update())
        own_draft = draft is not None and draft.current_item_id == item.id
        common = {"lease_owner": None, "lease_expires_at": None, "updated_at": now}
        if state == "AUTHORIZED":
            result = resp.get("result") or {}
            doc_id = _create_issued_document(s, item, result, bool(resp.get("simulated")), now,
                                             resp.get("nfse_xml_gzip_b64"))
            _apply(item, status="AUTHORIZED", finished_at=now, document_id=doc_id, last_error_code=None,
                   last_error_message=None, result={
                       "number": result.get("number"), "access_key": result.get("access_key"),
                       "authorized_at": result.get("authorized_at"), "simulated": bool(resp.get("simulated")),
                       "label": "SIMULADA — sem validade fiscal" if resp.get("simulated") else None,
                       "certificate_fingerprint": resp.get("certificate_fingerprint"),
                   }, **common)
            if own_draft:
                draft.status = "AUTHORIZED"
            audit(s, now, "emission.authorized", company_id=item.company_id, actor_type="worker", actor_id=owner,
                  target_type="batch_item", target_id=item.id,
                  details={"simulated": bool(resp.get("simulated")), "environment": item.environment,
                           "batch_id": item.batch_id, "scheduled_by": it.requested_by, "outcome": "AUTHORIZED"})
            final = "AUTHORIZED"
        elif state == "REJECTED":
            errs = [{"code": str(e.get("code"))[:60], "message": str(e.get("message"))[:300]} for e in (resp.get("errors") or [])][:20]
            _apply(item, status="REJECTED", finished_at=now, result={"errors": errs, "simulated": bool(resp.get("simulated"))},
                   last_error_code="REJECTED", last_error_message="Rejeição confirmada; corrija o rascunho.", **common)
            if own_draft:
                draft.status = "REJECTED"
            audit(s, now, "emission.rejected", company_id=item.company_id, actor_type="worker", actor_id=owner,
                  target_type="batch_item", target_id=item.id,
                  details={"environment": item.environment, "batch_id": item.batch_id, "scheduled_by": it.requested_by,
                           "outcome": "REJECTED"})
            final = "REJECTED"
        elif state == "DEFER" or (state == "AUTH_REVOKED" and item.submit_phase == "SUBMITTING"):
            # Nada transmitido nesta tentativa. Com envio anterior possível (SUBMITTING), só reconsulta depois.
            err = resp["error"]
            status = "AWAITING_CONFIRMATION" if item.submit_phase == "SUBMITTING" else "SCHEDULED"
            _apply(item, status=status, next_attempt_at=now + _backoff(item.attempts), last_error_code=err["code"],
                   last_error_message=err["message"], **common)
            if own_draft:
                draft.status = status
            final = status
        elif state == "AUTH_REVOKED":
            # Fase NONE: nunca transmitido. Retira o item; o rascunho volta a DRAFT.
            err = resp["error"]
            _apply(item, status="WITHDRAWN", finished_at=now, last_error_code=err["code"],
                   last_error_message=err["message"], **common)
            item.fencing_token += 1
            if own_draft:
                draft.status = "DRAFT"
                draft.version += 1
                draft.current_item_id = None
            audit(s, now, "emission.withdrawn_authorization_revoked", company_id=item.company_id, actor_type="worker",
                  actor_id=owner, target_type="batch_item", target_id=item.id, details={"requested_by": it.requested_by})
            final = "WITHDRAWN"
        elif state in ("BLOCKED", "NOT_CONFIGURED"):
            # O conector garante que BLOCKED/NOT_CONFIGURED nunca foi transmitido.
            err = resp.get("error") or {"code": state, "message": "Integração não configurada."}
            _apply(item, status="SCHEDULED", submit_phase="NONE",
                   next_attempt_at=now + timedelta(hours=1), last_error_code=str(err.get("code"))[:60],
                   last_error_message=str(err.get("message"))[:500], **common)
            if own_draft:
                draft.status = item.status
            final = item.status
        else:
            # UNCERTAIN / SENDING / IN_PROGRESS / TRANSPORT_ERROR / CONFLICT / NOT_FOUND / RECEIVED
            err = resp.get("error") or {"code": state or "UNCERTAIN", "message": "Resultado ainda não confirmado; será reconciliado."}
            _apply(item, status="AWAITING_CONFIRMATION", next_attempt_at=now + _backoff(item.attempts),
                   last_error_code=str(err.get("code"))[:60], last_error_message=str(err.get("message"))[:500], **common)
            if own_draft:
                draft.status = "AWAITING_CONFIRMATION"
            final = "AWAITING_CONFIRMATION"
        if own_draft:
            draft.updated_at = now
        return final


def _apply(item: BatchItem, **values) -> None:  # noqa: ANN003
    for k, v in values.items():
        setattr(item, k, v)


def _dec(v: str | None) -> Decimal | None:
    return None if v is None else Decimal(v)


def _create_issued_document(s: Session, item: BatchItem, result: dict, simulated: bool, now: datetime,
                            nfse_xml_gzip_b64: str | None = None) -> str:
    if nfse_xml_gzip_b64 and not simulated:
        # XML oficial devolvido pela Sefin: documento criado a partir dele (fonte de verdade).
        import base64
        import gzip

        from ..clients import CompanyInfo
        from ..nfse_xml import Limits
        from .ingest import ingest_xml

        from ..nfse_xml import safe_gunzip

        raw = safe_gunzip(base64.b64decode(nfse_xml_gzip_b64), 5 * 1024 * 1024)
        res = ingest_xml(s, company=CompanyInfo.from_dict(item.snapshot["company"]), environment=item.environment,
                         raw=raw, source="EMISSION", simulated=False, now=now, limits=Limits())
        doc = s.get(Document, res.document_id) if res.document_id else None
        if doc is not None:
            doc.draft_id = item.draft_id
            doc.customer_id = (item.snapshot.get("customer") or {}).get("id")
            doc.dps_id = doc.dps_id or item.dps_id
            doc.tax_settings = item.snapshot.get("tax_settings")
            return doc.id
    key = result.get("access_key")
    existing = s.scalar(
        select(Document).where(
            Document.company_id == item.company_id, Document.environment == item.environment, Document.access_key == key
        )
    )
    if existing is not None:
        return existing.id
    snap = item.snapshot
    tax = snap.get("tax_settings") or {}
    customer = snap.get("customer") or {}
    company = snap.get("company") or {}
    authorized_at = result.get("authorized_at")
    ret_sum = snap.get("retention_summary") or retencoes.resumo(
        snap.get("amount"), snap.get("unconditional_discount"), snap.get("conditional_discount"),
        snap.get("deductions"), tax)
    doc = Document(
        company_id=item.company_id, environment=item.environment, direction="ISSUED", company_role="PRESTADOR",
        access_key=key, number=result.get("number"), dps_id=item.dps_id, status="AUTHORIZED",
        competence=snap.get("competence"),
        issued_at=datetime.fromisoformat(authorized_at) if authorized_at else now,
        processed_at=datetime.fromisoformat(authorized_at) if authorized_at else now,
        issuer_doc=company.get("cnpj"), issuer_name=company.get("name"),
        recipient_doc=customer.get("document"), recipient_name=customer.get("name"),
        description=(snap.get("service") or {}).get("description"),
        service_national_code=(snap.get("service") or {}).get("national_code"),
        service_municipal_code=(snap.get("service") or {}).get("municipal_code"),
        amount=_dec(snap.get("amount")), deductions=_dec(snap.get("deductions")),
        ret_irrf=_dec(tax.get("irrf_withheld")), ret_inss=_dec(tax.get("inss_withheld")),
        ret_csll=_dec(tax.get("csll_withheld")),
        ret_iss=_dec(ret_sum["iss"]["amount"]),
        total_retained=_dec(ret_sum["total_withheld"]) if ret_sum["complete"] else None,
        net_amount=_dec(ret_sum["net_amount"]) if ret_sum["complete"] else None,
        retention_indicators={"iss": {"status": tax.get("iss_retention") or "NOT_INFORMED", "source": "rascunho"},
                              "pis_cofins_retention_code": tax.get("pis_cofins_retention")},
        tax_settings=tax, review_status=None, version=1, xml_blob_id=None, source="EMISSION",
        simulated=simulated, customer_id=customer.get("id"), draft_id=item.draft_id, created_at=now, updated_at=now,
    )
    retencoes_doc.apply(doc)
    s.add(doc)
    s.flush()
    return doc.id
