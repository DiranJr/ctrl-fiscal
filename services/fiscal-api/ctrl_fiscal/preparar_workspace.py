"""Preparação assistida de um workspace a partir das notas EMITIDAS (execução pela equipe da plataforma).

Uso (dentro do container fiscal-api):
  python -m ctrl_fiscal.preparar_workspace --company <id>            # simulação: só mostra o que seria feito
  python -m ctrl_fiscal.preparar_workspace --company <id> --aplicar  # cria perfis PENDENTES e completa campos vazios

Mesma lógica das ações da tela de Configurações (inventário + preparação idempotente + cadastro só em campos vazios),
registrada na auditoria do core como ator de sistema. Nada é aprovado; nenhum valor existente é sobrescrito.
Saída sem dados pessoais: apenas contagens, códigos e nomes de campos.
"""
from __future__ import annotations

import argparse
import sys

import httpx

from .clients import CompanyInfo, CoreClient
from .domain import inventario_servicos as inv
from .settings import FiscalSettings

ATOR = "cli:preparar_workspace"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True)
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args(argv)
    from ctrl_common.db import make_engine, make_sessionmaker

    settings = FiscalSettings.from_env("fiscal-api")
    core = CoreClient(httpx.Client(base_url=settings.core_api_url, timeout=15.0), settings.core_service_name,
                      settings.core_api_key)
    company = next((c for c in core.companies() if c.id == a.company), None)
    if company is None:
        print("workspace não encontrado ou inativo", file=sys.stderr)
        return 2
    sessions = make_sessionmaker(make_engine(settings.database_url))
    with sessions() as s:
        data = inv.inventario(s, company)
        props = [inv.proposta(g) for g in data["grupos"]]
        prest = inv.dados_prestador(s, company)
    values, sources = inv.valores_seguros(prest)
    print(f"notas emitidas lidas: {data['total_notas']} ({data['periodo']['de']} a {data['periodo']['ate']})")
    for p in props:
        print(f"  grupo {p['origin_key']}: {p['evidence']['total_notas']} nota(s); campos preenchidos: "
              f"{sorted(k for k, v in p['fields'].items() if v)}; divergências: "
              f"{[d['code'] for d in p['evidence']['divergencias']]}")
    print(f"cadastro do prestador — campos consistentes nas notas: {sorted(values)}")
    if not a.aplicar:
        print("simulação: nada foi gravado (use --aplicar)")
        return 0
    r = core.prepare_service_profiles(company.id, props, ATOR, actor_type="system")
    print(f"perfis criados: {len(r['created'])}; já existentes (não alterados): {len(r['existing'])}")
    c = core.complete_provider(company.id, values, sources, ATOR, actor_type="system")
    print(f"cadastro: preenchidos {sorted(c['applied'])}; iguais {sorted(c['equal'])}; "
          f"divergentes (não alterados) {[d['field'] for d in c['divergent']]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "CompanyInfo"]
