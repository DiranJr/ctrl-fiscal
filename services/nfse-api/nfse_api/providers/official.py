"""Provider oficial: Sefin Nacional (emissão/consulta), ADN Contribuintes (distribuição) e Parametrização.

Contratos CONFIRMADOS em 07/10/2026 pelas especificações oficiais obtidas com o e-CNPJ
(docs/oficial/manifesto-contratos.json):
- POST /SefinNacional/nfse {dpsXmlGZipB64} → 201 {idDps, chaveAcesso, nfseXmlGZipB64, alertas[]} |
  400/403/500 {idDPS?, erros[{codigo, descricao, complemento}]}.
- GET /SefinNacional/dps/{id} → 200 {idDps, chaveAcesso} | 404 ResponseErro.
- GET /SefinNacional/nfse/{chave} → 200 {chaveAcesso, nfseXmlGZipB64}.
- GET /contribuintes/DFe/{NSU}?lote=true&cnpjConsulta= → LoteDistribuicaoNSUResponse; fim = HTTP 404 +
  NENHUM_DOCUMENTO_LOCALIZADO (E2220); ArquivoXml = base64 de GZip (verificado); lotes de até 50.
- GET /parametrizacao/{municipio}/convenio.
Bloqueios mantidos: produção NÃO emite nem consulta DPS nesta etapa (somente distribuição ADN em leitura,
com flag explícita).
"""
from __future__ import annotations

import base64
import gzip
import io
import json
import logging
import ssl
import threading
from datetime import datetime

from cryptography.hazmat.primitives import serialization
from defusedxml import ElementTree as SafeET
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from ctrl_common.clock import Clock

from .. import dps as dps_mod
from .. import eventos as ev_mod
from .. import incidencia
from ..certs import CertificateReport, load_pfx
from ..models import Certificate
from ..settings import NfseSettings
from ..signer import sign_enveloped
from ..transport import BASE_PATHS, OfficialTransport, TransportError, build_ssl_context
from ..vault import Vault, VaultUnavailable
from .base import (
    CompanyContext,
    DistributionPage,
    EventOutcome,
    EventQuery,
    ProviderBlocked,
    QueryOutcome,
    SubmitOutcome,
    TransmissionNotSent,
    TransmissionUncertain,
)

log = logging.getLogger("ctrl.nfse.official")
# Contratos confirmados (envelopes, base paths, códigos) — ver docstring.
CONTRACT_CONFIRMED = True
UNCONFIRMED_CONTRACT: list[str] = []
NS = "{http://www.sped.fazenda.gov.br/nfse}"
READ_ONLY_PRODUCTION_OPERATIONS = ("distribution", "parametros")


def contract_confirmed() -> bool:
    return CONTRACT_CONFIRMED and all(v for env in BASE_PATHS.values() for v in env.values())


def _gunzip_b64(value: str | None, limit: int = 5 * 1024 * 1024) -> bytes | None:
    if not value:
        return None
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(value, validate=True))) as gz:
        data = gz.read(limit + 1)
    if len(data) > limit:
        raise TransmissionUncertain("NFSE_XML_TOO_LARGE")
    return data


def _nfse_number(xml: bytes | None) -> str | None:
    if not xml:
        return None
    root = SafeET.fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    n = root.find(f"{NS}infNFSe/{NS}nNFSe")
    return n.text.strip() if n is not None and n.text else None


def _errors(body: dict) -> list[dict]:
    raw = body.get("erros") or body.get("Erros") or ([body["erro"]] if body.get("erro") else [])
    out = []
    for e in raw[:20]:
        out.append({"code": str(e.get("codigo") or e.get("Codigo") or "")[:20],
                    "message": str(e.get("descricao") or e.get("Descricao") or "")[:300],
                    "complement": str(e.get("complemento") or e.get("Complemento") or "")[:300] or None})
    return out


class OfficialProvider:
    simulated = False

    def __init__(self, environment: str, sessions: sessionmaker, vault: Vault, settings: NfseSettings, clock: Clock,
                 http_factory=None, validator=None) -> None:  # noqa: ANN001
        if environment not in ("restricted", "production"):
            raise ValueError("ambiente oficial inválido")
        self.environment = environment
        self.sessions = sessions
        self.vault = vault
        self.settings = settings
        self.clock = clock
        self._http_factory = http_factory  # testes: injeta httpx.Client com MockTransport
        self.validator = validator or settings.icp_validator()
        self._ssl_cache: dict[tuple[str, str], ssl.SSLContext] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ certificado
    def active_certificate(self, company: CompanyContext) -> Certificate | None:
        with self.sessions() as s:
            return s.scalar(
                select(Certificate).where(
                    Certificate.company_id == company.company_id, Certificate.environment == self.environment,
                    Certificate.is_active.is_(True),
                ).order_by(Certificate.created_at.desc()).limit(1)
            )

    def _material(self, company: CompanyContext):  # noqa: ANN202
        cert_row = self.active_certificate(company)
        if cert_row is None:
            raise ProviderBlocked("NOT_CONFIGURED", "CERTIFICATE_NOT_CONFIGURED", "Certificado não configurado.")
        aad = f"{company.company_id}:{self.environment}".encode()
        try:
            pfx = self.vault.decrypt(cert_row.encrypted_pfx, aad)
            password = self.vault.decrypt(cert_row.encrypted_password, aad)
        except VaultUnavailable:
            raise ProviderBlocked("NOT_CONFIGURED", "VAULT_KEY_NOT_CONFIGURED", "Chave do cofre ausente.")
        except Exception:
            raise ProviderBlocked("BLOCKED", "CERTIFICATE_DECRYPT_FAILED", "Não foi possível abrir o certificado do cofre.")
        key, cert, extra = load_pfx(pfx, password)
        return cert_row, key, cert, extra

    def _ssl(self, company: CompanyContext) -> ssl.SSLContext:
        cert_row, key, cert, extra = self._material(company)
        cache_key = (company.company_id, cert_row.fingerprint_sha256)
        with self._lock:
            ctx = self._ssl_cache.get(cache_key)
            if ctx is None:
                chain = cert.public_bytes(serialization.Encoding.PEM) + b"".join(
                    c.public_bytes(serialization.Encoding.PEM) for c in extra)
                key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption())
                ctx = build_ssl_context(chain, key_pem)
                del key_pem
                self._ssl_cache = {k: v for k, v in self._ssl_cache.items() if k[0] != company.company_id}
                self._ssl_cache[cache_key] = ctx
            return ctx

    def _transport(self, company: CompanyContext, service: str) -> OfficialTransport:
        http = self._http_factory(service) if self._http_factory else None
        ssl_ctx = None if http is not None else self._ssl(company)
        return OfficialTransport(self.environment, service, ssl_ctx, timeout=self.settings.http_timeout_seconds,
                                 max_response_bytes=self.settings.max_response_bytes, http=http)

    # ------------------------------------------------------------------ prontidão
    def blockers(self, company: CompanyContext, operation: str = "submit") -> tuple[str, list[dict[str, str]]]:
        problems: list[dict[str, str]] = []
        if self.environment == "production" and operation in ("event", "event_query"):
            # Política de CANCELAMENTO em produção separada da de emissão (NFSE_PRODUCTION_CANCELLATION_ENABLED).
            # Habilitada por autorização do titular em 09/10/2026; a emissão em produção continua bloqueada.
            if not self.settings.production_cancellation_enabled:
                problems.append({"code": "PRODUCTION_CANCELLATION_BLOCKED",
                                 "message": "Produção: cancelamento pela plataforma desabilitado na configuração."})
        elif self.environment == "production":
            if operation not in READ_ONLY_PRODUCTION_OPERATIONS:
                problems.append({"code": "PRODUCTION_EMISSION_BLOCKED",
                                 "message": "Produção: emissão/consulta de DPS bloqueadas nesta etapa (somente leitura ADN)."})
            elif not self.settings.production_distribution_enabled:
                problems.append({"code": "PRODUCTION_DISTRIBUTION_DISABLED",
                                 "message": "Leitura ADN de produção desabilitada na configuração."})
        elif not self.settings.restricted_enabled:
            problems.append({"code": "RESTRICTED_DISABLED", "message": "Produção Restrita desabilitada na configuração."})
        if not self.vault.available:
            return "NOT_CONFIGURED", problems + [{"code": "VAULT_KEY_NOT_CONFIGURED", "message": "Chave do cofre ausente."}]
        cert = self.active_certificate(company)
        if cert is None:
            return "NOT_CONFIGURED", problems + [{"code": "CERTIFICATE_NOT_CONFIGURED",
                                                  "message": "Nenhum certificado A1 instalado para esta empresa/ambiente."}]
        checks = self._fresh_checks(cert)
        report = CertificateReport(cert.subject_cnpj, cert.not_before, cert.not_after, cert.fingerprint_sha256, checks)
        _, cert_problems = report.ok_for(company.cnpj, self.clock.now())
        problems.extend(cert_problems)
        if not contract_confirmed():
            problems.append({"code": "OFFICIAL_CONTRACT_UNCONFIRMED", "message": "Contrato oficial não confirmado."})
        # READY só com cadeia ICP-Brasil válida, revogação confirmada, validade, usos e CNPJ — nenhum aviso é ignorado.
        return ("BLOCKED" if problems else "READY"), problems

    def _fresh_checks(self, cert: Certificate) -> dict:
        """Reconfirma a LCR quando a última verificação passou de 24 h e persiste o resultado."""
        updated = self.validator.recheck_revocation(cert.checks or {}, self.clock.now())
        if updated is None:
            return cert.checks or {}
        with self.sessions() as s, s.begin():
            row = s.get(Certificate, cert.id)
            if row is not None:
                row.checks = updated
        return updated

    def check_ready(self, company: CompanyContext, operation: str) -> None:
        status, problems = self.blockers(company, operation)
        if status != "READY":
            first = problems[0]
            raise ProviderBlocked(status, first["code"], first["message"])

    # ------------------------------------------------------------------ Sefin
    def _convenio_ativo(self, company: CompanyContext, municipio: str) -> bool:
        """Convênio do município no Sistema Nacional, NESTE ambiente (Parametrização, leitura).
        200 com parametrosConvenio e aderenteAmbienteNacional=1 => ativo; 404 \"ainda não está ativo\" => inativo."""
        try:
            r = self._transport(company, "param").request("GET", f"/{municipio}/convenio", idempotent=True)
        except TransportError as exc:
            raise TransmissionNotSent(f"PARAMETRIZACAO_{exc.code}")
        body = self._json(r.body)
        if r.status == 200 and (body.get("parametrosConvenio") or {}).get("aderenteAmbienteNacional") == 1:
            return True
        if r.status in (200, 404):
            return False
        raise TransmissionNotSent(f"PARAMETRIZACAO_HTTP_{r.status}")

    def _paliq_conforme_convenio(self, company: CompanyContext, payload: dict) -> list[dict]:
        """Anexo I, RN DPS 506/507: para não optante (opSimpNac=1), pAliq é PROIBIDO com convênio do município de
        incidência ativo (E0617) e OBRIGATÓRIO com convênio inativo e regEspTrib=0 (E0619). Depende do ambiente."""
        perfil = (payload.get("company") or {}).get("fiscal_profile") or {}
        if perfil.get("simples_option") != "1":
            return []
        mun = incidencia.municipio_incidencia(payload)
        if not mun:
            return []
        rate = (payload.get("tax_settings") or {}).get("iss_rate")
        ativo = self._convenio_ativo(company, mun)
        if ativo and rate not in (None, ""):
            return [{"code": "LOCAL_E0617", "message": f"Convênio do município de incidência {mun} ATIVO neste ambiente: "
                                                       "não informe alíquota (pAliq)."}]
        if not ativo and rate in (None, "") and perfil.get("special_regime") == "0":
            return [{"code": "LOCAL_E0619", "message": f"Convênio do município de incidência {mun} NÃO ativo neste "
                                                       "ambiente: alíquota (pAliq) obrigatória."}]
        return []

    def submit_dps(self, company: CompanyContext, dps: dict, payload: dict) -> SubmitOutcome:
        self.check_ready(company, "submit")
        regra_paliq = self._paliq_conforme_convenio(company, payload)
        if regra_paliq:
            return SubmitOutcome(state="REJECTED", errors=regra_paliq)  # nada transmitido
        try:
            montada = dps_mod.montar(payload, environment=self.environment, serie=dps["series"], numero=int(dps["number"]),
                                     agora=self.clock.now())
        except dps_mod.DpsIncompleta as exc:
            return SubmitOutcome(state="REJECTED", errors=[{"code": f"LOCAL_{f['codigo']}", "message": f"{f['campo']}: {f['mensagem']}"}
                                                           for f in exc.faltas])
        except dps_mod.DpsInvalidaXsd as exc:
            return SubmitOutcome(state="REJECTED", errors=[{"code": "LOCAL_XSD", "message": m} for m in exc.erros])
        if montada.dps_id != dps["id"]:
            raise ProviderBlocked("BLOCKED", "DPS_ID_MISMATCH", "Id DPS reservado difere do Id calculado pelo leiaute.")
        _, key, cert, _ = self._material(company)
        signed = sign_enveloped(montada.xml, key, cert.public_bytes(serialization.Encoding.DER))
        dps_mod.validar_xsd(signed, self.environment)
        body = {"dpsXmlGZipB64": base64.b64encode(gzip.compress(signed)).decode()}
        try:
            r = self._transport(company, "sefin").request("POST", "/nfse", json=body, idempotent=False)
        except TransportError as exc:
            if exc.uncertain:
                raise TransmissionUncertain(exc.code)
            raise TransmissionNotSent(exc.code)
        data = self._json(r.body)
        if r.status == 201:
            xml = _gunzip_b64(data.get("nfseXmlGZipB64"))
            return SubmitOutcome(state="AUTHORIZED", access_key=data.get("chaveAcesso"), number=_nfse_number(xml),
                                 authorized_at=_dt(data.get("dataHoraProcessamento")), nfse_xml=xml,
                                 alerts=_errors({"erros": data.get("alertas") or []}))
        if r.status == 400:
            return SubmitOutcome(state="REJECTED", errors=_errors(data) or [{"code": "HTTP_400", "message": "Rejeitada"}],
                                 official_rejection=True)
        if r.status == 403:
            errs = _errors(data)
            raise ProviderBlocked("BLOCKED", "CERT_REJECTED_BY_SEFIN",
                                  (errs[0]["message"] if errs else "Certificado da transmissão recusado pela Sefin."))
        # 500 ou inesperado: pode ter sido processado; reconciliação por consulta à DPS.
        raise TransmissionUncertain(f"SEFIN_HTTP_{r.status}")

    def query_dps(self, company: CompanyContext, dps_id: str) -> QueryOutcome:
        self.check_ready(company, "query")
        try:
            t = self._transport(company, "sefin")
            r = t.request("GET", f"/dps/{dps_id}", idempotent=True)
            if r.status == 404:
                return QueryOutcome(found=False)
            if r.status != 200:
                raise TransmissionUncertain(f"SEFIN_DPS_HTTP_{r.status}")
            data = self._json(r.body)
            chave = data.get("chaveAcesso")
            r2 = t.request("GET", f"/nfse/{chave}", idempotent=True)
            xml = _gunzip_b64(self._json(r2.body).get("nfseXmlGZipB64")) if r2.status == 200 else None
        except TransportError as exc:
            raise TransmissionUncertain(exc.code)
        return QueryOutcome(found=True, access_key=chave, number=_nfse_number(xml),
                            authorized_at=_dt(data.get("dataHoraProcessamento")), nfse_xml=xml)

    # ------------------------------------------------------------------ eventos do emitente
    def register_event(self, company: CompanyContext, access_key: str, code: str, c_motivo: str,
                       x_motivo: str) -> EventOutcome:
        """POST /nfse/{chave}/eventos {pedidoRegistroEventoXmlGZipB64} (Swagger Sefin v1, EventosPostRequest).
        201 → evento (eventoXmlGZipB64); 400 → regra de negócio, corpo `erro` (objeto único); 401 → sem permissão.
        Qualquer outro status: incerto (reconciliar por consulta antes de reenviar)."""
        self.check_ready(company, "event")
        try:
            ped = ev_mod.montar(environment=self.environment, access_key=access_key, cnpj_autor=company.cnpj,
                                codigo=code, c_motivo=c_motivo, x_motivo=x_motivo, agora=self.clock.now())
        except ev_mod.EventoInvalido as exc:
            return EventOutcome(state="REJECTED", errors=[{"code": "LOCAL_EVENTO", "message": m} for m in exc.erros])
        _, key, cert, _ = self._material(company)
        signed = sign_enveloped(ped.xml, key, cert.public_bytes(serialization.Encoding.DER))
        try:
            ev_mod.validar_xsd(signed, self.environment)
        except ev_mod.EventoInvalido as exc:
            return EventOutcome(state="REJECTED", errors=[{"code": "LOCAL_XSD", "message": m} for m in exc.erros])
        body = {"pedidoRegistroEventoXmlGZipB64": base64.b64encode(gzip.compress(signed)).decode()}
        try:
            r = self._transport(company, "sefin").request("POST", f"/nfse/{access_key}/eventos", json=body,
                                                          idempotent=False)
        except TransportError as exc:
            if exc.uncertain:
                raise TransmissionUncertain(exc.code)
            raise TransmissionNotSent(exc.code)
        data = self._json(r.body)
        if r.status == 201:
            xml = _gunzip_b64(data.get("eventoXmlGZipB64"))
            return EventOutcome(state="REGISTERED", event_xml=xml, processed_at=_dt(data.get("dataHoraProcessamento")),
                                request_xml=signed)
        if r.status == 400:
            return EventOutcome(state="REJECTED", request_xml=signed, official_rejection=True,
                                errors=_errors(data) or [{"code": "HTTP_400", "message": "Pedido de evento recusado."}])
        if r.status in (401, 403):
            errs = _errors(data)
            raise ProviderBlocked("BLOCKED", "EVENT_NOT_PERMITTED",
                                  errs[0]["message"] if errs else "Sefin recusou a permissão para registrar o evento.")
        raise TransmissionUncertain(f"SEFIN_EVENTO_HTTP_{r.status}")

    def query_event(self, company: CompanyContext, access_key: str, code: str) -> EventQuery:
        """GET /nfse/{chave}/eventos/{tipoEvento}/{numSeqEvento} — evento único: numSeqEvento = 1 (Swagger v1)."""
        self.check_ready(company, "event_query")
        try:
            r = self._transport(company, "sefin").request("GET", f"/nfse/{access_key}/eventos/{int(code)}/1",
                                                          idempotent=True)
        except TransportError as exc:
            raise TransmissionUncertain(exc.code)
        if r.status == 404:
            return EventQuery(found=False)
        if r.status != 200:
            raise TransmissionUncertain(f"SEFIN_EVENTO_GET_HTTP_{r.status}")
        return EventQuery(found=True, event_xml=_gunzip_b64(self._json(r.body).get("eventoXmlGZipB64")))

    # ------------------------------------------------------------------ ADN
    def fetch_distribution(self, company: CompanyContext, from_nsu: int) -> DistributionPage:
        self.check_ready(company, "distribution")
        try:
            r = self._transport(company, "adn").request(
                "GET", f"/DFe/{int(from_nsu)}?lote=true&cnpjConsulta={company.cnpj}", idempotent=True)
        except TransportError as exc:
            raise TransmissionNotSent(exc.code)
        data = self._json(r.body)
        status = data.get("StatusProcessamento")
        if status == "NENHUM_DOCUMENTO_LOCALIZADO" or (r.status == 404 and not data.get("LoteDFe")):
            # Fim da fila (E2220). Manual ADN: aguardar ≥ 1 h antes de nova consulta.
            return DistributionPage(items=[], last_nsu=int(from_nsu), max_nsu=None, has_more=False, retry_after_seconds=3600)
        if r.status != 200 or status != "DOCUMENTOS_LOCALIZADOS":
            codes = ",".join(e["code"] for e in _errors(data)) or f"HTTP_{r.status}"
            raise TransmissionNotSent(f"ADN_{status or 'ERRO'}:{codes}"[:60])
        items = []
        for it in data.get("LoteDFe") or []:
            kind = it.get("TipoDocumento")
            items.append({
                "nsu": int(it["NSU"]), "access_key": it.get("ChaveAcesso"),
                "doc_kind": "NFSE" if kind == "NFSE" else "EVENT" if kind == "EVENTO" else kind,
                "event_type": it.get("TipoEvento"), "xml_gzip_b64": it.get("ArquivoXml"),
                "generated_at": it.get("DataHoraGeracao"),
            })
        last = max((i["nsu"] for i in items), default=int(from_nsu))
        return DistributionPage(items=items, last_nsu=last, max_nsu=None, has_more=len(items) >= 50)

    # ------------------------------------------------------------------ Parametrização (somente leitura)
    def municipal_parameters(self, company: CompanyContext, municipio: str) -> dict:
        self.check_ready(company, "parametros")
        try:
            r = self._transport(company, "param").request("GET", f"/{municipio}/convenio", idempotent=True)
        except TransportError as exc:
            raise TransmissionNotSent(exc.code)
        return {"http_status": r.status, "body": self._json(r.body)}

    @staticmethod
    def _json(body: bytes) -> dict:
        try:
            data = json.loads(body.decode("utf-8-sig") or "{}")
            return data if isinstance(data, dict) else {}
        except (ValueError, UnicodeDecodeError):
            return {}


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
