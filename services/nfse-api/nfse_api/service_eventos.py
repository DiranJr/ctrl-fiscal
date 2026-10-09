"""Ledger de pedidos de registro de evento do emitente (cancelamento 101101 / análise fiscal 101103).

Mesmas garantias do envio de DPS:
- o pedido é gravado ANTES da transmissão (estado RECEIVED → SENDING com lease);
- timeout/erro após o envio => UNCERTAIN; qualquer nova chamada CONSULTA o evento (GET …/eventos/{tipo}/1)
  antes de reenviar, e só reenvia quando a consulta confirma ausência (NOT_FOUND);
- recusa oficial (HTTP 400) só vira REJECTED depois de consultar: se o evento já existe (ex.: E0840 por envio
  anterior cujo retorno se perdeu), o resultado é REGISTERED — nunca se transforma sucesso em recusa;
- falha de comunicação nunca vira recusa fiscal; nada de protocolo/status inventado: REGISTERED só com o XML
  `evento` devolvido ou consultado na Sefin (no mock, XML SIMULADO marcado como tal).
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from ctrl_common.clock import Clock
from ctrl_common.problems import ProblemError

from . import eventos as ev_mod
from .models import EventRequest
from .providers.base import CompanyContext, ProviderBlocked, TransmissionNotSent, TransmissionUncertain

log = logging.getLogger("ctrl.nfse.eventos")
FINAL = ("REGISTERED", "REJECTED")


def payload_hash(req: dict) -> str:
    keys = ("company_id", "environment", "company_cnpj", "access_key", "event_code", "c_motivo", "x_motivo")
    return hashlib.sha256(json.dumps({k: req[k] for k in keys}, sort_keys=True).encode()).hexdigest()


def event_out(row: EventRequest) -> dict:
    return {
        "request_id": row.id, "environment": row.environment, "access_key": row.access_key,
        "event_code": row.event_code, "pre_id": row.pre_id, "state": row.state, "simulated": row.simulated,
        "transmissions": row.transmissions, "result": row.result, "errors": row.errors or [],
        "certificate_fingerprint": row.certificate_fingerprint,
        "event_xml_gzip_b64": base64.b64encode(row.event_xml_gz).decode() if row.event_xml_gz else None,
        "error": {"code": row.last_error_code, "message": _MESSAGES.get(row.last_error_code or "", "")}
        if row.last_error_code else None,
    }


_MESSAGES = {
    "TIMEOUT": "Sem confirmação do registro; será reconciliado por consulta ao evento.",
    "CONNECT_FAILED": "Falha de conexão antes do envio.",
    "REJECTION_UNCONFIRMED": "Recusa recebida, mas a consulta de conferência falhou; será reconciliado.",
}


class EventLedger:
    def __init__(self, sessions: sessionmaker, clock: Clock, providers: dict, sending_lease_seconds: int = 300) -> None:
        self.sessions = sessions
        self.clock = clock
        self.providers = providers
        self.lease = sending_lease_seconds

    def _provider(self, environment: str):  # noqa: ANN202
        p = self.providers.get(environment)
        if p is None:
            raise ProblemError(422, "UNKNOWN_ENVIRONMENT", "Ambiente desconhecido.")
        return p

    # ------------------------------------------------------------------ registro
    def register(self, req: dict) -> dict:
        env = req["environment"]
        provider = self._provider(env)
        h = payload_hash(req)
        pre_id = "PRE" + req["access_key"] + req["event_code"]
        now = self.clock.now()
        with self.sessions() as s, s.begin():
            row = s.scalar(select(EventRequest).where(EventRequest.id == req["request_id"]).with_for_update())
            if row is not None:
                if row.payload_hash != h:
                    raise ProblemError(409, "EVENT_REQUEST_MISMATCH", "request_id já usado com outro conteúdo.")
                if row.state in FINAL:
                    return event_out(row)
                if row.state == "SENDING" and row.sending_until and row.sending_until > now:
                    return {**event_out(row), "state": "IN_PROGRESS"}
                needs_reconcile = row.state in ("SENDING", "UNCERTAIN")
            else:
                done = s.scalar(select(EventRequest.id).where(EventRequest.environment == env, EventRequest.pre_id == pre_id,
                                                              EventRequest.state == "REGISTERED"))
                if done:
                    raise ProblemError(409, "EVENT_ALREADY_REGISTERED", "Evento já registrado para esta NFS-e.")
                row = EventRequest(id=req["request_id"], company_id=req["company_id"], environment=env,
                                   company_cnpj=req["company_cnpj"], access_key=req["access_key"],
                                   event_code=req["event_code"], pre_id=pre_id, active_pre_id=pre_id,
                                   c_motivo=req["c_motivo"], x_motivo=req["x_motivo"], payload_hash=h,
                                   state="RECEIVED", transmissions=0, simulated=provider.simulated,
                                   created_at=now, updated_at=now)
                s.add(row)
                try:
                    s.flush()
                except IntegrityError:
                    raise ProblemError(409, "EVENT_REQUEST_ACTIVE", "Já existe pedido ativo para este evento.")
                needs_reconcile = False
        if needs_reconcile:
            rec = self.reconcile(req["request_id"])
            if rec["state"] != "NOT_FOUND":
                return rec
        return self._transmit(req["request_id"], provider)

    def _transmit(self, request_id: str, provider) -> dict:  # noqa: ANN001
        with self.sessions() as s:
            row = s.get(EventRequest, request_id)
            company = CompanyContext(row.company_id, row.company_cnpj, row.environment)
            args = (row.access_key, row.event_code, row.c_motivo, row.x_motivo)
        try:
            provider.check_ready(company, "event")
        except ProviderBlocked as exc:
            return self._set(request_id, state="BLOCKED", last_error_code=exc.code, extra=exc)
        now = self.clock.now()
        with self.sessions() as s, s.begin():
            row = s.scalar(select(EventRequest).where(EventRequest.id == request_id).with_for_update())
            if row.state in FINAL:
                return event_out(row)
            if row.state == "SENDING" and row.sending_until and row.sending_until > now:
                return {**event_out(row), "state": "IN_PROGRESS"}
            from .service import _cert_fp

            row.certificate_fingerprint = _cert_fp(provider, company)
            row.state = "SENDING"  # commitado antes de qualquer byte sair
            row.sending_until = now + timedelta(seconds=self.lease)
            row.transmissions += 1
            row.updated_at = now
        try:
            outcome = provider.register_event(company, *args)
        except ProviderBlocked as exc:
            return self._set(request_id, state="BLOCKED", last_error_code=exc.code, extra=exc)
        except TransmissionNotSent as exc:
            return self._set(request_id, state="NOT_FOUND", last_error_code=exc.code)
        except TransmissionUncertain as exc:
            return self._set(request_id, state="UNCERTAIN", last_error_code=exc.code)
        except Exception as exc:  # qualquer falha após SENDING é incerta
            log.error("falha inesperada no evento request=%s tipo=%s", request_id, type(exc).__name__)
            return self._set(request_id, state="UNCERTAIN", last_error_code="UNEXPECTED")
        if outcome.state == "REGISTERED":
            return self._registered(request_id, outcome.event_xml, request_xml=outcome.request_xml)
        if outcome.official_rejection:
            try:
                q = provider.query_event(company, args[0], args[1])
            except (ProviderBlocked, TransmissionNotSent, TransmissionUncertain):
                return self._set(request_id, state="UNCERTAIN", last_error_code="REJECTION_UNCONFIRMED",
                                 errors=outcome.errors[:20])
            if q.found:
                return self._registered(request_id, q.event_xml, request_xml=outcome.request_xml, reconciled=True,
                                        rejection=outcome.errors[:5])
        return self._set(request_id, state="REJECTED", errors=outcome.errors[:20], request_xml=outcome.request_xml,
                         last_error_code=(outcome.errors[0]["code"] if outcome.errors else "REJECTED"))

    def _registered(self, request_id: str, event_xml: bytes | None, *, request_xml: bytes | None = None,
                    reconciled: bool = False, rejection: list | None = None) -> dict:
        if not event_xml:
            return self._set(request_id, state="UNCERTAIN", last_error_code="EVENT_XML_MISSING")
        try:
            info = ev_mod.resumo_evento(event_xml)
        except Exception:  # noqa: BLE001
            return self._set(request_id, state="UNCERTAIN", last_error_code="EVENT_XML_INVALID")
        with self.sessions() as s:
            row = s.get(EventRequest, request_id)
            expected_key, expected_code, ours = row.access_key, row.event_code, row.pre_id
        if info.get("access_key") != expected_key or info.get("event_code") != "e" + expected_code:
            return self._set(request_id, state="UNCERTAIN", last_error_code="EVENT_XML_MISMATCH")
        result = {**info, "reconciled": reconciled, "same_request": info.get("pre_id") == ours}
        if rejection:
            result["rejection_before_query"] = rejection
        return self._set(request_id, state="REGISTERED", result=result, event_xml=event_xml, request_xml=request_xml,
                         reconciled=reconciled)

    # ------------------------------------------------------------------ reconciliação
    def reconcile(self, request_id: str) -> dict:
        with self.sessions() as s:
            row = s.get(EventRequest, request_id)
            if row is None:
                raise ProblemError(404, "EVENT_REQUEST_NOT_FOUND", "Pedido de evento não registrado.")
            if row.state not in ("SENDING", "UNCERTAIN"):
                return event_out(row)
            if row.state == "SENDING" and row.sending_until and row.sending_until > self.clock.now():
                return {**event_out(row), "state": "IN_PROGRESS"}
            company = CompanyContext(row.company_id, row.company_cnpj, row.environment)
            key, code = row.access_key, row.event_code
            provider = self._provider(row.environment)
        try:
            q = provider.query_event(company, key, code)
        except (ProviderBlocked, TransmissionNotSent, TransmissionUncertain):
            return self._set(request_id, state="UNCERTAIN", reconciled=True)
        if q.found:
            return self._registered(request_id, q.event_xml, reconciled=True)
        # Consulta confirmou ausência: reenvio com o mesmo Id de pedido é permitido.
        return self._set(request_id, state="NOT_FOUND", reconciled=True)

    def get(self, request_id: str, reconcile: bool) -> dict:
        if reconcile:
            return self.reconcile(request_id)
        with self.sessions() as s:
            row = s.get(EventRequest, request_id)
            if row is None:
                raise ProblemError(404, "EVENT_REQUEST_NOT_FOUND", "Pedido de evento não registrado.")
            return event_out(row)

    def _set(self, request_id: str, *, state: str, last_error_code: str | None = None, result: dict | None = None,
             errors: list | None = None, reconciled: bool = False, extra: ProviderBlocked | None = None,
             event_xml: bytes | None = None, request_xml: bytes | None = None) -> dict:
        now = self.clock.now()
        with self.sessions() as s, s.begin():
            row = s.scalar(select(EventRequest).where(EventRequest.id == request_id).with_for_update())
            if row.state in FINAL:
                return event_out(row)  # nunca rebaixa um resultado final
            row.state = state
            row.sending_until = None
            row.updated_at = now
            if state in FINAL or state == "BLOCKED":
                # Libera novo pedido do mesmo evento: depois de uma recusa ou de um bloqueio ANTES do envio (nada foi
                # transmitido; o fiscal-worker trata BLOCKED como final e um novo pedido precisa ser aceito).
                row.active_pre_id = None
            if last_error_code is not None or state in FINAL:
                row.last_error_code = last_error_code
            if result is not None:
                row.result = result
            if errors is not None:
                row.errors = errors
            if event_xml:
                row.event_xml_gz = gzip.compress(event_xml)
            if request_xml:
                row.request_xml_gz = gzip.compress(request_xml)
            if reconciled:
                row.last_reconciled_at = now
            out = event_out(row)
        if extra is not None:
            out["error"] = {"code": extra.code, "message": extra.message}
            out["integration_status"] = extra.status
        return out
