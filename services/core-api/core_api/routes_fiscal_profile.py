"""Perfil fiscal do prestador e certificado A1 da empresa (Sprint 02)."""
from __future__ import annotations

import base64

import httpx
from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from ctrl_common.problems import ProblemError, problem_responses, trace_id_for
from ctrl_common.service_auth import service_headers

from .app import CompanyAccess, audit, company_access, ctx, db_session
from .models import Company
from .schemas import CertificateInfo, FiscalProfileOut, FiscalProfilePatch

router = APIRouter(tags=["companies"])
MAX_PFX_BYTES = 64 * 1024
ENVS = ("mock", "restricted", "production")


def missing_for_emission(c: Company) -> list[str]:
    missing = []
    if not c.municipality_code:
        missing.append("municipality_code")
    if not c.simples_option:
        missing.append("simples_option")
    if c.special_regime is None:
        missing.append("special_regime")
    if c.simples_option == "3" and not c.simples_regime:
        missing.append("simples_regime")
    return missing


def profile_out(c: Company) -> FiscalProfileOut:
    miss = missing_for_emission(c)
    return FiscalProfileOut(
        company_id=c.id, cnpj=c.cnpj, name=c.name, timezone=c.timezone, environment=c.nfse_environment,
        adn_environments=list(c.adn_environments or [c.nfse_environment]), municipality_code=c.municipality_code,
        municipal_registration=c.municipal_registration, simples_option=c.simples_option, simples_regime=c.simples_regime,
        special_regime=c.special_regime, phone=c.phone, email=c.email, source=c.fiscal_profile_source,
        updated_at=c.fiscal_profile_updated_at, complete_for_emission=not miss, missing=miss,
        dps_series=c.dps_series, postal_code=c.postal_code, address=c.address, workspace_status=_ws_status(c),
    )


def _ws_status(c: Company) -> str | None:
    from sqlalchemy import select
    from sqlalchemy.orm import object_session

    from .models import Workspace

    s = object_session(c)
    return s.scalar(select(Workspace.status).where(Workspace.company_id == c.id)) if s is not None else None


@router.get("/companies/{company_id}/fiscal-profile", response_model=FiscalProfileOut,
            responses=problem_responses(401, 403))
def get_profile(company_id: str, acc: CompanyAccess = Depends(company_access("view"))) -> FiscalProfileOut:
    return profile_out(acc.company)


@router.patch("/companies/{company_id}/fiscal-profile", response_model=FiscalProfileOut,
              responses=problem_responses(401, 403, 422))
def patch_profile(company_id: str, body: FiscalProfilePatch, request: Request,
                  acc: CompanyAccess = Depends(company_access("admin")), s: Session = Depends(db_session)) -> FiscalProfileOut:
    c = s.get(Company, acc.company.id, with_for_update=True)
    changes = body.model_dump(exclude_unset=True)
    from .seed import DEMO_CNPJS

    if c.cnpj in DEMO_CNPJS and (
        set(changes.get("adn_environments") or []) - {"mock"} or changes.get("environment") not in (None, "mock")
    ):
        raise ProblemError(422, "DEMO_COMPANY_OFFICIAL_BLOCKED",
                           "Empresa de demonstração não usa ambientes oficiais (Produção Restrita/Produção).")
    for key, value in changes.items():
        if key == "environment":
            c.nfse_environment = value
        elif key == "source":
            c.fiscal_profile_source = value
        elif key == "adn_environments":
            c.adn_environments = sorted(set(value))
        elif key == "dps_series":
            from .routes_workspaces import validar_serie

            erro = validar_serie(value)
            if erro:
                raise ProblemError(422, "DPS_SERIES_INVALID", erro,
                                   errors=[{"field": "dps_series", "code": "INVALID", "message": erro}])
            c.dps_series = value
        elif key == "address":
            c.address = value
        else:
            setattr(c, key, value)
    if c.simples_regime and c.simples_option != "3":
        raise ProblemError(422, "SIMPLES_REGIME_REQUIRES_OPTION_3",
                           "regApTribSN só se aplica a optante ME/EPP (opSimpNac=3).",
                           errors=[{"field": "simples_regime", "code": "INVALID", "message": "Exige simples_option=3."}])
    now = ctx(request).clock.now()
    c.fiscal_profile_updated_at = now
    c.updated_at = now
    audit(s, now, "company.fiscal_profile_updated", actor_id=acc.user.id, company_id=c.id, target_type="company",
          target_id=c.id, details={"fields": sorted(changes)}, trace_id=trace_id_for(request))
    s.commit()
    return profile_out(c)


def _nfse(request: Request) -> tuple[httpx.Client, dict]:
    c = ctx(request)
    if c.nfse_http is None or not c.settings.nfse_api_key:
        raise ProblemError(503, "INTEGRATION_UNAVAILABLE", "Serviço de integração NFS-e indisponível.")
    return c.nfse_http, service_headers("core-api", c.settings.nfse_api_key)


@router.post(
    "/companies/{company_id}/certificate", status_code=201, response_model=CertificateInfo,
    responses=problem_responses(401, 403, 413, 422, 503),
    summary="Anexa o certificado A1 (PFX) da empresa. Exige a permissão 'certificates'. A senha nunca é armazenada em claro nem devolvida.",
)
async def upload_certificate(
    company_id: str, request: Request,
    file: UploadFile = File(..., description="Arquivo .pfx/.p12 do e-CNPJ A1"),
    password: str = Form(..., min_length=1, max_length=128, description="Senha do PFX (não é registrada em log)"),
    environments: str = Form("restricted,production", description="Ambientes que usarão o certificado"),
    acc: CompanyAccess = Depends(company_access("certificates")),
    s: Session = Depends(db_session),
) -> CertificateInfo:
    from .seed import DEMO_CNPJS

    if acc.company.cnpj in DEMO_CNPJS:
        raise ProblemError(422, "DEMO_COMPANY_NO_CERTIFICATE",
                           "Empresa de demonstração não recebe certificado: nenhuma conexão oficial é permitida.")
    envs = sorted({e.strip() for e in environments.split(",") if e.strip()})
    if not envs or any(e not in ("restricted", "production") for e in envs):
        raise ProblemError(422, "INVALID_ENVIRONMENTS", "Use restricted e/ou production.")
    data = await file.read(MAX_PFX_BYTES + 1)
    if len(data) > MAX_PFX_BYTES:
        raise ProblemError(413, "PAYLOAD_TOO_LARGE", "Arquivo de certificado maior que o permitido.")
    http, headers = _nfse(request)
    try:
        r = await run_in_threadpool(http.post, f"/internal/v1/companies/{acc.company.id}/certificates", headers=headers, json={
            "cnpj": acc.company.cnpj, "environments": envs,
            "pfx_b64": base64.b64encode(data).decode(), "password": password,
        })
    except httpx.HTTPError:
        raise ProblemError(503, "INTEGRATION_UNAVAILABLE", "Serviço de integração NFS-e indisponível.")
    finally:
        del data, password
    body = r.json() if r.headers.get("content-type", "").startswith(("application/json", "application/problem+json")) else {}
    if r.status_code == 422:
        raise ProblemError(422, body.get("code", "CERTIFICATE_INVALID"), body.get("detail", "Certificado inválido."),
                           errors=body.get("errors"))
    if r.status_code != 201:
        raise ProblemError(503, "INTEGRATION_UNAVAILABLE", "Falha ao registrar o certificado.")
    now = ctx(request).clock.now()
    audit(s, now, "company.certificate_uploaded", actor_id=acc.user.id, company_id=acc.company.id, target_type="certificate",
          target_id=body.get("fingerprint_sha256"), details={"environments": envs, "accepted": body.get("accepted")},
          trace_id=trace_id_for(request))
    s.commit()
    return CertificateInfo.model_validate(body)


@router.get("/companies/{company_id}/certificates", responses=problem_responses(401, 403, 503),
            summary="Certificado ativo do workspace (situação e validade) e histórico — somente metadados, sem segredos.")
def certificate_history(company_id: str, request: Request, acc: CompanyAccess = Depends(company_access("view"))) -> dict:
    from datetime import datetime

    http, headers = _nfse(request)
    try:
        r = http.get(f"/internal/v1/companies/{acc.company.id}/certificates", headers=headers)
    except httpx.HTTPError:
        raise ProblemError(503, "INTEGRATION_UNAVAILABLE", "Serviço de integração NFS-e indisponível.")
    if r.status_code != 200:
        raise ProblemError(503, "INTEGRATION_UNAVAILABLE", "Falha ao consultar os certificados.")
    keys = ("environment", "fingerprint_sha256", "subject_cnpj", "not_before", "not_after", "is_active", "created_at",
            "replaced_at", "replaced_by_fingerprint", "material_purged")
    rows = [{k: h.get(k) for k in keys} for h in r.json()]
    now = ctx(request).clock.now()
    ativos = [h for h in rows if h["is_active"]]
    status, active = "ABSENT", None
    if ativos:
        h = ativos[0]
        fim = datetime.fromisoformat(str(h["not_after"]).replace("Z", "+00:00"))
        days = (fim - now).days
        status = ("CNPJ_MISMATCH" if h["subject_cnpj"] and h["subject_cnpj"] != acc.company.cnpj
                  else "EXPIRED" if fim <= now else "EXPIRING" if days <= 30 else "VALID")
        active = {"fingerprint_sha256": h["fingerprint_sha256"], "not_before": h["not_before"], "not_after": h["not_after"],
                  "days_to_expiry": days, "environments": sorted(x["environment"] for x in ativos),
                  "subject_cnpj_matches": h["subject_cnpj"] == acc.company.cnpj}
    return {"status": status, "active": active, "history": rows,
            "note": "Certificado vencido, de outro CNPJ ou ausente bloqueia transmissões; consultas locais continuam."}


@router.delete("/companies/{company_id}/certificate", status_code=204, responses=problem_responses(401, 403, 503),
               summary="Desativa o certificado da empresa no ambiente indicado.")
def delete_certificate(company_id: str, request: Request,
                       environment: str = Query(..., pattern="^(restricted|production)$"),
                       acc: CompanyAccess = Depends(company_access("certificates")), s: Session = Depends(db_session)) -> Response:
    http, headers = _nfse(request)
    try:
        r = http.delete(f"/internal/v1/companies/{acc.company.id}/certificates", headers=headers, params={"environment": environment})
    except httpx.HTTPError:
        raise ProblemError(503, "INTEGRATION_UNAVAILABLE", "Serviço de integração NFS-e indisponível.")
    if r.status_code != 204:
        raise ProblemError(503, "INTEGRATION_UNAVAILABLE", "Falha ao desativar o certificado.")
    now = ctx(request).clock.now()
    audit(s, now, "company.certificate_deactivated", actor_id=acc.user.id, company_id=acc.company.id,
          target_type="certificate", details={"environment": environment}, trace_id=trace_id_for(request))
    s.commit()
    return Response(status_code=204)
