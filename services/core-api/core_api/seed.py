"""Seed LOCAL explícito. Uso: python -m core_api.seed

Exige SEED_ADMIN_EMAIL e SEED_ADMIN_PASSWORD (ou SEED_ADMIN_PASSWORD_FILE).
Não existe senha padrão. Recusa execução com APP_ENV=production.
Cria duas empresas de DEMONSTRAÇÃO (CNPJs fictícios com DV válido) para
exercitar o isolamento entre empresas; não são dados fiscais reais.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from sqlalchemy import select

from ctrl_common.config import ConfigError, database_url, env, secret
from ctrl_common.db import make_engine, make_sessionmaker
from ctrl_common.fiscal_types import _cnpj_dv

from .models import Company, Customer, Membership, User
from .security import hash_password

def _demo_cnpjs() -> frozenset[str]:
    return frozenset(c for _, c in DEMO_COMPANIES)


ALL_PERMISSIONS = ["view", "import", "sync", "review", "prepare", "schedule", "emit", "cancel", "certificates", "admin"]


def demo_cnpj(base12: str) -> str:
    return base12 + _cnpj_dv(base12)


DEMO_COMPANIES = [
    ("Empresa Demonstração A (LOCAL)", demo_cnpj("112223330001")),
    ("Empresa Demonstração B (LOCAL)", demo_cnpj("998887770001")),
]
DEMO_CUSTOMERS = [
    ("Cliente Demonstração 1 (LOCAL)", demo_cnpj("445556660001")),
    ("Cliente Demonstração 2 (LOCAL)", demo_cnpj("778889990001")),
]


def run(url: str, email: str, password: str, name: str = "Administrador local") -> dict:
    if len(password) < 12:
        raise ConfigError("SEED_ADMIN_PASSWORD deve ter ao menos 12 caracteres")
    now = datetime.now(timezone.utc)
    sessions = make_sessionmaker(make_engine(url))
    created = {"user": False, "companies": 0, "customers": 0}
    with sessions() as s:
        user = s.scalar(select(User).where(User.email == email.lower()))
        if user is None:
            user = User(email=email.lower(), name=name, password_hash=hash_password(password),
                        is_active=True, created_at=now, updated_at=now)
            s.add(user)
            s.flush()
            created["user"] = True
        for cname, cnpj in DEMO_COMPANIES:
            company = s.scalar(select(Company).where(Company.cnpj == cnpj))
            if company is None:
                company = Company(name=cname, cnpj=cnpj, timezone="America/Sao_Paulo", nfse_environment="mock", dps_series="1",
                                  is_active=True, created_at=now, updated_at=now)
                s.add(company)
                s.flush()
                created["companies"] += 1
            from .workspaces import ensure_workspace

            ensure_workspace(s, company, now, status="ACTIVE")
            if s.scalar(select(Membership).where(Membership.user_id == user.id, Membership.company_id == company.id)) is None:
                s.add(Membership(user_id=user.id, company_id=company.id, permissions=ALL_PERMISSIONS,
                                 created_at=now, updated_at=now))
            for cust_name, doc in DEMO_CUSTOMERS:
                exists = s.scalar(select(Customer).where(Customer.company_id == company.id, Customer.document == doc))
                if exists is None:
                    s.add(Customer(company_id=company.id, name=cust_name, document_type="CNPJ", document=doc,
                                   is_active=True, version=1, created_at=now, updated_at=now))
                    created["customers"] += 1
        s.commit()
    return created


def main() -> int:
    # Demonstração DESLIGADA por padrão: nada repovoa o ambiente operacional. Só roda com confirmação explícita.
    if env("ALLOW_DEMO_SEED", "false").lower() != "true":
        sys.stderr.write("Seed de demonstração desabilitado (defina ALLOW_DEMO_SEED=true só em ambiente descartável).\n")
        return 2
    if env("APP_ENV", "production") == "production":
        sys.stderr.write("Seed local recusado com APP_ENV=production.\n")
        return 2
    email = env("SEED_ADMIN_EMAIL")
    password = secret("SEED_ADMIN_PASSWORD", required=False)
    if not email or not password:
        sys.stderr.write("Defina SEED_ADMIN_EMAIL e SEED_ADMIN_PASSWORD_FILE (ou SEED_ADMIN_PASSWORD).\n")
        return 2
    result = run(database_url("CORE"), email, password)
    sys.stdout.write(f"Seed concluído: {result}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Empresas de demonstração (CNPJs fictícios do seed): sem certificado, sem leitura ADN nem emissão oficiais.
DEMO_CNPJS = _demo_cnpjs()
