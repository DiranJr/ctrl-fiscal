"""Conector: ledger por submission_id/DPS/ambiente gravado ANTES da transmissão.

- Duplicata (mesmo submission_id e hash) devolve o resultado salvo.
- Mesmo submission_id com conteúdo diferente => 409.
- Mesmo Id DPS com outro submission_id => 409.
- Timeout após envio => UNCERTAIN; nova chamada reconcilia (consulta por DPS)
  ANTES de qualquer reenvio. Só reenvia quando a consulta confirma ausência.
- Eventos do emitente (cancelamento/análise fiscal): ledger próprio em service_eventos.py.
"""
from __future__ import annotations

import base64
import gzip
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from ctrl_common.clock import Clock
from ctrl_common.problems import ProblemError

from .models import Submission
from .providers.base import (
    CompanyContext,
    NfseProvider,
    ProviderBlocked,
    TransmissionNotSent,
    TransmissionUncertain,
)

log = logging.getLogger("ctrl.nfse")
FINAL = ("AUTHORIZED", "REJECTED")


def submission_out(row: Submission) -> dict:
    return {
        "submission_id": row.id,
        "dps_id": row.dps_id,
        "environment": row.environment,
        "state": row.state,
        "simulated": row.simulated,
        "transmissions": row.transmissions,
        "certificate_fingerprint": row.certificate_fingerprint,
        "result": row.result,
        "nfse_xml_gzip_b64": base64.b64encode(row.nfse_xml_gz).decode() if row.nfse_xml_gz else None,
        "errors": row.errors or [],
        "error": {"code": row.last_error_code, "message": _messages.get(row.last_error_code or "", "")}
        if row.last_error_code else None,
    }


_messages = {
    "TIMEOUT": "Sem confirmação do processamento; será reconciliado por consulta à DPS.",
    "CONNECT_FAILED": "Falha de conexão antes do envio.",
}


class Connector:
    def __init__(self, sessions: sessionmaker, clock: Clock, providers: dict[str, NfseProvider], sending_lease_seconds: int = 300) -> None:
        self.sessions = sessions
        self.clock = clock
        self.providers = providers
        self.sending_lease_seconds = sending_lease_seconds

    def provider(self, environment: str) -> NfseProvider:
        p = self.providers.get(environment)
        if p is None:
            raise ProblemError(422, "UNKNOWN_ENVIRONMENT", "Ambiente desconhecido.")
        return p

    # ------------------------------------------------------------------ envio
    def submit(self, req: dict, payload_hash: str) -> dict:
        env = req["environment"]
        company = CompanyContext(req["company_id"], req["company_cnpj"], env)
        provider = self.provider(env)
        now = self.clock.now()
        with self.sessions() as s, s.begin():
            row = s.scalar(select(Submission).where(Submission.id == req["submission_id"]).with_for_update())
            if row is not None:
                if row.payload_hash != payload_hash or row.dps_id != req["dps"]["id"] or row.environment != env:
                    raise ProblemError(409, "SUBMISSION_PAYLOAD_MISMATCH", "submission_id já usado com outro conteúdo.")
                if row.state in FINAL:
                    return submission_out(row)
                if row.state == "SENDING" and row.sending_until and row.sending_until > now:
                    return {**submission_out(row), "state": "IN_PROGRESS"}
                needs_reconcile = row.state in ("SENDING", "UNCERTAIN")
            else:
                clash = s.scalar(select(Submission.id).where(Submission.environment == env,
                                                             Submission.dps_id == req["dps"]["id"]))
                if clash:
                    raise ProblemError(409, "DPS_ID_CONFLICT", "Id DPS já vinculado a outro envio.")
                row = Submission(id=req["submission_id"], company_id=req["company_id"], environment=env,
                                 company_cnpj=req["company_cnpj"], dps_id=req["dps"]["id"], payload_hash=payload_hash,
                                 state="RECEIVED", transmissions=0, simulated=provider.simulated,
                                 created_at=now, updated_at=now)
                s.add(row)
                try:
                    s.flush()
                except IntegrityError:
                    raise ProblemError(409, "SUBMISSION_CONFLICT", "Envio concorrente com o mesmo identificador.")
                needs_reconcile = False
        if needs_reconcile:
            rec = self.reconcile(req["submission_id"])
            if rec["state"] != "NOT_FOUND":
                return rec
        return self._transmit(req["submission_id"], company, provider, req)

    def _transmit(self, submission_id: str, company: CompanyContext, provider: NfseProvider, req: dict) -> dict:
        try:
            provider.check_ready(company, "submit")
        except ProviderBlocked as exc:
            return self._set(submission_id, state="BLOCKED", last_error_code=exc.code, extra_error=exc)
        now = self.clock.now()  # depois de check_ready (que pode demorar), para o lease cobrir o envio inteiro
        with self.sessions() as s, s.begin():
            row = s.scalar(select(Submission).where(Submission.id == submission_id).with_for_update())
            if row.state in FINAL:
                return submission_out(row)
            # Outro processo marcou SENDING enquanto este conferia a prontidão: não envia de novo.
            if row.state == "SENDING" and row.sending_until and row.sending_until > now:
                return {**submission_out(row), "state": "IN_PROGRESS"}
            # Marca SENDING (e commita) antes de qualquer byte sair. O lease (padrão 300 s) cobre o pior caso do
            # envio oficial: consulta de convênio com novas tentativas + POST, cada chamada com timeout de 30 s.
            row.certificate_fingerprint = _cert_fp(provider, company)
            row.state = "SENDING"
            row.sending_until = now + timedelta(seconds=self.sending_lease_seconds)
            row.transmissions += 1
            row.updated_at = now
        try:
            outcome = provider.submit_dps(company, req["dps"], req["payload"])
        except ProviderBlocked as exc:
            return self._set(submission_id, state="BLOCKED", last_error_code=exc.code, extra_error=exc)
        except TransmissionNotSent as exc:
            return self._set(submission_id, state="NOT_FOUND", last_error_code=exc.code)
        except TransmissionUncertain as exc:
            return self._set(submission_id, state="UNCERTAIN", last_error_code=exc.code)
        except Exception as exc:  # qualquer outra falha após SENDING é incerta
            log.error("falha inesperada no envio submission=%s tipo=%s", submission_id, type(exc).__name__)
            return self._set(submission_id, state="UNCERTAIN", last_error_code="UNEXPECTED")
        if outcome.state == "AUTHORIZED":
            return self._set(submission_id, state="AUTHORIZED", result=_result(outcome), nfse_xml=outcome.nfse_xml)
        if outcome.official_rejection:
            # Recusa oficial pode significar "esta DPS já gerou NFS-e" (envio anterior cujo retorno se perdeu).
            # Antes de registrar REJECTED (final), consulta a DPS: existe => AUTHORIZED; consulta falhou => UNCERTAIN.
            try:
                q = provider.query_dps(company, req["dps"]["id"])
            except (ProviderBlocked, TransmissionNotSent, TransmissionUncertain):
                return self._set(submission_id, state="UNCERTAIN", last_error_code="REJECTION_UNCONFIRMED")
            if q.found:
                return self._set(submission_id, state="AUTHORIZED", reconciled=True, nfse_xml=q.nfse_xml, result={
                    "access_key": q.access_key, "number": q.number,
                    "authorized_at": q.authorized_at.isoformat() if q.authorized_at else None, "reconciled": True,
                })
        return self._set(submission_id, state="REJECTED", errors=outcome.errors[:20])

    # ------------------------------------------------------------ reconciliação
    def reconcile(self, submission_id: str) -> dict:
        with self.sessions() as s:
            row = s.get(Submission, submission_id)
            if row is None:
                raise ProblemError(404, "SUBMISSION_NOT_FOUND", "Envio não registrado.")
            if row.state not in ("SENDING", "UNCERTAIN"):
                return submission_out(row)
            if row.state == "SENDING" and row.sending_until and row.sending_until > self.clock.now():
                return {**submission_out(row), "state": "IN_PROGRESS"}
            company = CompanyContext(row.company_id, row.company_cnpj, row.environment)
            dps_id = row.dps_id
            provider = self.provider(row.environment)
        try:
            q = provider.query_dps(company, dps_id)
        except (ProviderBlocked, TransmissionNotSent, TransmissionUncertain):
            return self._set(submission_id, state="UNCERTAIN", reconciled=True)
        if q.found:
            return self._set(submission_id, state="AUTHORIZED", reconciled=True, nfse_xml=q.nfse_xml, result={
                "access_key": q.access_key, "number": q.number,
                "authorized_at": q.authorized_at.isoformat() if q.authorized_at else None, "reconciled": True,
            })
        # Consulta confirmou ausência: reenvio com a MESMA identidade DPS é permitido.
        return self._set(submission_id, state="NOT_FOUND", reconciled=True)

    def get(self, submission_id: str, reconcile: bool) -> dict:
        if reconcile:
            return self.reconcile(submission_id)
        with self.sessions() as s:
            row = s.get(Submission, submission_id)
            if row is None:
                raise ProblemError(404, "SUBMISSION_NOT_FOUND", "Envio não registrado.")
            return submission_out(row)

    def _set(self, submission_id: str, *, state: str, last_error_code: str | None = None, result: dict | None = None,
             errors: list | None = None, reconciled: bool = False, extra_error: ProviderBlocked | None = None,
             nfse_xml: bytes | None = None) -> dict:
        now = self.clock.now()
        with self.sessions() as s, s.begin():
            row = s.scalar(select(Submission).where(Submission.id == submission_id).with_for_update())
            if row.state in FINAL:
                return submission_out(row)  # nunca rebaixa um resultado final
            row.state = state
            row.sending_until = None
            row.updated_at = now
            if last_error_code is not None or state in FINAL:
                row.last_error_code = last_error_code
            if result is not None:
                row.result = result
            if errors is not None:
                row.errors = errors
            if nfse_xml:
                row.nfse_xml_gz = gzip.compress(nfse_xml)
            if reconciled:
                row.last_reconciled_at = now
            out = submission_out(row)
        if extra_error is not None:
            out["error"] = {"code": extra_error.code, "message": extra_error.message}
            out["integration_status"] = extra_error.status
        return out


def _cert_fp(provider, company) -> str | None:  # noqa: ANN001
    """Certificado ATIVO do próprio workspace usado nesta transmissão (nunca o de outro workspace)."""
    get = getattr(provider, "active_certificate", None)
    cert = get(company) if get else None
    return cert.fingerprint_sha256 if cert else None


def _result(outcome) -> dict:  # noqa: ANN001
    return {
        "access_key": outcome.access_key,
        "number": outcome.number,
        "authorized_at": outcome.authorized_at.isoformat() if outcome.authorized_at else None,
        "alerts": getattr(outcome, "alerts", [])[:20],
    }
