"""Workspaces: um workspace por empresa (mesmo identificador), perfis de serviço e prontidão por ambiente/operação.

Invariantes (no banco e aqui, nunca só na interface):
- workspaces.company_id único e companies.cnpj único ⇒ uma empresa por workspace e um workspace por CNPJ; a criação
  concorrente com o mesmo CNPJ falha na restrição única (409), sem estado parcial;
- não há operação que troque a empresa (ou o CNPJ) de um workspace;
- SUPER_ADMIN cria/administra workspaces, mas NÃO ganha acesso fiscal: operar exige vínculo explícito (membership);
- um workspace em preparação (PREPARING) ou suspenso não transmite (o fiscal-api recusa o agendamento);
- o certificado (um ativo por workspace) e a sua compatibilidade ficam no conector (nfse-api).
"""
from __future__ import annotations

from datetime import date

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ctrl_common import dominios
from ctrl_common.fiscal_types import is_valid_cnpj
from ctrl_common.problems import ProblemError, problem_responses, trace_id_for
from ctrl_common.service_auth import service_headers

from .app import CompanyAccess, CurrentUser, audit, company_access, ctx, current_user, db_session
from .models import Company, Membership, ServiceProfile, Workspace
from .routes_fiscal_profile import missing_for_emission

router = APIRouter(tags=["workspaces"])
ERRS = problem_responses(401, 403, 404, 409, 422)
STATUSES = ("PREPARING", "ACTIVE", "SUSPENDED")
SIMPLES = ("2", "3")
IBSCBS_OBRIGATORIO_DESDE = date(2026, 10, 1)  # Ato Conjunto RFB/CGIBS 4/2026 (não optantes)
SERIE_MIN, SERIE_MAX = 1, 49999  # faixa da série para aplicativo próprio (ver validar_serie)


class Strict(BaseModel):
    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------- utilidades
def workspace_of(s: Session, company_id: str) -> Workspace | None:
    return s.scalar(select(Workspace).where(Workspace.company_id == company_id))


def workspace_out(w: Workspace, c: Company, permissions: list[str] | None = None) -> dict:
    return {"id": w.id, "name": w.name, "status": w.status,
            "company": {"id": c.id, "name": c.name, "cnpj": c.cnpj, "municipality_code": c.municipality_code},
            "environment": c.nfse_environment, "permissions": permissions}


def validar_serie(serie: str | None) -> str | None:
    """Série da DPS: só dígitos (TSSerieDPS) e, para o aplicativo próprio do contribuinte, entre 1 e 49999."""
    if serie in (None, ""):
        return "Série da DPS não configurada para este workspace."
    if not serie.isdigit() or not SERIE_MIN <= int(serie) <= SERIE_MAX:
        return f"Série da DPS fora da faixa do aplicativo próprio ({SERIE_MIN}–{SERIE_MAX})."
    return None


# ---------------------------------------------------------------- workspaces do usuário
@router.get("/workspaces", responses=ERRS, summary="Workspaces em que o usuário tem vínculo (contexto de trabalho).")
def my_workspaces(cu: CurrentUser = Depends(current_user), s: Session = Depends(db_session)) -> list[dict]:
    rows = s.execute(select(Workspace, Company, Membership).join(Company, Company.id == Workspace.company_id)
                     .join(Membership, Membership.company_id == Company.id)
                     .where(Membership.user_id == cu.user.id, Company.is_active.is_(True))
                     .order_by(Workspace.name)).all()
    return [workspace_out(w, c, list(m.permissions or [])) for w, c, m in rows]


# ---------------------------------------------------------------- administração (SUPER_ADMIN)
class CompanyNew(Strict):
    cnpj: str = Field(pattern=r"^[0-9]{14}$")
    name: str = Field(min_length=2, max_length=200)
    municipality_code: str | None = Field(None, pattern=r"^\d{7}$")
    timezone: str = Field("America/Sao_Paulo", max_length=64)


class WorkspaceCreate(Strict):
    name: str = Field(min_length=2, max_length=200)
    company: CompanyNew


class WorkspacePatch(Strict):
    name: str | None = Field(None, min_length=2, max_length=200)
    status: str | None = Field(None, pattern="^(PREPARING|ACTIVE|SUSPENDED)$")


def _require_super(cu: CurrentUser) -> None:
    if not cu.user.is_super_admin:
        raise ProblemError(403, "SUPER_ADMIN_REQUIRED", "Somente SUPER_ADMIN administra workspaces.")


@router.get("/admin/workspaces", responses=ERRS)
def admin_workspaces(cu: CurrentUser = Depends(current_user), s: Session = Depends(db_session)) -> list[dict]:
    _require_super(cu)
    return [workspace_out(w, c) for w, c in s.execute(
        select(Workspace, Company).join(Company, Company.id == Workspace.company_id).order_by(Workspace.name)).all()]


@router.post("/admin/workspaces", status_code=201, responses=ERRS,
             summary="Cria workspace em PREPARAÇÃO com a sua empresa (CNPJ único). Não cria vínculos nem certificado.")
def create_workspace(body: WorkspaceCreate, request: Request, cu: CurrentUser = Depends(current_user),
                     s: Session = Depends(db_session)) -> dict:
    _require_super(cu)
    if not is_valid_cnpj(body.company.cnpj):
        raise ProblemError(422, "CNPJ_INVALID", "CNPJ inválido.")
    if body.company.municipality_code and not dominios.municipio(body.company.municipality_code):
        raise ProblemError(422, "MUNICIPALITY_NOT_IN_IBGE", "Município não existe na tabela IBGE (Anexo A).")
    now = ctx(request).clock.now()
    if s.scalar(select(Company.id).where(Company.cnpj == body.company.cnpj)):
        raise ProblemError(409, "CNPJ_ALREADY_IN_WORKSPACE", "Já existe workspace para este CNPJ.")
    c = Company(name=body.company.name.strip(), cnpj=body.company.cnpj, timezone=body.company.timezone,
                nfse_environment="restricted", municipality_code=body.company.municipality_code,
                adn_environments=[], is_active=True, created_at=now, updated_at=now)
    s.add(c)
    try:
        s.flush()
        w = Workspace(id=c.id, company_id=c.id, name=body.name.strip(), status="PREPARING", created_by=cu.user.id,
                      created_at=now, updated_at=now)
        s.add(w)
        s.flush()
    except IntegrityError:  # corrida com o mesmo CNPJ: a restrição única decide; nada fica pela metade
        s.rollback()
        raise ProblemError(409, "CNPJ_ALREADY_IN_WORKSPACE", "Já existe workspace para este CNPJ.")
    audit(s, now, "workspace.created", actor_id=cu.user.id, company_id=c.id, target_type="workspace", target_id=w.id,
          details={"status": "PREPARING"}, trace_id=trace_id_for(request))
    s.commit()
    return workspace_out(w, c)


@router.patch("/admin/workspaces/{workspace_id}", responses=ERRS,
              summary="Nome/situação do workspace. ACTIVE não libera emissão nem cancelamento (controles separados).")
def patch_workspace(workspace_id: str, body: WorkspacePatch, request: Request, cu: CurrentUser = Depends(current_user),
                    s: Session = Depends(db_session)) -> dict:
    _require_super(cu)
    w = s.scalar(select(Workspace).where(Workspace.id == workspace_id).with_for_update())
    if w is None:
        raise ProblemError(404, "WORKSPACE_NOT_FOUND", "Workspace não encontrado.")
    now = ctx(request).clock.now()
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(w, k, v.strip() if isinstance(v, str) and k == "name" else v)
    w.updated_at = now
    audit(s, now, "workspace.updated", actor_id=cu.user.id, company_id=w.company_id, target_type="workspace",
          target_id=w.id, details=changes, trace_id=trace_id_for(request))
    s.commit()
    return workspace_out(w, s.get(Company, w.company_id))


# ---------------------------------------------------------------- prontidão
def _integration(request: Request, company: Company, env: str) -> dict | None:
    c = ctx(request)
    if c.nfse_http is None or not c.settings.nfse_api_key:
        return None
    try:
        r = c.nfse_http.get(f"/internal/v1/companies/{company.id}/integration-status",
                            params={"environment": env, "cnpj": company.cnpj},
                            headers=service_headers("core-api", c.settings.nfse_api_key))
    except httpx.HTTPError:
        return None
    return r.json() if r.status_code == 200 else None


def _issue(code: str, message: str, severity: str = "error") -> dict:
    return {"code": code, "message": message, "severity": severity}


@router.get("/companies/{company_id}/readiness", responses=ERRS,
            summary="Prontidão do workspace por ambiente e operação (consulta, emissão, cancelamento): o que falta.")
def readiness(company_id: str, request: Request, acc: CompanyAccess = Depends(company_access("view")),
              s: Session = Depends(db_session)) -> dict:
    c = s.get(Company, acc.company.id)
    w = workspace_of(s, c.id)
    now = ctx(request).clock.now()
    profiles = list(s.scalars(select(ServiceProfile).where(ServiceProfile.company_id == c.id,
                                                           ServiceProfile.is_active.is_(True))))
    cadastro: list[dict] = []
    rotulos = {"municipality_code": "o município do emitente (IBGE)", "simples_option": "a opção pelo Simples Nacional",
               "special_regime": "o regime especial de tributação", "simples_regime": "o regime de apuração do Simples"}
    for f in missing_for_emission(c):
        cadastro.append(_issue(f"PROFILE_{f.upper()}", f"Perfil fiscal: informe {rotulos.get(f, f)}."))
    serie = validar_serie(c.dps_series)
    if serie:
        cadastro.append(_issue("DPS_SERIES", serie))
    if not c.municipal_registration:
        cadastro.append(_issue("MUNICIPAL_REGISTRATION", "Inscrição municipal não informada (exigência depende do "
                                                         "município).", "warning"))
    from . import perfis_servico as ps

    servicos: list[dict] = []
    aprovados = [p for p in profiles if p.status == "APPROVED"]
    pendentes = [p for p in profiles if p.status == "PENDING_REVIEW"]
    if not aprovados:
        servicos.append(_issue("NO_APPROVED_SERVICE_PROFILE",
                               "Nenhum perfil de serviço aprovado: revise e aprove ao menos um perfil em "
                               f"Configurações → Perfis de serviço ({len(pendentes)} pendente(s) de revisão)."))
    detalhe_perfis = []
    for p in pendentes:
        blk = ps.bloqueios(p, c, now.date())
        falta = "; ".join(f"{i['label']} ({i['code']})" for i in blk)
        detalhe_perfis.append(_issue(
            "SERVICE_PROFILE_PENDING",
            f"Perfil \"{p.name}\" pendente de revisão" + (f": falta {falta}" if blk else
                                                          ": dados completos, aguardando aprovação."),
            "warning"))
    ws_issue = None if w and w.status == "ACTIVE" else _issue(
        "WORKSPACE_NOT_ACTIVE", f"Workspace em {w.status if w else 'estado desconhecido'}: transmissões bloqueadas.")
    envs = []
    for env in ("production", "restricted"):
        st = _integration(request, c, env)
        enabled_read = env in (c.adn_environments or [])
        cert = (st or {}).get("certificate") or {}
        caps = (st or {}).get("capabilities") or {}
        blockers = [_issue(b["code"], b["message"]) for b in (st or {}).get("blockers", [])]
        if st is None:
            blockers = [_issue("INTEGRATION_UNAVAILABLE", "Conector NFS-e indisponível para conferir.")]
        ops = []
        consulta = ([] if enabled_read else [_issue("ENV_NOT_ENABLED_FOR_READ", "Leitura ADN não habilitada neste ambiente.")])
        if not caps.get("distribution"):
            consulta += [b for b in blockers if b["code"] not in ("PRODUCTION_EMISSION_BLOCKED",
                                                                   "PRODUCTION_CANCELLATION_BLOCKED")] or \
                [_issue("DISTRIBUTION_NOT_READY", "Leitura ADN indisponível.")]
        ops.append({"operation": "consulta", "ready": not consulta, "issues": consulta})
        base = ([ws_issue] if ws_issue else []) + cadastro
        emissao = list(base) + servicos
        if env != c.nfse_environment:
            emissao.append(_issue("NOT_EMISSION_ENVIRONMENT", "Este não é o ambiente de emissão do workspace."))
        if not caps.get("emission"):
            emissao += [b for b in blockers if b not in emissao] or [_issue("EMISSION_NOT_READY", "Emissão indisponível.")]
        ops.append({"operation": "emissao", "ready": not [i for i in emissao if i["severity"] == "error"],
                    "issues": emissao})
        canc = ([ws_issue] if ws_issue else [])
        if not caps.get("cancellation"):
            canc += [b for b in blockers if b["code"] != "PRODUCTION_EMISSION_BLOCKED"] or \
                [_issue("CANCELLATION_NOT_READY", "Cancelamento indisponível.")]
        ops.append({"operation": "cancelamento", "ready": not canc, "issues": canc})
        envs.append({"environment": env, "certificate": {k: cert.get(k) for k in
                                                         ("present", "subject_cnpj_matches", "not_after", "days_to_expiry")},
                     "operations": ops})
    sections = _secoes(c, cadastro, envs, servicos + detalhe_perfis, ws_issue)
    return {"workspace": workspace_out(w, c) if w else None, "checked_at": now, "environments": envs,
            "sections": sections,
            "note": "Concluir o cadastro não libera produção: emissão e cancelamento têm controles próprios."}


def _secoes(c: Company, cadastro: list[dict], envs: list[dict], perfis: list[dict], ws_issue: dict | None) -> list[dict]:
    """Prontidão agrupada pelo que precisa ser feito: cadastro, certificado, perfil, nota e ambiente."""
    info = "info"
    cad = list(cadastro)
    cad.append(_issue("PROVIDER_ADDRESS_NOT_SENT",
                      "Endereço e CEP do prestador são opcionais no cadastro e NÃO são enviados na DPS quando o "
                      "emitente é o próprio prestador (regra E0128: o grupo prest/end não deve ser informado).", info))
    cert = []
    for e in envs:
        cc = e["certificate"] or {}
        nome = "Produção" if e["environment"] == "production" else "Produção Restrita"
        if cc.get("present"):
            ok = bool(cc.get("subject_cnpj_matches"))
            cert.append(_issue("CERTIFICATE_OK" if ok else "CERTIFICATE_CNPJ_MISMATCH",
                               f"{nome}: certificado instalado, válido até {str(cc.get('not_after'))[:10]}"
                               + ("" if ok else " — CNPJ do titular diverge do workspace"), info if ok else "error"))
        else:
            cert.append(_issue("CERTIFICATE_ABSENT", f"{nome}: sem certificado instalado.",
                               "error" if e["environment"] == c.nfse_environment else info))
    nota = [_issue(code, msg, info) for code, msg in (
        ("NOTE_CUSTOMER", "Tomador cadastrado em Clientes (CNPJ/CPF e nome; endereço completo quando houver retenção "
                          "do ISS pelo tomador — E0237 — ou quando o cIndOp/incidência exigir — E0234)."),
        ("NOTE_DATES", "Competência e data de início da prestação (dCompet) dentro da competência."),
        ("NOTE_VALUES", "Valor do serviço, descontos e deduções; retenções efetivamente aplicadas (ISS, INSS, IRRF, "
                        "contribuições) e tributos aproximados (totTrib, Lei 12.741/2012)."),
        ("NOTE_LOCATION", "Município da prestação (cLocPrestacao)."),
        ("NOTE_CONDITIONAL", "Grupos exigidos pelo perfil: obra (subitens de construção), imóvel ou destinatário "
                             "(conforme o cIndOp) e diferimento (conforme a classificação)."),
    )]
    amb = [ws_issue] if ws_issue else []
    codigos_amb = ("PRODUCTION_EMISSION_BLOCKED", "PRODUCTION_CANCELLATION_BLOCKED", "ENV_NOT_ENABLED_FOR_READ",
                   "NOT_EMISSION_ENVIRONMENT", "RESTRICTED_DISABLED", "OFFICIAL_DISABLED", "EMISSION_NOT_READY",
                   "CANCELLATION_NOT_READY", "DISTRIBUTION_NOT_READY", "INTEGRATION_UNAVAILABLE")
    for e in envs:
        nome = "Produção" if e["environment"] == "production" else "Produção Restrita"
        for op in e["operations"]:
            label = {"consulta": "consulta", "emissao": "emissão", "cancelamento": "cancelamento"}[op["operation"]]
            if op["ready"]:
                amb.append(_issue("ENV_READY", f"{nome}: {label} habilitada.", info))
            for i in op["issues"]:
                if i["code"] in codigos_amb:
                    amb.append(_issue(i["code"], f"{nome} — {label}: {i['message']}", i["severity"]))
    secs = [("cadastro", "Cadastro do prestador", cad), ("certificado", "Certificado e integração", cert),
            ("perfil", "Perfil do serviço", perfis), ("nota", "Dados específicos de cada nota", nota),
            ("ambiente", "Habilitação do ambiente", amb)]
    return [{"id": k, "title": t, "ok": not [i for i in items if i["severity"] == "error"], "items": items}
            for k, t, items in secs]

