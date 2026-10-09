"""nfse-api: conector interno. Não é roteado pelo gateway; exige identidade de serviço."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from ctrl_common.clock import Clock, SystemClock
from ctrl_common.db import make_engine, make_sessionmaker
from ctrl_common.hashing import canonical_hash
from ctrl_common.problems import ProblemError, install_problem_handlers
from ctrl_common.service_auth import ServiceAuthenticator
from ctrl_common.web import BodySizeLimitMiddleware, TraceMiddleware

from .providers.base import CompanyContext, ProviderBlocked, TransmissionNotSent, TransmissionUncertain
from .providers.mock import MockProvider
from .providers.official import UNCONFIRMED_CONTRACT, OfficialProvider
from . import cert_store
from .service import Connector
from .service_eventos import EventLedger
from .settings import NfseSettings
from .vault import Vault


class DpsRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^DPS[0-9]{7}[12][0-9A-Z]{14}[0-9]{20}$")
    series: str = Field(pattern=r"^[0-9]{1,5}$")
    number: int = Field(ge=1, le=999_999_999_999_999)


class SubmissionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    submission_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    company_id: str = Field(min_length=36, max_length=36)
    environment: Literal["mock", "restricted", "production"]
    company_cnpj: str = Field(pattern=r"^[0-9A-Z]{12}[0-9]{2}$")
    dps: DpsRef
    payload: dict
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class CertificateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cnpj: str = Field(pattern=r"^[0-9A-Z]{12}[0-9]{2}$")
    environments: list[Literal["restricted", "production"]] = Field(min_length=1, max_length=2)
    pfx_b64: str = Field(min_length=16, max_length=120_000)
    password: str = Field(min_length=1, max_length=128)

    def __repr__(self) -> str:  # nunca expor o conteúdo em logs/tracebacks
        return f"CertificateIn(cnpj=***, environments={self.environments})"


class DisabledMock:
    """Mock desligado em execução: nenhuma operação, sem fallback."""

    environment = "mock"
    simulated = True

    def _block(self, *a, **k):  # noqa: ANN002, ANN003, ANN202
        raise ProviderBlocked("BLOCKED", "MOCK_DISABLED", "Simulação desativada neste ambiente de execução.")

    check_ready = submit_dps = query_dps = fetch_distribution = register_event = query_event = _block


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    company_id: str = Field(min_length=36, max_length=36)
    environment: Literal["mock", "restricted", "production"]
    company_cnpj: str = Field(pattern=r"^[0-9A-Z]{12}[0-9]{2}$")
    access_key: str = Field(pattern=r"^[0-9]{6}[0-9A-Z]{14}[0-9]{30}$")
    event_code: Literal["101101", "101103"]
    c_motivo: Literal["1", "2", "9"]
    x_motivo: str = Field(min_length=15, max_length=255)


class DistributionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_id: str = Field(min_length=36, max_length=36)
    environment: Literal["mock", "restricted", "production"]
    cnpj: str = Field(pattern=r"^[0-9A-Z]{12}[0-9]{2}$")
    from_nsu: int = Field(ge=0)


def create_app(settings: NfseSettings, clock: Clock | None = None, mock_provider: MockProvider | None = None,
               official_http_factory=None) -> FastAPI:  # noqa: ANN001
    """official_http_factory: só testes automatizados (httpx.MockTransport no lugar do governo)."""
    settings.validate()
    clock = clock or SystemClock()
    engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)
    vault = Vault(settings.vault_key_b64)
    validator = settings.icp_validator()
    mock = mock_provider or (MockProvider(sessions, clock, settings.mock_page_size) if settings.mock_enabled else DisabledMock())
    providers = {
        "mock": mock,
        "restricted": OfficialProvider("restricted", sessions, vault, settings, clock, official_http_factory, validator),
        "production": OfficialProvider("production", sessions, vault, settings, clock, official_http_factory, validator),
    }
    connector = Connector(sessions, clock, providers, settings.sending_lease_seconds)
    events = EventLedger(sessions, clock, providers, settings.sending_lease_seconds)
    auth = ServiceAuthenticator(settings.internal_clients)
    app = FastAPI(title="CTRL Fiscal — NFS-e connector (interno)", version="0.1.0", docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.engine = engine
    app.state.connector = connector
    app.state.events = events
    app.state.mock = mock
    app.state.providers = providers
    install_problem_handlers(app)
    app.add_middleware(BodySizeLimitMiddleware, default_limit=512 * 1024)
    app.add_middleware(TraceMiddleware)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "nfse-api"}

    @app.get("/ready")
    def ready() -> dict:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            raise ProblemError(503, "DATABASE_UNAVAILABLE", "Banco indisponível.")
        return {"status": "ready", "service": "nfse-api"}

    @app.get("/internal/v1/companies/{company_id}/integration-status")
    def integration_status(
        company_id: str, request: Request,
        environment: Literal["mock", "restricted", "production"] = Query(...),
        cnpj: str = Query(..., pattern=r"^[0-9A-Z]{12}[0-9]{2}$"),
    ) -> dict:
        auth.authenticate(request, "integration:read")
        now: datetime = clock.now()
        company = CompanyContext(company_id, cnpj, environment)
        if environment == "mock":
            enabled = not isinstance(mock, DisabledMock)
            return {
                "company_id": company_id, "environment": "mock", "status": "SIMULATED" if enabled else "BLOCKED",
                "simulated": True, "provider": "mock", "certificate": {"present": False},
                "capabilities": {"emission": enabled, "distribution": enabled, "cancellation": enabled, "simulated": True},
                "blockers": [{"code": "SIMULATION_ONLY" if enabled else "MOCK_DISABLED",
                              "message": "Ambiente de simulação: nada é enviado ao governo." if enabled
                              else "Simulação desativada neste ambiente de execução."}],
                "checked_at": now,
            }
        prov: OfficialProvider = providers[environment]  # type: ignore[assignment]
        st_emit, problems = prov.blockers(company, "submit")
        st_dist, problems_dist = prov.blockers(company, "distribution")
        st_event, _ = prov.blockers(company, "event")
        for p_ in problems_dist:
            if p_ not in problems:
                problems.append(p_)
        cert = prov.active_certificate(company)
        cert_out = {"present": False}
        if cert is not None:
            cert_out = {
                "present": True, "subject_cnpj_matches": cert.subject_cnpj == cnpj, "not_before": cert.not_before,
                "not_after": cert.not_after, "days_to_expiry": (cert.not_after - now).days,
            }
        status = "READY" if st_emit == "READY" else ("READY" if st_dist == "READY" and environment == "production" else st_emit)
        return {
            "company_id": company_id, "environment": environment, "status": status, "simulated": False,
            "provider": "official", "certificate": cert_out,
            "capabilities": {"emission": st_emit == "READY", "distribution": st_dist == "READY",
                             "cancellation": st_event == "READY",
                             "simulated": False},
            # policy = chave de configuração ligada; capabilities = empresa pronta (chave + certificado + contrato).
            "policy": prov.policy(),
            "blockers": problems, "checked_at": now,
        }

    @app.post("/internal/v1/companies/{company_id}/certificates", status_code=201)
    def install_certificate(company_id: str, body: CertificateIn, request: Request) -> dict:
        auth.authenticate(request, "certificates:write")
        import base64 as _b64

        try:
            pfx = _b64.b64decode(body.pfx_b64, validate=True)
        except ValueError:
            raise ProblemError(422, "PFX_INVALID", "Arquivo PFX inválido.")
        try:
            return cert_store.install(sessions, vault, company_id=company_id, cnpj=body.cnpj,
                                      environments=list(body.environments), pfx=pfx,
                                      password=body.password.encode("utf-8"), now=clock.now(), validator=validator)
        except cert_store.CertificateRejected as exc:
            raise ProblemError(422, exc.code, exc.message,
                               errors=[{"field": "file", "code": p_["code"], "message": p_["message"]} for p_ in exc.problems] or None)
        finally:
            del pfx

    @app.get("/internal/v1/companies/{company_id}/certificates")
    def certificate_history(company_id: str, request: Request) -> list[dict]:
        auth.authenticate(request, "integration:read")
        return cert_store.history(sessions, company_id=company_id)

    @app.delete("/internal/v1/companies/{company_id}/certificates", status_code=204)
    def remove_certificate(company_id: str, request: Request,
                           environment: Literal["restricted", "production"] = Query(...)):  # noqa: ANN202
        auth.authenticate(request, "certificates:write")
        cert_store.deactivate(sessions, company_id=company_id, environment=environment, now=clock.now())
        from fastapi import Response as _R

        return _R(status_code=204)

    @app.get("/internal/v1/companies/{company_id}/municipal-parameters/{municipio}")
    def municipal_parameters(company_id: str, municipio: str, request: Request,
                             environment: Literal["restricted", "production"] = Query(...),
                             cnpj: str = Query(..., pattern=r"^[0-9A-Z]{12}[0-9]{2}$")) -> dict:
        auth.authenticate(request, "integration:read")
        if not (municipio.isdigit() and len(municipio) == 7):
            raise ProblemError(422, "MUNICIPIO_INVALIDO", "Código IBGE com 7 dígitos.")
        prov: OfficialProvider = providers[environment]  # type: ignore[assignment]
        try:
            return {"environment": environment, "municipio": municipio,
                    **prov.municipal_parameters(CompanyContext(company_id, cnpj, environment), municipio)}
        except ProviderBlocked as exc:
            raise ProblemError(409, exc.code, exc.message)
        except (TransmissionNotSent, TransmissionUncertain) as exc:
            raise ProblemError(503, "PARAMETRIZACAO_UNAVAILABLE", exc.code)

    @app.post("/internal/v1/submissions")
    def submit(body: SubmissionIn, request: Request) -> dict:
        auth.authenticate(request, "submissions:write")
        if canonical_hash(body.payload) != body.payload_hash:
            raise ProblemError(422, "PAYLOAD_HASH_MISMATCH", "Hash informado não corresponde ao payload.")
        return connector.submit(body.model_dump(), body.payload_hash)

    @app.get("/internal/v1/submissions/{submission_id}")
    def get_submission(submission_id: str, request: Request, reconcile: bool = Query(True)) -> dict:
        auth.authenticate(request, "submissions:read")
        return connector.get(submission_id, reconcile)

    @app.post("/internal/v1/events")
    def register_event(body: EventIn, request: Request) -> dict:
        auth.authenticate(request, "events:write")
        return events.register(body.model_dump())

    @app.get("/internal/v1/events/{request_id}")
    def get_event(request_id: str, request: Request, reconcile: bool = Query(True)) -> dict:
        auth.authenticate(request, "events:read")
        return events.get(request_id, reconcile)

    @app.get("/internal/v1/companies/{company_id}/nfse/{access_key}/events/{event_code}")
    def query_event_by_type(company_id: str, access_key: str, event_code: str, request: Request,
                            environment: str = Query(...), cnpj: str = Query(..., pattern=r"^[0-9A-Z]{12}[0-9]{2}$")) -> dict:
        """Consulta (somente leitura) de um evento da nota por tipo — acompanhamento da análise fiscal (105104/105105)."""
        import base64 as _b64
        import gzip as _gz

        auth.authenticate(request, "events:read")
        if event_code not in ("105104", "105105", "101101", "101103", "105102") or len(access_key) != 50 \
                or not access_key.isdigit():
            raise ProblemError(422, "EVENT_QUERY_INVALID", "Consulta de evento inválida.")
        provider = providers.get(environment)
        if provider is None:
            raise ProblemError(422, "UNKNOWN_ENVIRONMENT", "Ambiente desconhecido.")
        company = CompanyContext(company_id, cnpj, environment)
        try:
            q = provider.query_event(company, access_key, event_code)
        except ProviderBlocked as exc:
            return {"state": "BLOCKED", "found": None, "error": {"code": exc.code, "message": exc.message}}
        except (TransmissionNotSent, TransmissionUncertain) as exc:
            return {"state": "UNAVAILABLE", "found": None, "error": {"code": exc.code, "message": "Consulta indisponível."}}
        return {"state": "OK", "found": bool(q.found), "simulated": provider.simulated,
                "event_xml_gzip_b64": _b64.b64encode(_gz.compress(q.event_xml)).decode() if q.found and q.event_xml else None}

    @app.post("/internal/v1/distribution/fetch")
    def distribution(body: DistributionIn, request: Request) -> dict:
        auth.authenticate(request, "distribution:read")
        provider = providers[body.environment]
        company = CompanyContext(body.company_id, body.cnpj, body.environment)
        base = {"simulated": provider.simulated, "items": [], "last_nsu": body.from_nsu, "max_nsu": None,
                "has_more": False, "retry_after_seconds": None, "error": None}
        try:
            provider.check_ready(company, "distribution")
            page = provider.fetch_distribution(company, body.from_nsu)
        except ProviderBlocked as exc:
            return {**base, "status": exc.status, "error": {"code": exc.code, "message": exc.message}}
        except (TransmissionNotSent, TransmissionUncertain) as exc:
            return {**base, "status": "ERROR", "error": {"code": exc.code, "message": "Falha na consulta à distribuição."},
                    "retry_after_seconds": 900}
        return {**base, "status": "OK", "items": page.items, "last_nsu": page.last_nsu, "max_nsu": page.max_nsu,
                "has_more": page.has_more, "retry_after_seconds": page.retry_after_seconds}

    return app
