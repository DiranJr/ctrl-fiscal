"""Fábrica do core-api: autenticação, empresas, clientes e autorização interna."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

import httpx
from fastapi import Depends, FastAPI, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from ctrl_common.clock import Clock, SystemClock
from ctrl_common.db import make_engine, make_sessionmaker
from ctrl_common.problems import ProblemError, install_problem_handlers
from ctrl_common.service_auth import ServiceAuthenticator
from ctrl_common.tokens import AccessTokenVerifier, TokenError
from ctrl_common.web import BodySizeLimitMiddleware, TraceMiddleware

from .models import AuditEvent, Company, Membership, RefreshSession, User
from .security import AccessTokenIssuer
from .settings import CoreSettings

API_PREFIX = "/api/core/v1"


@dataclass
class CoreContext:
    settings: CoreSettings
    sessions: sessionmaker
    clock: Clock
    issuer: AccessTokenIssuer
    verifier: AccessTokenVerifier
    service_auth: ServiceAuthenticator
    nfse_http: httpx.Client | None


def ctx(request: Request) -> CoreContext:
    return request.app.state.ctx


def db_session(request: Request) -> Iterator[Session]:
    with ctx(request).sessions() as s:
        yield s


@dataclass
class CurrentUser:
    user: User
    session_id: str


def current_user(request: Request, s: Session = Depends(db_session)) -> CurrentUser:
    c = ctx(request)
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise ProblemError(401, "UNAUTHENTICATED", "Token de acesso ausente.", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = c.verifier.verify(auth[7:].strip())
    except TokenError:
        raise ProblemError(401, "INVALID_TOKEN", "Token de acesso inválido ou expirado.", headers={"WWW-Authenticate": "Bearer"})
    user = s.get(User, claims.user_id)
    sess = s.get(RefreshSession, claims.session_id)
    now = c.clock.now()
    if user is None or not user.is_active:
        raise ProblemError(401, "INVALID_TOKEN", "Usuário inativo ou inexistente.")
    if sess is None or sess.user_id != user.id or sess.revoked_at is not None or sess.absolute_expires_at <= now:
        raise ProblemError(401, "SESSION_REVOKED", "Sessão encerrada. Entre novamente.")
    return CurrentUser(user=user, session_id=sess.id)


@dataclass
class CompanyAccess:
    user: User
    company: Company
    permissions: list[str]


def company_access(permission: str):  # noqa: ANN201
    """Confere a associação atual (no banco) entre usuário e empresa da rota."""

    def dep(company_id: str, cu: CurrentUser = Depends(current_user), s: Session = Depends(db_session)) -> CompanyAccess:
        m = s.scalar(
            select(Membership).where(Membership.user_id == cu.user.id, Membership.company_id == company_id)
        )
        company = s.get(Company, company_id) if m else None
        if m is None or company is None or not company.is_active:
            raise ProblemError(403, "COMPANY_FORBIDDEN", "Usuário sem acesso a esta empresa.")
        perms = list(m.permissions or [])
        if permission not in perms:
            raise ProblemError(403, "PERMISSION_DENIED", f"Permissão necessária: {permission}.")
        return CompanyAccess(user=cu.user, company=company, permissions=perms)

    return dep


def audit(
    s: Session,
    now: datetime,
    action: str,
    *,
    actor_type: str = "user",
    actor_id: str | None = None,
    company_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict | None = None,
    trace_id: str | None = None,
) -> None:
    s.add(
        AuditEvent(
            occurred_at=now,
            actor_type=actor_type,
            actor_id=actor_id,
            company_id=company_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
            trace_id=trace_id,
        )
    )


def create_app(settings: CoreSettings, clock: Clock | None = None, nfse_http: httpx.Client | None = None) -> FastAPI:
    settings.validate()
    engine = make_engine(settings.database_url)
    if nfse_http is None and settings.nfse_api_url and settings.nfse_api_key:
        nfse_http = httpx.Client(base_url=settings.nfse_api_url, timeout=httpx.Timeout(5.0))
    clock = clock or SystemClock()
    context = CoreContext(
        settings=settings,
        sessions=make_sessionmaker(engine),
        clock=clock,
        issuer=AccessTokenIssuer(settings.jwt_private_key_pem, settings.jwt_kid, settings.access_ttl_seconds),
        verifier=AccessTokenVerifier(settings.jwt_public_key_pem, clock=clock),
        service_auth=ServiceAuthenticator(settings.internal_clients),
        nfse_http=nfse_http,
    )
    docs = settings.enable_docs
    app = FastAPI(
        title="CTRL Fiscal — Core API",
        version="0.1.0",
        description="Autenticação, empresas, clientes e autorização. Contrato canônico para o frontend.",
        docs_url=f"{API_PREFIX}/docs" if docs else None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json" if docs else None,
    )
    app.state.ctx = context
    app.state.engine = engine
    install_problem_handlers(app)
    app.add_middleware(BodySizeLimitMiddleware, default_limit=64 * 1024, overrides={"/certificate": 192 * 1024})
    app.add_middleware(TraceMiddleware)

    from .routes_admin import router as admin_router
    from .routes_auth import router as auth_router
    from .routes_companies import router as companies_router
    from .routes_fiscal_profile import router as profile_router
    from .routes_internal import router as internal_router

    app.include_router(auth_router, prefix=API_PREFIX)
    app.include_router(admin_router, prefix=API_PREFIX)
    app.include_router(companies_router, prefix=API_PREFIX)
    app.include_router(profile_router, prefix=API_PREFIX)
    from .routes_workspaces import router as workspaces_router

    app.include_router(workspaces_router, prefix=API_PREFIX)
    from .routes_perfis import internal as perfis_internal, router as perfis_router

    app.include_router(perfis_router, prefix=API_PREFIX)
    app.include_router(perfis_internal)
    app.include_router(internal_router)

    @app.get("/health", include_in_schema=False)
    def health() -> dict:
        return {"status": "ok", "service": "core-api"}

    @app.get("/ready", include_in_schema=False)
    def ready() -> dict:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            raise ProblemError(503, "DATABASE_UNAVAILABLE", "Banco indisponível.")
        return {"status": "ready", "service": "core-api"}

    return app
