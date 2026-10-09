"""Perfis de serviço do workspace (revisão pelo usuário autorizado) e rotas internas usadas pelo fiscal-api
(preparação a partir das notas emitidas e consulta do perfil aplicado a um rascunho)."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ctrl_common import dominios
from ctrl_common.problems import ProblemError, problem_responses, trace_id_for

from . import perfis_servico as ps
from .app import CompanyAccess, audit, company_access, ctx, db_session
from .models import Company, ServiceProfile, ServiceProfileVersion

router = APIRouter(tags=["service-profiles"])
internal = APIRouter(prefix="/internal/v1", include_in_schema=False)
ERRS = problem_responses(401, 403, 404, 409, 422)


class Strict(BaseModel):
    model_config = {"extra": "forbid"}


class IbsCbsRegular(Strict):
    cst: str = Field(pattern=r"^[0-9]{3}$")
    class_code: str = Field(pattern=r"^[0-9]{6}$")


class IbsCbsProfile(Strict):
    operation_indicator: str = Field(pattern=r"^[0-9]{6}$", description="cIndOp (Anexo C)")
    cst: str = Field(pattern=r"^[0-9]{3}$", description="CST = 3 primeiros dígitos do cClassTrib (E0959)")
    class_code: str = Field(pattern=r"^[0-9]{6}$", description="cClassTrib (tabela oficial para NFS-e)")
    personal_use: str | None = Field(None, pattern="^[01]$", description="indFinal")
    regular: IbsCbsRegular | None = Field(None, description="gTribRegular, só quando a classificação exige")


class ServiceProfileIn(Strict):
    name: str = Field(min_length=2, max_length=120)
    national_code: str = Field(pattern=r"^[0-9]{6}$")
    municipal_code: str | None = Field(None, pattern=r"^[0-9A-Za-z]{1,20}$")
    nbs_code: str | None = Field(None, pattern=r"^[0-9]{9}$")
    description: str | None = Field(None, max_length=2000)
    issqn_taxation: str | None = Field(None, pattern="^(TAXABLE|IMMUNE|EXPORT|NOT_INCIDENT)$")
    pis_cofins_cst: str | None = Field(None, pattern=r"^[0-9]{2}$")
    ibs_cbs: IbsCbsProfile | None = None
    source: str | None = Field(None, max_length=300)
    valid_from: date | None = None
    valid_to: date | None = None


class ServiceProfilePatch(ServiceProfileIn):
    name: str | None = Field(None, min_length=2, max_length=120)  # type: ignore[assignment]
    national_code: str | None = Field(None, pattern=r"^[0-9]{6}$")  # type: ignore[assignment]
    is_active: bool | None = Field(None, description="Compatibilidade: false inativa, true reativa (volta à revisão).")
    expected_version: int = Field(ge=1)


class VersionRef(Strict):
    expected_version: int = Field(ge=1)


class ApproveIn(VersionRef):
    valid_from: date | None = Field(None, description="Início da vigência (padrão: hoje).")
    note: str | None = Field(None, max_length=500)


def profile_out(p: ServiceProfile, company: Company | None, hoje: date) -> dict:
    pend = ps.pendencias(p, company, hoje)
    return {"id": p.id, "name": p.name, "national_code": p.national_code, "municipal_code": p.municipal_code,
            "nbs_code": p.nbs_code, "description": p.description, "issqn_taxation": p.issqn_taxation,
            "pis_cofins_cst": p.pis_cofins_cst, "ibs_cbs": p.ibs_cbs, "source": p.source, "is_active": p.is_active,
            "status": p.status, "origin_key": p.origin_key, "evidence": p.evidence, "field_sources": p.field_sources or {},
            "suggestions": p.suggestions or [], "valid_from": p.valid_from, "valid_to": p.valid_to,
            "approved_by": p.approved_by, "approved_at": p.approved_at, "version": p.version, "updated_at": p.updated_at,
            "descriptions": ps.descricoes(p), "pending": pend,
            "can_approve": p.status == "PENDING_REVIEW" and not [i for i in pend if i["severity"] == "error"]}


def _check_codes(data: dict) -> None:
    """Formato/existência nas tabelas oficiais na gravação (a compatibilidade completa vira pendência)."""
    errs = []
    if data.get("national_code") and not dominios.servico_nacional(data["national_code"]):
        errs.append({"field": "national_code", "code": "E0310", "message": "cTribNac não existe na lista nacional (Anexo B)."})
    if data.get("nbs_code") and not dominios.nbs(data["nbs_code"]):
        errs.append({"field": "nbs_code", "code": "E0316", "message": "cNBS não existe na tabela NBS (Anexo B)."})
    ibs = data.get("ibs_cbs")
    if ibs:
        if not dominios.cindop_valido(ibs["operation_indicator"]):
            errs.append({"field": "ibs_cbs.operation_indicator", "code": "E0901", "message": "cIndOp fora do Anexo C."})
        c = ps.classificacao(ibs["class_code"])
        if c is None:
            errs.append({"field": "ibs_cbs.class_code", "code": "E0958",
                         "message": "cClassTrib inexistente na tabela oficial para NFS-e."})
        elif c["cst"] != ibs["cst"]:
            errs.append({"field": "ibs_cbs.cst", "code": "E0959", "message": "CST deve ser os 3 primeiros dígitos do cClassTrib."})
    if data.get("valid_from") and data.get("valid_to") and data["valid_to"] < data["valid_from"]:
        errs.append({"field": "valid_to", "code": "VIGENCIA", "message": "Fim da vigência anterior ao início."})
    if errs:
        raise ProblemError(422, "SERVICE_PROFILE_INVALID", "Perfil de serviço inválido.", errors=errs)


def _get(s: Session, company_id: str, profile_id: str, lock: bool = True) -> ServiceProfile:
    q = select(ServiceProfile).where(ServiceProfile.id == profile_id, ServiceProfile.company_id == company_id)
    p = s.scalar(q.with_for_update() if lock else q)
    if p is None:
        raise ProblemError(404, "SERVICE_PROFILE_NOT_FOUND", "Perfil de serviço não encontrado.")
    return p


def _versao(p: ServiceProfile, expected: int) -> None:
    if expected != p.version:
        raise ProblemError(409, "VERSION_CONFLICT", "Perfil alterado por outra pessoa. Recarregue antes de salvar.")


def _today(request: Request, company: Company) -> date:
    from zoneinfo import ZoneInfo

    return ctx(request).clock.now().astimezone(ZoneInfo(company.timezone or "America/Sao_Paulo")).date()


# ---------------------------------------------------------------- rotas públicas
@router.get("/companies/{company_id}/service-profiles", responses=ERRS,
            summary="Perfis de serviço do workspace, com descrições oficiais dos códigos, origem dos campos e pendências.")
def list_profiles(company_id: str, request: Request, acc: CompanyAccess = Depends(company_access("view")),
                  s: Session = Depends(db_session)) -> list[dict]:
    c = s.get(Company, acc.company.id)
    hoje = _today(request, c)
    rows = s.scalars(select(ServiceProfile).where(ServiceProfile.company_id == c.id)
                     .order_by(ServiceProfile.status != "PENDING_REVIEW", ServiceProfile.status, ServiceProfile.name))
    return [profile_out(p, c, hoje) for p in rows]


@router.get("/companies/{company_id}/service-profiles/{profile_id}/versions", responses=ERRS,
            summary="Histórico completo do perfil (conteúdo de cada versão, ação, responsável e horário).")
def versions(company_id: str, profile_id: str, acc: CompanyAccess = Depends(company_access("view")),
             s: Session = Depends(db_session)) -> list[dict]:
    p = _get(s, acc.company.id, profile_id, lock=False)
    return [{"version": v.version, "action": v.action, "snapshot": v.snapshot, "actor_type": v.actor_type,
             "actor_id": v.actor_id, "created_at": v.created_at}
            for v in s.scalars(select(ServiceProfileVersion).where(ServiceProfileVersion.profile_id == p.id)
                               .order_by(ServiceProfileVersion.version.desc()))]


@router.post("/companies/{company_id}/service-profiles", status_code=201, responses=ERRS,
             summary="Cadastra perfil manual (fica pendente de revisão).")
def create_profile(company_id: str, body: ServiceProfileIn, request: Request,
                   acc: CompanyAccess = Depends(company_access("admin")), s: Session = Depends(db_session)) -> dict:
    data = body.model_dump()
    _check_codes(data)
    now = ctx(request).clock.now()
    data["ibs_cbs"] = _ibs(data.get("ibs_cbs"))
    preenchidos = [k for k, v in data.items() if v not in (None, "")]
    p = ServiceProfile(company_id=acc.company.id, **data, is_active=True, status="PENDING_REVIEW", version=1,
                       field_sources=ps.fontes_usuario(preenchidos, acc.user.id, now), created_by=acc.user.id,
                       updated_by=acc.user.id, created_at=now, updated_at=now)
    s.add(p)
    s.flush()
    ps.registrar_versao(s, p, "created", now, acc.user.id)
    audit(s, now, "service_profile.created", actor_id=acc.user.id, company_id=acc.company.id,
          target_type="service_profile", target_id=p.id, details={"version": 1, "fields": preenchidos},
          trace_id=trace_id_for(request))
    s.commit()
    return profile_out(p, s.get(Company, acc.company.id), _today(request, acc.company))


def _ibs(v: dict | None) -> dict | None:
    if not v:
        return None
    return {k: x for k, x in v.items() if not (k in ("regular", "personal_use") and x in (None, {}))}


def _jsonable(v):  # noqa: ANN001, ANN202
    return v.isoformat() if isinstance(v, date) else v


@router.patch("/companies/{company_id}/service-profiles/{profile_id}", responses=ERRS,
              summary="Edita o perfil. Perfil aprovado volta a 'pendente de revisão' (nova versão).")
def patch_profile(company_id: str, profile_id: str, body: ServiceProfilePatch, request: Request,
                  acc: CompanyAccess = Depends(company_access("admin")), s: Session = Depends(db_session)) -> dict:
    p = _get(s, acc.company.id, profile_id)
    changes = body.model_dump(exclude_unset=True)
    _versao(p, changes.pop("expected_version"))
    ativo = changes.pop("is_active", None)
    if ativo is False:
        return _set_status(s, request, acc, p, "INACTIVE", "inactivated")
    if ativo is True and p.status == "INACTIVE":
        return _set_status(s, request, acc, p, "PENDING_REVIEW", "reactivated")
    if "ibs_cbs" in changes:
        changes["ibs_cbs"] = _ibs(changes["ibs_cbs"])
    merged = {k: getattr(p, k) for k in ps.CAMPOS} | changes
    _check_codes(merged)
    alterados = {k: {"de": _jsonable(getattr(p, k)), "para": _jsonable(v)} for k, v in changes.items() if getattr(p, k) != v}
    if not alterados:
        return profile_out(p, s.get(Company, acc.company.id), _today(request, acc.company))
    now = ctx(request).clock.now()
    for k in alterados:
        setattr(p, k, changes[k])
    p.field_sources = ps.fontes_usuario(list(alterados), acc.user.id, now, p.field_sources)
    estava_aprovado = p.status == "APPROVED"
    if p.status == "APPROVED":
        p.status, p.approved_by, p.approved_at = "PENDING_REVIEW", None, None
    p.version += 1
    p.updated_by, p.updated_at = acc.user.id, now
    ps.registrar_versao(s, p, "updated", now, acc.user.id)
    audit(s, now, "service_profile.updated", actor_id=acc.user.id, company_id=acc.company.id,
          target_type="service_profile", target_id=p.id,
          details={"version": p.version, "changes": alterados, "approval_revoked": estava_aprovado},
          trace_id=trace_id_for(request))
    s.commit()
    return profile_out(p, s.get(Company, acc.company.id), _today(request, acc.company))


@router.post("/companies/{company_id}/service-profiles/{profile_id}/approve", responses=ERRS,
             summary="Aprova o perfil revisado: exige que não haja pendência bloqueante; confirma a origem dos campos.")
def approve(company_id: str, profile_id: str, body: ApproveIn, request: Request,
            acc: CompanyAccess = Depends(company_access("admin")), s: Session = Depends(db_session)) -> dict:
    p = _get(s, acc.company.id, profile_id)
    _versao(p, body.expected_version)
    company = s.get(Company, acc.company.id)
    hoje = _today(request, company)
    if p.status != "PENDING_REVIEW":
        raise ProblemError(409, "SERVICE_PROFILE_NOT_PENDING", f"Perfil em {p.status}: só um perfil pendente de "
                                                               "revisão pode ser aprovado.")
    blk = ps.bloqueios(p, company, hoje)
    if blk:
        raise ProblemError(409, "SERVICE_PROFILE_HAS_PENDENCIES", "O perfil ainda tem dados obrigatórios ausentes ou "
                                                                  "incompatíveis.",
                           errors=[{"field": i["field"], "code": i["code"], "message": i["message"]} for i in blk])
    now = ctx(request).clock.now()
    preenchidos = [k for k in ps.CAMPOS if getattr(p, k) not in (None, "")]
    confirmados = {}
    for k in preenchidos:
        src = dict((p.field_sources or {}).get(k) or {"origem": "usuario"})
        src.update(confirmado_por=acc.user.id, em=now.isoformat())
        confirmados[k] = src
    p.field_sources = {**(p.field_sources or {}), **confirmados}
    p.valid_from = body.valid_from or p.valid_from or hoje
    p.status, p.approved_by, p.approved_at = "APPROVED", acc.user.id, now
    p.version += 1
    p.updated_by, p.updated_at = acc.user.id, now
    ps.registrar_versao(s, p, "approved", now, acc.user.id)
    audit(s, now, "service_profile.approved", actor_id=acc.user.id, company_id=acc.company.id,
          target_type="service_profile", target_id=p.id,
          details={"version": p.version, "valid_from": p.valid_from.isoformat(), "confirmed_fields": preenchidos,
                   "note": body.note}, trace_id=trace_id_for(request))
    s.commit()
    return profile_out(p, company, hoje)


def _set_status(s: Session, request: Request, acc: CompanyAccess, p: ServiceProfile, status: str, action: str) -> dict:
    now = ctx(request).clock.now()
    p.status, p.is_active = status, status != "INACTIVE"
    if status != "APPROVED":
        p.approved_by, p.approved_at = None, None
    p.version += 1
    p.updated_by, p.updated_at = acc.user.id, now
    ps.registrar_versao(s, p, action, now, acc.user.id)
    audit(s, now, f"service_profile.{action}", actor_id=acc.user.id, company_id=acc.company.id,
          target_type="service_profile", target_id=p.id, details={"version": p.version}, trace_id=trace_id_for(request))
    s.commit()
    return profile_out(p, s.get(Company, acc.company.id), _today(request, acc.company))


@router.post("/companies/{company_id}/service-profiles/{profile_id}/inactivate", responses=ERRS)
def inactivate(company_id: str, profile_id: str, body: VersionRef, request: Request,
               acc: CompanyAccess = Depends(company_access("admin")), s: Session = Depends(db_session)) -> dict:
    p = _get(s, acc.company.id, profile_id)
    _versao(p, body.expected_version)
    return _set_status(s, request, acc, p, "INACTIVE", "inactivated")


@router.post("/companies/{company_id}/service-profiles/{profile_id}/reactivate", responses=ERRS,
             summary="Reativa um perfil inativo: volta a 'pendente de revisão' (precisa de nova aprovação).")
def reactivate(company_id: str, profile_id: str, body: VersionRef, request: Request,
               acc: CompanyAccess = Depends(company_access("admin")), s: Session = Depends(db_session)) -> dict:
    p = _get(s, acc.company.id, profile_id)
    _versao(p, body.expected_version)
    if p.status != "INACTIVE":
        raise ProblemError(409, "SERVICE_PROFILE_NOT_INACTIVE", "Somente perfil inativo pode ser reativado.")
    return _set_status(s, request, acc, p, "PENDING_REVIEW", "reactivated")


# ---------------------------------------------------------------- rotas internas (fiscal-api)
class PreparedProposal(Strict):
    origin_key: str = Field(min_length=3, max_length=80)
    name: str = Field(min_length=2, max_length=120)
    fields: dict
    field_sources: dict
    evidence: dict
    suggestions: list = Field(default_factory=list)


class PrepareIn(Strict):
    actor_id: str | None = Field(None, max_length=64)
    actor_type: str = Field("user", pattern="^(user|system)$")
    proposals: list[PreparedProposal] = Field(max_length=200)


CAMPOS_PREPARO = ("national_code", "municipal_code", "nbs_code", "description", "issqn_taxation", "pis_cofins_cst",
                  "ibs_cbs")


@internal.post("/companies/{company_id}/service-profiles/prepare")
def prepare(company_id: str, body: PrepareIn, request: Request, s: Session = Depends(db_session)) -> dict:
    """Cria perfis PENDENTES a partir do inventário das notas emitidas. Idempotente por origin_key; perfil já
    existente (mesma origem) NUNCA é sobrescrito — apenas listado como existente."""
    c = ctx(request)
    c.service_auth.authenticate(request, "service_profiles:prepare")
    company = s.get(Company, company_id)
    if company is None:
        raise ProblemError(404, "COMPANY_NOT_FOUND", "Empresa não encontrada.")
    now = c.clock.now()
    criados, existentes = [], []
    for prop in body.proposals:
        found = s.scalar(select(ServiceProfile).where(ServiceProfile.company_id == company_id,
                                                      ServiceProfile.origin_key == prop.origin_key))
        if found is not None:
            existentes.append({"origin_key": prop.origin_key, "id": found.id, "status": found.status})
            continue
        data = {k: prop.fields.get(k) for k in CAMPOS_PREPARO}
        # Código fora da tabela oficial não entra no perfil (fica só na evidência, como divergência a revisar).
        if data.get("nbs_code") and not dominios.nbs(data["nbs_code"]):
            data["nbs_code"] = None
        if data.get("national_code") and not dominios.servico_nacional(data["national_code"]):
            continue
        p = ServiceProfile(company_id=company_id, name=prop.name, **data, is_active=True, status="PENDING_REVIEW",
                           version=1, origin_key=prop.origin_key, evidence=prop.evidence,
                           field_sources=prop.field_sources, suggestions=prop.suggestions,
                           source="Preparado a partir das notas emitidas (XML oficial)",
                           created_by=body.actor_id if body.actor_type == "user" else None,
                           updated_by=body.actor_id if body.actor_type == "user" else None,
                           created_at=now, updated_at=now)
        try:
            with s.begin_nested():
                s.add(p)
                s.flush()
        except IntegrityError:  # preparação concorrente da mesma origem: a outra venceu
            existentes.append({"origin_key": prop.origin_key, "id": None, "status": "CONCURRENT"})
            continue
        ps.registrar_versao(s, p, "prepared", now, body.actor_id, body.actor_type)
        audit(s, now, "service_profile.prepared", actor_type=body.actor_type, actor_id=body.actor_id,
              company_id=company_id, target_type="service_profile", target_id=p.id,
              details={"origin_key": prop.origin_key, "notes": prop.evidence.get("total_notas"),
                       "fields": sorted(k for k, v in data.items() if v)}, trace_id=trace_id_for(request))
        criados.append({"origin_key": prop.origin_key, "id": p.id})
    s.commit()
    return {"created": criados, "existing": existentes}


@internal.get("/companies/{company_id}/service-profiles/{profile_id}")
def internal_profile(company_id: str, profile_id: str, request: Request, s: Session = Depends(db_session)) -> dict:
    c = ctx(request)
    c.service_auth.authenticate(request, "service_profiles:read")
    p = _get(s, company_id, profile_id, lock=False)
    company = s.get(Company, company_id)
    hoje = c.clock.now().date()
    return {**ps.snapshot(p), "id": p.id, "version": p.version,
            "blocking": ps.bloqueios(p, company, hoje)}


class ProviderCompleteIn(Strict):
    actor_id: str | None = Field(None, max_length=64)
    actor_type: str = Field("user", pattern="^(user|system)$")
    values: dict = Field(description="Valores lidos de forma CONSISTENTE das notas emitidas (grupo emit).")
    sources: dict = Field(default_factory=dict, description="Campo → notas de referência.")


CAMPOS_PRESTADOR = ("municipal_registration", "phone", "email", "postal_code", "municipality_code")
CAMPOS_ENDERECO = ("street", "number", "complement", "district", "city", "state")


@internal.post("/companies/{company_id}/provider-data/complete")
def provider_complete(company_id: str, body: ProviderCompleteIn, request: Request, s: Session = Depends(db_session)) -> dict:
    """Completa o cadastro do prestador SOMENTE nos campos vazios; valor divergente do cadastro nunca é sobrescrito
    (volta como divergência para decisão do usuário)."""
    c = ctx(request)
    c.service_auth.authenticate(request, "provider:complete")
    company = s.scalar(select(Company).where(Company.id == company_id).with_for_update())
    if company is None:
        raise ProblemError(404, "COMPANY_NOT_FOUND", "Empresa não encontrada.")
    aplicados, iguais, divergentes = {}, [], []
    for k in CAMPOS_PRESTADOR:
        novo = body.values.get(k)
        if not novo:
            continue
        atual = getattr(company, k)
        if not atual:
            setattr(company, k, novo)
            aplicados[k] = novo
        elif str(atual) == str(novo):
            iguais.append(k)
        else:
            divergentes.append({"field": k, "cadastro": atual, "notas": novo})
    end_novo = body.values.get("address") or {}
    if end_novo:
        atual = dict(company.address or {})
        for k in CAMPOS_ENDERECO:
            v = end_novo.get(k)
            if not v:
                continue
            if not atual.get(k):
                atual[k] = v
                aplicados[f"address.{k}"] = v
            elif atual[k] == v:
                iguais.append(f"address.{k}")
            else:
                divergentes.append({"field": f"address.{k}", "cadastro": atual[k], "notas": v})
        company.address = atual or None
    now = c.clock.now()
    if aplicados:
        company.updated_at = company.fiscal_profile_updated_at = now
        audit(s, now, "company.provider_data_completed", actor_type=body.actor_type, actor_id=body.actor_id,
              company_id=company_id, target_type="company", target_id=company_id,
              details={"applied": sorted(aplicados), "divergent": [d["field"] for d in divergentes],
                       "sources": {k: body.sources.get(k.split(".")[0]) or body.sources.get(k) for k in aplicados}},
              trace_id=trace_id_for(request))
    s.commit()
    return {"applied": aplicados, "equal": iguais, "divergent": divergentes}
