from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ctrl_common.problems import ProblemError, problem_responses, trace_id_for
from ctrl_common.service_auth import service_headers
from ctrl_common.web import Page, PageParams

from .app import CompanyAccess, CurrentUser, audit, company_access, ctx, current_user, db_session
from .models import Company, Customer, Membership
from .schemas import CompanyOut, CustomerCreate, CustomerOut, CustomerPatch, IntegrationStatus

router = APIRouter(tags=["companies"])
ERRS = problem_responses(401, 403, 404, 409, 422)


def customer_out(c: Customer) -> CustomerOut:
    return CustomerOut(
        id=c.id, company_id=c.company_id, name=c.name, document_type=c.document_type, document=c.document,
        email=c.email, phone=c.phone, municipal_registration=c.municipal_registration, municipality_code=c.municipality_code,
        postal_code=c.postal_code, address=c.address, is_active=c.is_active, version=c.version,
        created_at=c.created_at, updated_at=c.updated_at,
    )


@router.get("/companies", response_model=Page[CompanyOut], responses=problem_responses(401, 422))
def list_companies(
    pp: PageParams = Depends(), cu: CurrentUser = Depends(current_user), s: Session = Depends(db_session)
) -> Page[CompanyOut]:
    base = (
        select(Company, Membership)
        .join(Membership, Membership.company_id == Company.id)
        .where(Membership.user_id == cu.user.id, Company.is_active.is_(True))
    )
    total = s.scalar(select(func.count()).select_from(base.subquery()))
    rows = s.execute(base.order_by(Company.name, Company.id).offset(pp.offset).limit(pp.page_size)).all()
    return Page[CompanyOut](
        items=[
            CompanyOut(id=c.id, name=c.name, cnpj=c.cnpj, timezone=c.timezone, environment=c.nfse_environment,
                       permissions=list(m.permissions or []))
            for c, m in rows
        ],
        page=pp.page, page_size=pp.page_size, total=total,
    )


@router.get("/companies/{company_id}/customers", response_model=Page[CustomerOut], responses=ERRS)
def list_customers(
    company_id: str,
    query: str | None = Query(None, max_length=100, description="Busca por nome ou documento."),
    active: bool | None = Query(None),
    pp: PageParams = Depends(),
    acc: CompanyAccess = Depends(company_access("view")),
    s: Session = Depends(db_session),
) -> Page[CustomerOut]:
    q = select(Customer).where(Customer.company_id == acc.company.id)
    if query:
        like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        q = q.where(or_(Customer.name.like(like, escape="\\"), Customer.document.like(like, escape="\\")))
    if active is not None:
        q = q.where(Customer.is_active.is_(active))
    total = s.scalar(select(func.count()).select_from(q.subquery()))
    items = s.scalars(q.order_by(Customer.name, Customer.id).offset(pp.offset).limit(pp.page_size)).all()
    return Page[CustomerOut](items=[customer_out(c) for c in items], page=pp.page, page_size=pp.page_size, total=total)


@router.post("/companies/{company_id}/customers", status_code=201, response_model=CustomerOut, responses=ERRS)
def create_customer(
    company_id: str,
    body: CustomerCreate,
    request: Request,
    acc: CompanyAccess = Depends(company_access("prepare")),
    s: Session = Depends(db_session),
) -> CustomerOut:
    now = ctx(request).clock.now()
    c = Customer(
        company_id=acc.company.id, name=body.name.strip(), document_type=body.document_type, document=body.document,
        email=body.email, phone=body.phone, municipal_registration=body.municipal_registration,
        municipality_code=body.municipality_code, postal_code=body.postal_code,
        address=body.address.model_dump() if body.address else None,
        is_active=True, version=1, created_at=now, updated_at=now,
    )
    s.add(c)
    try:
        s.flush()
    except IntegrityError:
        s.rollback()
        raise ProblemError(409, "CUSTOMER_DUPLICATE", "Já existe cliente com este documento nesta empresa.")
    audit(s, now, "customer.created", actor_id=acc.user.id, company_id=acc.company.id, target_type="customer",
          target_id=c.id, trace_id=trace_id_for(request))
    s.commit()
    return customer_out(c)


def _get_customer(s: Session, company_id: str, customer_id: str, lock: bool = False) -> Customer:
    q = select(Customer).where(Customer.id == customer_id, Customer.company_id == company_id)
    if lock:
        q = q.with_for_update()
    c = s.scalar(q)
    if c is None:
        raise ProblemError(404, "CUSTOMER_NOT_FOUND", "Cliente não encontrado.")
    return c


@router.get("/companies/{company_id}/customers/{customer_id}", response_model=CustomerOut, responses=ERRS)
def get_customer(
    company_id: str, customer_id: str, acc: CompanyAccess = Depends(company_access("view")), s: Session = Depends(db_session)
) -> CustomerOut:
    return customer_out(_get_customer(s, acc.company.id, customer_id))


@router.patch("/companies/{company_id}/customers/{customer_id}", response_model=CustomerOut, responses=ERRS)
def patch_customer(
    company_id: str,
    customer_id: str,
    body: CustomerPatch,
    request: Request,
    acc: CompanyAccess = Depends(company_access("prepare")),
    s: Session = Depends(db_session),
) -> CustomerOut:
    c = _get_customer(s, acc.company.id, customer_id, lock=True)
    if body.expected_version is not None and body.expected_version != c.version:
        raise ProblemError(409, "VERSION_CONFLICT", "Cliente alterado por outra pessoa. Recarregue antes de salvar.")
    changes = body.model_dump(exclude_unset=True, exclude={"expected_version"})
    for key, value in changes.items():
        if key == "name" and value is None:
            raise ProblemError(422, "VALIDATION_ERROR", "Nome não pode ser vazio.")
        setattr(c, key, value)
    now = ctx(request).clock.now()
    c.version += 1
    c.updated_at = now
    audit(s, now, "customer.updated", actor_id=acc.user.id, company_id=acc.company.id, target_type="customer",
          target_id=c.id, details={"fields": sorted(changes.keys())}, trace_id=trace_id_for(request))
    s.commit()
    return customer_out(c)


@router.get(
    "/companies/{company_id}/integration-status",
    response_model=IntegrationStatus,
    responses=problem_responses(401, 403, 503),
    summary="Estado público do certificado/integração NFS-e. Nunca retorna segredo.",
)
def integration_status(
    company_id: str, request: Request,
    environment: str | None = Query(None, pattern="^(mock|restricted|production)$",
                                    description="Padrão: ambiente de emissão. 'production' = leitura ADN (somente leitura)."),
    acc: CompanyAccess = Depends(company_access("view")),
) -> IntegrationStatus:
    c = ctx(request)
    env = environment or acc.company.nfse_environment
    if env not in {acc.company.nfse_environment, *(acc.company.adn_environments or [])}:
        raise ProblemError(403, "ENVIRONMENT_NOT_ENABLED", "Ambiente não habilitado para esta empresa.")
    if c.nfse_http is None or not c.settings.nfse_api_key:
        raise ProblemError(503, "INTEGRATION_UNAVAILABLE", "Serviço de integração NFS-e indisponível.")
    try:
        r = c.nfse_http.get(
            f"/internal/v1/companies/{acc.company.id}/integration-status",
            params={"environment": env, "cnpj": acc.company.cnpj},
            headers=service_headers("core-api", c.settings.nfse_api_key),
        )
    except httpx.HTTPError:
        raise ProblemError(503, "INTEGRATION_UNAVAILABLE", "Serviço de integração NFS-e indisponível.")
    if r.status_code != 200:
        raise ProblemError(503, "INTEGRATION_UNAVAILABLE", "Serviço de integração NFS-e indisponível.")
    return IntegrationStatus.model_validate(r.json())
