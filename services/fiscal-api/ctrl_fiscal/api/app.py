"""Fábrica da fiscal-api."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator

import httpx
from fastapi import Depends, FastAPI, Header, Request
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ctrl_common.clock import Clock, SystemClock
from ctrl_common.db import make_engine, make_sessionmaker
from ctrl_common.problems import ProblemError, install_problem_handlers, problem_response
from ctrl_common.tokens import AccessTokenVerifier, TokenError
from ctrl_common.web import BodySizeLimitMiddleware, TraceMiddleware

from ..clients import CompanyInfo, CoreClient, UpstreamError
from ..domain.common import DomainError, canonical_hash
from ..models import IdempotencyKey
from ..settings import FiscalSettings

API_PREFIX = "/api/fiscal/v1"


@dataclass
class FiscalContext:
    settings: FiscalSettings
    sessions: sessionmaker
    clock: Clock
    verifier: AccessTokenVerifier
    core: CoreClient


@dataclass
class CompanyCtx:
    company: CompanyInfo
    user_id: str
    permissions: list[str]
    session_id: str | None = None


def ctx(request: Request) -> FiscalContext:
    return request.app.state.ctx


def db_session(request: Request) -> Iterator[Session]:
    with ctx(request).sessions() as s:
        yield s


def company_ctx(permission: str) -> Callable[..., CompanyCtx]:
    """Valida JWT e confere a autorização ATUAL no core (não confia no token)."""

    def dep(company_id: str, request: Request) -> CompanyCtx:
        c = ctx(request)
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise ProblemError(401, "UNAUTHENTICATED", "Token de acesso ausente.", headers={"WWW-Authenticate": "Bearer"})
        try:
            claims = c.verifier.verify(auth[7:].strip())
        except TokenError:
            raise ProblemError(401, "INVALID_TOKEN", "Token de acesso inválido ou expirado.", headers={"WWW-Authenticate": "Bearer"})
        if len(company_id) != 36:
            raise ProblemError(403, "COMPANY_FORBIDDEN", "Usuário sem acesso a esta empresa.")
        try:
            authz = c.core.authorize(claims.user_id, claims.session_id, company_id)
        except UpstreamError as exc:
            if exc.status == 401:
                raise ProblemError(401, exc.code, "Sessão inválida ou encerrada.")
            if exc.status == 403:
                raise ProblemError(403, "COMPANY_FORBIDDEN", "Usuário sem acesso a esta empresa.")
            raise ProblemError(503, "AUTHZ_UNAVAILABLE", "Serviço de autorização indisponível.")
        if authz.company.id != company_id:
            raise ProblemError(403, "COMPANY_FORBIDDEN", "Usuário sem acesso a esta empresa.")
        if permission not in authz.permissions:
            raise ProblemError(403, "PERMISSION_DENIED", f"Permissão necessária: {permission}.")
        return CompanyCtx(company=authz.company, user_id=claims.user_id, permissions=authz.permissions,
                          session_id=claims.session_id)

    return dep


def require_idempotency_key(
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", description="Obrigatório; até 128 caracteres [A-Za-z0-9-_:.]."),
) -> str:
    key = (idempotency_key or "").strip()
    if not key or len(key) > 128 or not all(ch.isalnum() or ch in "-_:." for ch in key):
        raise ProblemError(422, "IDEMPOTENCY_KEY_REQUIRED", "Cabeçalho Idempotency-Key obrigatório (até 128 caracteres seguros).")
    return key


def run_idempotent(
    s: Session, cc: CompanyCtx, operation: str, key: str, payload: object, now, action: Callable[[], tuple[int, dict]]  # noqa: ANN001
) -> tuple[int, dict]:
    """Mesma chave + mesmo payload => mesmo resultado; payload diferente => 409."""
    request_hash = canonical_hash({"op": operation, "payload": payload})

    def lookup() -> IdempotencyKey | None:
        return s.scalar(select(IdempotencyKey).where(
            IdempotencyKey.user_id == cc.user_id, IdempotencyKey.company_id == cc.company.id,
            IdempotencyKey.operation == operation, IdempotencyKey.key == key,
        ))

    existing = lookup()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise ProblemError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency-Key já usada com outro conteúdo.")
        return existing.response_status, existing.response_body
    status, body = action()
    s.add(IdempotencyKey(user_id=cc.user_id, company_id=cc.company.id, operation=operation, key=key,
                         request_hash=request_hash, response_status=status, response_body=body, created_at=now))
    try:
        s.commit()
    except IntegrityError:
        # Requisição concorrente com a mesma chave venceu: descarta esta e devolve a dela.
        s.rollback()
        existing = lookup()
        if existing is None or existing.request_hash != request_hash:
            raise ProblemError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency-Key já usada com outro conteúdo.")
        return existing.response_status, existing.response_body
    return status, body


def create_app(settings: FiscalSettings, clock: Clock | None = None, core_http: httpx.Client | None = None) -> FastAPI:
    settings.validate()
    engine = make_engine(settings.database_url)
    if core_http is None:
        core_http = httpx.Client(base_url=settings.core_api_url, timeout=httpx.Timeout(5.0))
    clock = clock or SystemClock()
    context = FiscalContext(
        settings=settings,
        sessions=make_sessionmaker(engine),
        clock=clock,
        verifier=AccessTokenVerifier(settings.jwt_public_key_pem, clock=clock),
        core=CoreClient(core_http, settings.core_service_name, settings.core_api_key),
    )
    docs = settings.enable_docs
    app = FastAPI(
        title="CTRL Fiscal — Fiscal API",
        version="0.1.0",
        description=(
            "NFS-e: documentos emitidos/recebidos, rascunhos, lotes agendados, importação, exportação e "
            "sincronização ADN. Ambiente `mock` é SIMULAÇÃO local, sem validade fiscal."
        ),
        docs_url=f"{API_PREFIX}/docs" if docs else None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json" if docs else None,
    )
    app.state.ctx = context
    app.state.engine = engine
    install_problem_handlers(app)

    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError):  # noqa: ANN202
        return problem_response(request, exc.status, exc.code, exc.detail, exc.errors)

    app.add_middleware(
        BodySizeLimitMiddleware, default_limit=256 * 1024, overrides={"/documents/import": settings.upload_max_bytes + 64 * 1024}
    )
    app.add_middleware(TraceMiddleware)

    from .routes import router

    app.include_router(router, prefix=API_PREFIX)

    @app.get("/health", include_in_schema=False)
    def health() -> dict:
        return {"status": "ok", "service": "fiscal-api"}

    @app.get("/ready", include_in_schema=False)
    def ready() -> dict:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            raise ProblemError(503, "DATABASE_UNAVAILABLE", "Banco indisponível.")
        return {"status": "ready", "service": "fiscal-api"}

    return app


__all__ = ["create_app", "company_ctx", "db_session", "ctx", "CompanyCtx", "Depends"]
