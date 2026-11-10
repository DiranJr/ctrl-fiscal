"""Ambiente do workspace e leitura ADN: troca EXPLÍCITA, confirmada e auditada pelo administrador do workspace.

- Opções = o que a instalação efetivamente permite (política do conector por ambiente) + certificado do workspace;
  ambiente indisponível vem com o motivo e como ajustar. Nada é presumido.
- A troca muda só companies.nfse_environment e companies.adn_environments DESTE workspace (linha travada). Não muda
  situação do workspace, perfis, certificado, emissão nem cancelamento (controles próprios da instalação e do
  workspace) e não toca dados do fiscal: documentos, rascunhos, cursores e históricos ficam no ambiente em que foram
  gravados (cada ambiente tem a sua agenda/cursor; a do ambiente que deixa de ser lido é suspensa, não apagada).
- Agendamentos do ambiente anterior: o worker confere o ambiente vigente antes de transmitir e retira o item não
  iniciado (ENVIRONMENT_CHANGED, nada transmitido); item com envio possível só reconcilia no ambiente original.
- Confirmação: o pedido repete empresa, CNPJ e os ambientes de origem e destino; divergência = 409 (nada muda).
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ctrl_common.problems import ProblemError, problem_responses, trace_id_for

from .app import CompanyAccess, audit, company_access, ctx, db_session
from .models import Company

router = APIRouter(tags=["workspaces"])
ERRS = problem_responses(401, 403, 409, 422, 503)
OFICIAIS = ("production", "restricted")
NOME = {"production": "Produção", "restricted": "Produção Restrita", "mock": "Simulação"}
# Bloqueios que dependem da CONFIGURAÇÃO DA INSTALAÇÃO (iguais para todos os workspaces), com como ajustar.
GLOBAIS = {
    "RESTRICTED_DISABLED": "Produção Restrita desligada na instalação (NFSE_RESTRICTED_ENABLED=false). Ajuste na "
                           "configuração do servidor, se a instalação deve usar esse ambiente.",
    "PRODUCTION_DISTRIBUTION_DISABLED": "Leitura ADN de Produção desligada na instalação "
                                        "(NFSE_PRODUCTION_DISTRIBUTION_ENABLED=false). Ajuste na configuração do servidor.",
    "OFFICIAL_DISABLED": "Integração oficial desligada na instalação.",
    "VAULT_KEY_NOT_CONFIGURED": "Cofre de certificados sem chave na instalação.",
    "OFFICIAL_CONTRACT_UNCONFIRMED": "Contrato oficial não confirmado na instalação.",
}


class Confirmacao(BaseModel):
    model_config = {"extra": "forbid"}
    company_id: str = Field(max_length=36)
    cnpj: str = Field(pattern=r"^[0-9A-Z]{12}[0-9]{2}$")
    from_environment: str = Field(max_length=16)
    to_environment: Literal["production", "restricted"]


class TrocaAmbiente(BaseModel):
    model_config = {"extra": "forbid"}
    environment: Literal["production", "restricted"]
    adn_read: bool = Field(True, description="Habilita a leitura ADN (consulta de notas) no ambiente escolhido.")
    confirmation: Confirmacao


def _status(request: Request, c: Company, env: str) -> dict | None:
    from .routes_workspaces import _integration  # noqa: PLC0415

    return _integration(request, c, env)


def opcao(c: Company, env: str, st: dict | None) -> dict:
    """Disponibilidade de um ambiente para ESTE workspace: instalação (global) × empresa (certificado)."""
    if st is None:
        return {"environment": env, "label": NOME[env], "available": False, "read_available": False,
                "global_issues": [{"code": "INTEGRATION_UNAVAILABLE",
                                   "message": "Conector NFS-e indisponível para conferir este ambiente."}],
                "company_issues": [], "installation": {}, "certificate": {}}
    policy = st.get("policy") or {}
    cert = st.get("certificate") or {}
    codigos = {b.get("code") for b in st.get("blockers") or []}
    globais = [{"code": k, "message": v} for k, v in GLOBAIS.items() if k in codigos]
    empresa = []
    if not cert.get("present"):
        empresa.append({"code": "CERTIFICATE_ABSENT", "message": f"Sem certificado instalado para {NOME[env]}."})
    elif not cert.get("subject_cnpj_matches"):
        empresa.append({"code": "CERTIFICATE_CNPJ_MISMATCH", "message": "O CNPJ do certificado diverge do workspace."})
    usavel = bool(policy.get("distribution") or policy.get("emission"))
    if not usavel and not globais:
        globais.append({"code": "ENVIRONMENT_DISABLED", "message": f"{NOME[env]} desabilitada na instalação."})
    return {"environment": env, "label": NOME[env], "available": usavel,
            "read_available": bool(policy.get("distribution")) and not empresa,
            "global_issues": globais, "company_issues": empresa,
            # Só informação: habilitar emissão/cancelamento é da instalação + workspace ativo + perfis aprovados.
            "installation": {k: bool(policy.get(k)) for k in ("distribution", "emission", "cancellation")},
            "certificate": {k: cert.get(k) for k in ("present", "subject_cnpj_matches", "not_after")}}


def _saida(c: Company, opcoes: list[dict]) -> dict:
    return {"company_id": c.id, "cnpj": c.cnpj, "environment": c.nfse_environment,
            "adn_environments": sorted(c.adn_environments or []), "options": opcoes,
            "note": "Trocar o ambiente não habilita emissão nem cancelamento: dependem da instalação, do workspace "
                    "ativo e de perfis de serviço aprovados. Documentos, rascunhos e cursores de cada ambiente são "
                    "preservados e continuam separados."}


@router.get("/companies/{company_id}/environment", responses=ERRS,
            summary="Ambiente do workspace, leitura ADN e opções efetivamente disponíveis na instalação (com motivo).")
def ambiente(company_id: str, request: Request, acc: CompanyAccess = Depends(company_access("admin")),
             s: Session = Depends(db_session)) -> dict:
    c = s.get(Company, acc.company.id)
    return _saida(c, [opcao(c, env, _status(request, c, env)) for env in OFICIAIS])


@router.post("/companies/{company_id}/environment", responses=ERRS,
             summary="Troca confirmada do ambiente do workspace e da leitura ADN (somente este workspace). Não habilita "
                     "emissão nem cancelamento e não move nem apaga dados de outros ambientes.")
def trocar_ambiente(company_id: str, body: TrocaAmbiente, request: Request,
                    acc: CompanyAccess = Depends(company_access("admin")), s: Session = Depends(db_session)) -> dict:
    from .seed import DEMO_CNPJS  # noqa: PLC0415

    c = s.get(Company, acc.company.id, with_for_update=True)
    if c.cnpj in DEMO_CNPJS:
        raise ProblemError(422, "DEMO_COMPANY_OFFICIAL_BLOCKED",
                           "Empresa de demonstração não usa ambientes oficiais (Produção Restrita/Produção).")
    conf = body.confirmation
    esperado = {"company_id": c.id, "cnpj": c.cnpj, "from_environment": c.nfse_environment,
                "to_environment": body.environment}
    diverge = [k for k, v in esperado.items() if getattr(conf, k) != v]
    if diverge:
        raise ProblemError(409, "ENVIRONMENT_CONFIRMATION_MISMATCH",
                           "A confirmação não corresponde ao workspace ou ao ambiente atual (pode ter mudado em outra "
                           "sessão). Recarregue, revise e confirme de novo. Nada foi alterado.",
                           errors=[{"field": f"confirmation.{k}", "code": "MISMATCH", "message": "Divergente."}
                                   for k in diverge])
    op = opcao(c, body.environment, _status(request, c, body.environment))
    if any(i["code"] == "INTEGRATION_UNAVAILABLE" for i in op["global_issues"]):
        raise ProblemError(503, "INTEGRATION_UNAVAILABLE", "Conector NFS-e indisponível: não foi possível conferir o "
                                                          "ambiente. Nada foi alterado.")
    if not op["available"]:
        raise ProblemError(409, "ENVIRONMENT_UNAVAILABLE",
                           f"{NOME[body.environment]} não está disponível nesta instalação. Nada foi alterado.",
                           errors=[{"field": "environment", **i} for i in op["global_issues"]])
    if body.adn_read and not op["read_available"]:
        raise ProblemError(409, "ADN_READ_UNAVAILABLE",
                           f"Leitura ADN em {NOME[body.environment]} indisponível para este workspace. Nada foi alterado.",
                           errors=[{"field": "adn_read", **i} for i in op["global_issues"] + op["company_issues"]]
                           or [{"field": "adn_read", "code": "DISTRIBUTION_DISABLED",
                                "message": "Leitura ADN desligada na instalação."}])
    antes = {"environment": c.nfse_environment, "adn_environments": sorted(c.adn_environments or [])}
    depois = {"environment": body.environment, "adn_environments": [body.environment] if body.adn_read else []}
    if antes != depois:
        now = ctx(request).clock.now()
        c.nfse_environment = depois["environment"]
        c.adn_environments = depois["adn_environments"]
        c.updated_at = now
        audit(s, now, "company.environment_changed", actor_id=acc.user.id, company_id=c.id, target_type="company",
              target_id=c.id, trace_id=trace_id_for(request),
              details={"from": antes, "to": depois, "confirmation": conf.model_dump(),
                       "installation": op["installation"], "emission_enabled_by_change": False})
        s.commit()
    return _saida(c, [opcao(c, env, _status(request, c, env)) for env in OFICIAIS]) | {"changed": antes != depois,
                                                                                         "previous": antes}
