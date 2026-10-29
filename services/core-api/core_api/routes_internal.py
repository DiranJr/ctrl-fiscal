"""Rotas internas (não roteadas pelo gateway e exigindo identidade de serviço)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ctrl_common.problems import ProblemError

from .app import ctx, db_session
from .models import Company, Customer, Membership, RefreshSession, User
from .routes_companies import customer_out

router = APIRouter(prefix="/internal/v1", include_in_schema=False)


def _company_dict(c: Company) -> dict:
    from sqlalchemy.orm import object_session

    from .models import Workspace
    from .seed import DEMO_CNPJS

    s = object_session(c)
    w = s.scalar(select(Workspace).where(Workspace.company_id == c.id)) if s is not None else None
    return {
        "workspace_id": w.id if w else None, "workspace_status": w.status if w else None,
        "is_demo": c.cnpj in DEMO_CNPJS,  # empresas de demonstração nunca usam ambientes oficiais
        "id": c.id, "name": c.name, "cnpj": c.cnpj, "timezone": c.timezone,
        "environment": c.nfse_environment, "municipality_code": c.municipality_code, "is_active": c.is_active,
        "adn_environments": list(c.adn_environments or [c.nfse_environment]),
        "dps_series": c.dps_series,
        "fiscal_profile": {
            "municipal_registration": c.municipal_registration, "simples_option": c.simples_option,
            "simples_regime": c.simples_regime, "special_regime": c.special_regime, "phone": c.phone, "email": c.email,
            "source": c.fiscal_profile_source,
        },
    }


@router.get("/authz")
def authz(
    request: Request,
    user_id: str = Query(..., max_length=36),
    session_id: str = Query(..., max_length=36),
    company_id: str = Query(..., max_length=36),
    s: Session = Depends(db_session),
) -> dict:
    """Autorização vigente: usuário ativo, sessão não revogada e associação atual."""
    c = ctx(request)
    c.service_auth.authenticate(request, "authz")
    now = c.clock.now()
    user = s.get(User, user_id)
    sess = s.get(RefreshSession, session_id)
    if user is None or not user.is_active:
        raise ProblemError(401, "INVALID_TOKEN", "Usuário inativo ou inexistente.")
    if sess is None or sess.user_id != user.id or sess.revoked_at is not None or sess.absolute_expires_at <= now:
        raise ProblemError(401, "SESSION_REVOKED", "Sessão encerrada.")
    m = s.scalar(select(Membership).where(Membership.user_id == user_id, Membership.company_id == company_id))
    company = s.get(Company, company_id) if m else None
    if m is None or company is None or not company.is_active:
        raise ProblemError(403, "COMPANY_FORBIDDEN", "Usuário sem acesso a esta empresa.")
    return {
        "user": {"id": user.id, "name": user.name, "email": user.email},
        "company": _company_dict(company),
        "permissions": list(m.permissions or []),
    }


@router.get("/authz/job")
def authz_job(
    request: Request,
    user_id: str = Query(..., max_length=36),
    company_id: str = Query(..., max_length=36),
    s: Session = Depends(db_session),
) -> dict:
    """Autorização ATUAL de quem pediu um job/lote, conferida pelo worker antes de transmitir.

    Sem sessão (o agendamento sobrevive ao login), mas com o estado vigente: usuário ativo, empresa ativa e vínculo
    com as permissões de hoje. Desativação ou retirada de permissão depois do agendamento bloqueia a transmissão."""
    c = ctx(request)
    c.service_auth.authenticate(request, "authz:job")
    user = s.get(User, user_id)
    if user is None or not user.is_active:
        raise ProblemError(403, "USER_INACTIVE", "Usuário inativo ou inexistente.")
    m = s.scalar(select(Membership).where(Membership.user_id == user_id, Membership.company_id == company_id))
    company = s.get(Company, company_id) if m else None
    if m is None or company is None or not company.is_active:
        raise ProblemError(403, "COMPANY_FORBIDDEN", "Usuário sem acesso a esta empresa.")
    from .routes_workspaces import workspace_of

    w = workspace_of(s, company.id)
    # Contexto vigente conferido antes de transmitir em produção: workspace ativo e ambiente de emissão atual.
    return {"user_id": user.id, "company_id": company.id, "permissions": list(m.permissions or []),
            "workspace_status": w.status if w else None, "environment": company.nfse_environment}


@router.get("/companies")
def companies(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    s: Session = Depends(db_session),
) -> dict:
    ctx(request).service_auth.authenticate(request, "companies:read")
    q = select(Company).where(Company.is_active.is_(True))
    total = s.scalar(select(func.count()).select_from(q.subquery()))
    items = s.scalars(q.order_by(Company.id).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [_company_dict(c) for c in items], "page": page, "page_size": page_size, "total": total}


@router.get("/companies/{company_id}/customers")
def customer_by_document(
    company_id: str, request: Request, document: str = Query(..., min_length=11, max_length=14),
    s: Session = Depends(db_session),
) -> dict:
    ctx(request).service_auth.authenticate(request, "customers:read")
    c = s.scalar(select(Customer).where(Customer.company_id == company_id, Customer.document == document.upper()))
    if c is None:
        raise ProblemError(404, "CUSTOMER_NOT_FOUND", "Cliente não encontrado.")
    return customer_out(c).model_dump(mode="json")


@router.get("/companies/{company_id}/customers/{customer_id}")
def customer(company_id: str, customer_id: str, request: Request, s: Session = Depends(db_session)) -> dict:
    ctx(request).service_auth.authenticate(request, "customers:read")
    c = s.scalar(select(Customer).where(Customer.id == customer_id, Customer.company_id == company_id))
    if c is None:
        raise ProblemError(404, "CUSTOMER_NOT_FOUND", "Cliente não encontrado.")
    return customer_out(c).model_dump(mode="json")


@router.get("/companies/{company_id}/parametrizacao-iss")
def parametrizacao_iss(
    company_id: str, request: Request,
    municipio: str = Query(..., pattern=r"^[0-9]{7}$"),
    servico: str | None = Query(None, pattern=r"^[0-9]{2}\.[0-9]{2}\.[0-9]{2}\.[0-9]{3}$"),
    competencia: str | None = Query(None, pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$"),
    s: Session = Depends(db_session),
) -> dict:
    """Convênio do município de incidência e alíquota parametrizada no ambiente do workspace (leitura oficial)."""
    from .parametrizacao_iss import consultar
    from .seed import DEMO_CNPJS

    c = ctx(request)
    c.service_auth.authenticate(request, "parametrizacao:read")
    company = s.get(Company, company_id)
    if company is None:
        raise ProblemError(404, "COMPANY_NOT_FOUND", "Empresa não encontrada.")
    now = c.clock.now()
    if company.nfse_environment not in ("restricted", "production") or company.cnpj in DEMO_CNPJS:
        return {"environment": company.nfse_environment, "municipio": municipio, "consulted_at": now.isoformat(),
                "convenio": {"status": "NOT_APPLICABLE", "detail": "SIMULACAO"},
                "aliquota": {"status": "NOT_APPLICABLE", "rate": None, "servico": servico, "competencia": competencia}}
    return consultar(c.nfse_http, c.settings.nfse_api_key, company_id=company.id, cnpj=company.cnpj,
                     environment=company.nfse_environment, municipio=municipio, servico=servico,
                     competencia=competencia, now=now)
