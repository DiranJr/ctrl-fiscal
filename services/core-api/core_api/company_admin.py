"""Cadastro administrativo da empresa REAL para a integração oficial (Produção Restrita). Uso:

  python -m core_api.company_admin --owner-email EMAIL < empresa.json

- O perfil (JSON) vem do stdin — gerado por scripts/derivar_perfil_empresa.py a partir do ADN de produção
  (somente leitura) e confirmado pelo usuário. Nada é presumido: campos ausentes ficam nulos e a validação
  oficial da DPS bloqueia a emissão.
- Ambiente de EMISSÃO fixo em "restricted"; produção nunca é atribuída aqui. A leitura ADN usa
  adn_environments (padrão restricted + production, somente leitura).
- O dono recebe todas as permissões. Contas marcadas como de teste na criação (auditoria auth.test_user_created) são recusadas, e nenhuma outra
  associação é alterada.
- Idempotente pelo CNPJ. Não imprime CNPJ, nome, IM, fone ou e-mail.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from ctrl_common.config import database_url
from ctrl_common.db import make_engine, make_sessionmaker
from ctrl_common.fiscal_types import is_valid_cnpj

from .models import AuditEvent, Company, Membership, User
from .seed import ALL_PERMISSIONS
TEST_USER_MARK = "auth.test_user_created"  # marca histórica das antigas contas de teste (CLI removido)

PROFILE_FIELDS = ("municipal_registration", "simples_option", "simples_regime", "special_regime", "phone", "email")
READ_ENVS = ("restricted", "production")


def upsert_company(url: str, profile: dict, owner_email: str, adn_environments: list[str]) -> dict:
    cnpj = (profile.get("cnpj") or "").strip().upper()
    if not is_valid_cnpj(cnpj):  # DV do CNPJ do prestador (Anexo I E0080)
        raise SystemExit("CNPJ do perfil inválido")
    if not profile.get("name") or not str(profile.get("municipality_code") or "").isdigit() \
            or len(profile["municipality_code"]) != 7:
        raise SystemExit("Perfil sem razão social ou município IBGE (7 dígitos)")
    if profile.get("simples_regime") and profile.get("simples_option") != "3":
        raise SystemExit("regApTribSN só se aplica a opSimpNac=3")
    if not set(adn_environments) <= set(READ_ENVS):
        raise SystemExit("adn_environments aceita apenas restricted/production")
    now = datetime.now(timezone.utc)
    sessions = make_sessionmaker(make_engine(url))
    with sessions() as s, s.begin():
        owner = s.scalar(select(User).where(User.email == owner_email.strip().lower()))
        if owner is None or not owner.is_active:
            raise SystemExit("Conta do responsável inexistente ou inativa")
        if s.scalar(select(AuditEvent.id).where(AuditEvent.action == TEST_USER_MARK, AuditEvent.target_id == owner.id)):
            raise SystemExit("Conta de teste não pode ser responsável pela empresa real")
        company = s.scalar(select(Company).where(Company.cnpj == cnpj).with_for_update())
        created = company is None
        if created:
            company = Company(cnpj=cnpj, created_at=now, is_active=True)
            s.add(company)
        elif company.nfse_environment == "production":
            raise SystemExit("Empresa já está em produção; alteração recusada por esta ferramenta")
        company.name = profile["name"].strip()
        company.timezone = profile.get("timezone") or "America/Sao_Paulo"
        company.nfse_environment = "restricted"
        company.municipality_code = profile["municipality_code"]
        for f in PROFILE_FIELDS:
            setattr(company, f, profile.get(f) or None)
        company.fiscal_profile_source = (profile.get("fiscal_profile_source") or "cadastro administrativo")[:300]
        company.fiscal_profile_updated_at = now
        company.adn_environments = sorted(set(adn_environments))
        company.updated_at = now
        from .workspaces import ensure_workspace

        ensure_workspace(s, company, now, status="PREPARING")  # novo workspace nasce em preparação
        s.flush()
        m = s.scalar(select(Membership).where(Membership.user_id == owner.id, Membership.company_id == company.id))
        if m is None:
            s.add(Membership(user_id=owner.id, company_id=company.id, permissions=list(ALL_PERMISSIONS),
                             created_at=now, updated_at=now))
        else:
            m.permissions = list(ALL_PERMISSIONS)
            m.updated_at = now
        s.add(AuditEvent(occurred_at=now, actor_type="system", actor_id="core_api.company_admin",
                         action="company.created" if created else "company.fiscal_profile_updated",
                         target_type="company", target_id=company.id,
                         details={"environment": "restricted", "adn_environments": company.adn_environments,
                                  "fields": sorted(f for f in PROFILE_FIELDS if profile.get(f)),
                                  "source": company.fiscal_profile_source}))
        return {"company_id": company.id, "created": created,
                "missing": sorted(f for f in PROFILE_FIELDS if not profile.get(f))}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner-email", required=True)
    ap.add_argument("--adn-environments", default="restricted,production")
    a = ap.parse_args(argv)
    profile = json.loads(sys.stdin.read())
    out = upsert_company(database_url("CORE"), profile, a.owner_email,
                         [e.strip() for e in a.adn_environments.split(",") if e.strip()])
    sys.stdout.write(f"Empresa {'criada' if out['created'] else 'atualizada'} id={out['company_id']} "
                     f"ambiente=restricted; campos opcionais ausentes: {out['missing']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
