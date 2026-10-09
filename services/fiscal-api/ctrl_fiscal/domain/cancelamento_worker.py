"""Processamento dos pedidos de cancelamento/análise fiscal (fiscal-worker).

Garantias (mesmas da emissão):
- lease + fencing token; commit local só com o token vigente;
- antes de CADA envio, confere no core a autorização ATUAL de quem pediu (ativo, vínculo, permissão "cancel");
  revogada sem envio => WITHDRAWN; revogada com envio possível => só reconsulta, nunca reenvia;
- reconfere a nota (ainda autorizada, mesma empresa/ambiente) antes do primeiro envio;
- com envio possível (fase SUBMITTING), consulta o conector (que consulta a Sefin) ANTES de reenviar;
- timeout/erro => AWAITING_CONFIRMATION (nunca REJECTED); REJECTED só com recusa oficial confirmada;
- REGISTERED aplica o XML do evento oficial pelo MESMO caminho da ingestão do ADN (document_events), e só então
  a nota muda de situação. Se a gravação local falhar depois do registro oficial, o pedido continua em
  SUBMITTING e a próxima tentativa obtém o evento por consulta e reaplica (idempotente por event_id).
"""
from __future__ import annotations

import base64
import gzip
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from ctrl_common.clock import Clock

from ..clients import CompanyInfo, NfseClient, UpstreamError
from ..models import CancellationRequest, Document
from ..nfse_xml import Limits, XmlRejected
from .common import audit
from .ingest import ingest_xml
from .leases import fenced_update, lease_free, try_acquire

log = logging.getLogger("ctrl.cancellation")
ACTIVE = ("QUEUED", "PROCESSING", "AWAITING_CONFIRMATION")
CANCEL_PERMISSION = "cancel"


@dataclass
class LeasedCancellation:
    id: str
    token: int
    company_id: str
    environment: str
    document_id: str
    access_key: str
    company_cnpj: str
    event_code: str
    reason_code: str
    reason_text: str
    submit_phase: str
    requested_by: str
    attempts: int


Authorizer = Callable[[LeasedCancellation], "bool | None"]


def core_authorizer(core) -> Authorizer:  # noqa: ANN001
    def check(it: LeasedCancellation) -> bool | None:
        try:
            perms = core.job_permissions(it.requested_by, it.company_id)
        except UpstreamError:
            return None
        return perms is not None and CANCEL_PERMISSION in perms
    return check


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(3600, 30 * 2 ** min(max(attempts - 1, 0), 7)))


def acquire(sessions: sessionmaker, clock: Clock, owner: str, *, limit: int, lease_seconds: int) -> list[LeasedCancellation]:
    now = clock.now()
    out: list[LeasedCancellation] = []
    with sessions() as s, s.begin():
        rows = s.execute(select(CancellationRequest.id, CancellationRequest.fencing_token).where(
            CancellationRequest.status.in_(ACTIVE), CancellationRequest.next_attempt_at <= now,
            lease_free(CancellationRequest, now)).order_by(CancellationRequest.next_attempt_at).limit(limit)
            .with_for_update(skip_locked=True)).all()
        for rid, token in rows:
            if not try_acquire(s, CancellationRequest, rid, token, owner, now, lease_seconds):
                continue
            r = s.get(CancellationRequest, rid, populate_existing=True)
            r.attempts += 1
            r.status = "PROCESSING" if r.status == "QUEUED" else r.status
            r.updated_at = now
            out.append(LeasedCancellation(
                id=r.id, token=token + 1, company_id=r.company_id, environment=r.environment, document_id=r.document_id,
                access_key=r.access_key, company_cnpj=r.company_cnpj, event_code=r.event_code,
                reason_code=r.reason_code, reason_text=r.reason_text, submit_phase=r.submit_phase,
                requested_by=r.requested_by, attempts=r.attempts))
    return out


def _payload(it: LeasedCancellation) -> dict:
    return {"request_id": it.id, "company_id": it.company_id, "environment": it.environment,
            "company_cnpj": it.company_cnpj, "access_key": it.access_key, "event_code": it.event_code,
            "c_motivo": it.reason_code, "x_motivo": it.reason_text}


def process(sessions: sessionmaker, nfse: NfseClient, clock: Clock, owner: str, it: LeasedCancellation,
            company: CompanyInfo | None, authorize: Authorizer, limits: Limits) -> str:
    resp: dict | None = None
    try:
        if it.submit_phase == "SUBMITTING":
            resp = nfse.get_event(it.id, reconcile=True)  # consulta antes de qualquer reenvio
        if resp is None or resp.get("state") in ("RECEIVED", "NOT_FOUND"):
            allowed = authorize(it)
            if allowed is None:
                return _finish(sessions, clock, owner, it, company, {"state": "DEFER", "error": {
                    "code": "AUTHZ_UNAVAILABLE", "message": "Autorização não conferida (core indisponível); nada enviado."}},
                    limits)
            if allowed is False:
                return _finish(sessions, clock, owner, it, company, {"state": "AUTH_REVOKED", "error": {
                    "code": "AUTHORIZATION_REVOKED",
                    "message": "Quem pediu não tem mais a permissão de cancelamento nesta empresa; nada foi enviado."}},
                    limits)
            if it.submit_phase != "SUBMITTING":
                with sessions() as s, s.begin():
                    doc = s.get(Document, it.document_id)
                    if doc is None or doc.company_id != it.company_id or doc.environment != it.environment \
                            or doc.access_key != it.access_key:
                        return _finish(sessions, clock, owner, it, company, {"state": "LOCAL_BLOCK", "error": {
                            "code": "DOCUMENT_MISMATCH", "message": "Nota não corresponde ao pedido."}}, limits)
                    if doc.status != "AUTHORIZED":
                        return _finish(sessions, clock, owner, it, company, {"state": "LOCAL_BLOCK", "error": {
                            "code": "ALREADY_CANCELLED", "message": "A nota já não está autorizada (evento recebido)."}},
                            limits)
                    if not fenced_update(s, CancellationRequest, it.id, it.token, owner, submit_phase="SUBMITTING"):
                        return "LOST_LEASE"
                it.submit_phase = "SUBMITTING"
            resp = nfse.register_event(_payload(it))
    except UpstreamError as exc:
        if exc.status == 409:
            resp = {"state": "CONFLICT", "error": {"code": exc.code, "message": "Conflito no conector (pedido ativo ou "
                                                                               "evento já registrado)."}}
        else:
            resp = {"state": "TRANSPORT_ERROR", "error": {"code": exc.code, "message": "Falha de comunicação com o conector."}}
    return _finish(sessions, clock, owner, it, company, resp or {"state": "TRANSPORT_ERROR"}, limits)


def _finish(sessions: sessionmaker, clock: Clock, owner: str, it: LeasedCancellation, company: CompanyInfo | None,
            resp: dict, limits: Limits) -> str:
    now = clock.now()
    state = resp.get("state")
    with sessions() as s, s.begin():
        r = s.scalar(select(CancellationRequest).where(
            CancellationRequest.id == it.id, CancellationRequest.fencing_token == it.token,
            CancellationRequest.lease_owner == owner).with_for_update())
        if r is None:
            return "LOST_LEASE"
        common = {"lease_owner": None, "lease_expires_at": None, "updated_at": now}

        def final(status: str, code: str | None, message: str | None, **extra) -> str:  # noqa: ANN003
            for k, v in {**common, "status": status, "finished_at": now, "active_document_id": None,
                         "last_error_code": code, "last_error_message": message, **extra}.items():
                setattr(r, k, v)
            audit(s, now, f"cancellation.{status.lower()}", company_id=r.company_id, actor_type="worker",
                  actor_id=owner, target_type="document", target_id=r.document_id, trace_id=r.trace_id,
                  details={"request_id": r.id, "environment": r.environment, "event_code": r.event_code,
                           "requested_by": r.requested_by, "outcome": status, "code": code,
                           "simulated": bool(resp.get("simulated", r.simulated))})
            return status

        if state == "REGISTERED":
            raw = resp.get("event_xml_gzip_b64")
            if not raw or company is None:
                # Sem o XML oficial não há como aplicar: mantém aguardando (reconsulta trará o XML).
                return _await(r, now, "EVENT_XML_PENDING", "Evento registrado; aguardando XML para aplicar.", common)
            xml = gzip.decompress(base64.b64decode(raw))
            try:
                res = ingest_xml(s, company=company, environment=r.environment, raw=xml, source="EVENT_REQ",
                                 simulated=bool(resp.get("simulated")), now=now, limits=limits)
            except XmlRejected as exc:
                return _await(r, now, "EVENT_XML_REJECTED", f"XML do evento não aplicado: {exc.code}", common)
            extra = {}
            resultado = {**(resp.get("result") or {}), "applied": res.outcome, "simulated": bool(resp.get("simulated")),
                         "certificate_fingerprint": resp.get("certificate_fingerprint")}
            if r.kind == "ANALYSIS":
                # Solicitação registrada NÃO cancela: aguarda 105104 (deferido) ou 105105 (indeferido) do município.
                resultado["analise"] = {"situacao": "PENDENTE", "consultado_em": None}
                extra["next_attempt_at"] = now + ANALISE_INTERVALO
            return final("REGISTERED", None, None, event_row_id=res.event_row_id, result=resultado, **extra)
        if state == "REJECTED":
            errs = [{"code": str(e.get("code"))[:20], "message": str(e.get("message"))[:300],
                     "complement": e.get("complement")} for e in (resp.get("errors") or [])][:20]
            return final("REJECTED", errs[0]["code"] if errs else "REJECTED",
                         (errs[0]["message"] if errs else "Pedido recusado.")[:500], errors=errs)
        if state == "BLOCKED":
            err = resp.get("error") or {"code": "BLOCKED", "message": "Integração bloqueada."}
            # O conector garante que nada foi transmitido.
            return final("BLOCKED", str(err.get("code"))[:60], str(err.get("message"))[:500])
        if state == "LOCAL_BLOCK" or (state == "AUTH_REVOKED" and r.submit_phase != "SUBMITTING"):
            err = resp["error"]
            return final("WITHDRAWN", err["code"], err["message"])
        if state == "CONFLICT" and r.submit_phase == "SUBMITTING":
            # Conector já tem o pedido/evento: reconsulta na próxima tentativa.
            return _await(r, now, resp["error"]["code"], resp["error"]["message"], common)
        if state == "CONFLICT":
            err = resp["error"]
            return final("BLOCKED", err["code"], err["message"])
        if state == "DEFER" and r.submit_phase != "SUBMITTING":
            err = resp["error"]
            for k, v in {**common, "status": "QUEUED", "next_attempt_at": now + _backoff(r.attempts),
                         "last_error_code": err["code"], "last_error_message": err["message"]}.items():
                setattr(r, k, v)
            return "QUEUED"
        err = resp.get("error") or {"code": state or "UNCERTAIN",
                                    "message": "Resultado ainda não confirmado; será reconciliado por consulta."}
        return _await(r, now, str(err.get("code"))[:60], str(err.get("message"))[:500], common)


def _await(r: CancellationRequest, now, code: str, message: str, common: dict) -> str:  # noqa: ANN001
    for k, v in {**common, "status": "AWAITING_CONFIRMATION", "next_attempt_at": now + _backoff(r.attempts),
                 "last_error_code": code, "last_error_message": message}.items():
        setattr(r, k, v)
    return "AWAITING_CONFIRMATION"


ANALISE_INTERVALO = timedelta(hours=6)
DECISOES = {"105104": "DEFERIDA", "105105": "INDEFERIDA"}


def follow_analysis(sessions: sessionmaker, nfse: NfseClient, clock: Clock, companies: Callable[[str], "CompanyInfo | None"],
                    limits: Limits, *, limit: int = 20) -> int:
    """Acompanha solicitações de análise fiscal registradas: consulta 105104/105105 (somente leitura) e aplica o
    evento do município pelo mesmo caminho da ingestão. Também reconhece o evento já trazido pela sincronização ADN."""
    from ..models import DocumentEvent

    now = clock.now()
    with sessions() as s:
        ids = list(s.scalars(select(CancellationRequest.id).where(
            CancellationRequest.kind == "ANALYSIS", CancellationRequest.status == "REGISTERED",
            CancellationRequest.next_attempt_at <= now).order_by(CancellationRequest.next_attempt_at).limit(limit)))
    feitos = 0
    for rid in ids:
        with sessions() as s:
            r = s.get(CancellationRequest, rid)
            if r is None or ((r.result or {}).get("analise") or {}).get("situacao") not in (None, "PENDENTE"):
                continue
            company = companies(r.company_id)
            locais = {e for e in s.scalars(select(DocumentEvent.event_type).where(
                DocumentEvent.company_id == r.company_id, DocumentEvent.environment == r.environment,
                DocumentEvent.access_key == r.access_key))}
            env, key, cnpj = r.environment, r.access_key, r.company_cnpj
        decisao, xml = None, None
        for code, situacao in DECISOES.items():
            if f"e{code}" in locais:
                decisao = situacao
                break
        if decisao is None:
            for code, situacao in DECISOES.items():
                try:
                    q = nfse.query_event(r.company_id, env, cnpj, key, code)
                except UpstreamError:
                    q = {"state": "UNAVAILABLE"}
                if q.get("state") != "OK":
                    break
                if q.get("found") and q.get("event_xml_gzip_b64"):
                    decisao, xml = situacao, gzip.decompress(base64.b64decode(q["event_xml_gzip_b64"]))
                    break
        with sessions() as s, s.begin():
            r = s.scalar(select(CancellationRequest).where(CancellationRequest.id == rid).with_for_update())
            res = dict(r.result or {})
            analise = dict(res.get("analise") or {})
            analise["consultado_em"] = now.isoformat()
            if xml is not None and company is not None:
                try:
                    ingest_xml(s, company=company, environment=r.environment, raw=xml, source="EVENT_REQ",
                               simulated=bool(r.simulated), now=now, limits=limits)
                except XmlRejected:
                    decisao = None
            if decisao:
                analise["situacao"] = decisao
                r.next_attempt_at = now + timedelta(days=36500)
                audit(s, now, f"cancellation.analysis_{decisao.lower()}", company_id=r.company_id, actor_type="worker",
                      actor_id="follow_analysis", target_type="document", target_id=r.document_id, trace_id=r.trace_id,
                      details={"request_id": r.id, "environment": r.environment})
            else:
                analise.setdefault("situacao", "PENDENTE")
                r.next_attempt_at = now + ANALISE_INTERVALO
            res["analise"] = analise
            r.result = res
            r.updated_at = now
        feitos += 1
    return feitos


def pending_count(sessions: sessionmaker) -> int:
    with sessions() as s:
        return s.scalar(select(func.count()).select_from(CancellationRequest)
                        .where(CancellationRequest.status.in_(ACTIVE))) or 0
