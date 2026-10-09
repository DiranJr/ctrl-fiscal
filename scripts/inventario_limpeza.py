"""Inventário (SOMENTE LEITURA) do ambiente operacional para a limpeza de demonstração/teste.

Classifica pela ORIGEM e pelos VÍNCULOS, não só por nome ou ambiente fiscal:
- Empresa DEMO: CNPJ em core_api.seed.DEMO_COMPANIES (criada pelo seed local) — confere também o nome.
- Usuário de TESTE: marca de auditoria de criação (core_api.test_user / core_api.homolog_user) ou criado pelo seed
  (admin local do seed: e-mail SEED_ADMIN_EMAIL padrão) — e sem vínculo com empresa real.
- Usuário LEGÍTIMO: qualquer outro; nunca é removido.
- Documento/rascunho/lote/job: herdam a classificação da empresa (company_id). Produção Restrita da empresa real é
  evidência de homologação e é preservada.
Imprime contagens e identificadores parciais; e-mails de pessoas reais são mascarados.

Uso: .venv\\Scripts\\python -I scripts\\inventario_limpeza.py [--json saida.json]
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services/shared"), str(ROOT / "services/core-api")]

from core_api.seed import DEMO_COMPANIES  # noqa: E402

DEMO_CNPJS = {c for _, c in DEMO_COMPANIES}
TEST_MARKS = ("auth.test_user_created", "auth.homolog_user_created")


def sql(q: str) -> list[list[str]]:
    out = subprocess.run(["docker", "exec", "ctrlfiscal-mysql-1", "sh", "-c",
                          f'MYSQL_PWD="$(cat /run/secrets/mysql_root_password)" mysql --default-character-set=utf8mb4 -uroot -N -B -e "{q}"'],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if out.returncode:
        raise SystemExit(out.stderr[:300])
    return [ln.split("\t") for ln in out.stdout.strip().splitlines() if ln.strip()]


def mask(email: str) -> str:
    user, _, dom = email.partition("@")
    return f"{user[:2]}***@{dom}"


def main(argv: list[str]) -> int:
    inv: dict = {"companies": [], "users": [], "fiscal": {}, "nfse": {}, "duvidas": []}
    comps = sql("select id, cnpj, name, nfse_environment, is_active from ctrl_core.companies")
    demo_ids = set()
    for cid, cnpj, name, env, active in comps:
        is_demo = cnpj in DEMO_CNPJS
        if is_demo and "Demonstra" not in name:
            inv["duvidas"].append(f"empresa {cid[:8]}: CNPJ de demonstração com nome inesperado")
        if is_demo:
            demo_ids.add(cid)
        inv["companies"].append({"id": cid, "demo": is_demo, "env": env, "active": active == "1",
                                 "name": name if is_demo else "(empresa real)"})
    marks = {r[0]: r[1] for r in sql(
        "select target_id, action from ctrl_core.audit_events where action in ('auth.test_user_created','auth.homolog_user_created')")}
    seed_marks = {r[0] for r in sql("select target_id from ctrl_core.audit_events where actor_id like 'core_api.seed%'")}
    mem = sql("select user_id, company_id, permissions from ctrl_core.memberships")
    users = sql("select id, email, is_active, created_at from ctrl_core.users")
    for uid, email, active, created in users:
        links = [m[1] for m in mem if m[0] == uid]
        real_links = [c for c in links if c not in demo_ids]
        origin = marks.get(uid) or ("seed" if uid in seed_marks else None)
        test = origin is not None or email.endswith("@ctrl.local")
        kind = "TESTE" if test else "LEGITIMO"
        if test and email.endswith("@ctrl.local") and origin is None:
            origin = "dominio ctrl.local (sem marca de auditoria)"
        inv["users"].append({"id": uid, "email": email if test else mask(email), "active": active == "1", "kind": kind,
                             "origin": origin, "demo_links": len(links) - len(real_links), "real_links": len(real_links)})
        if test and real_links:
            inv["duvidas"].append(f"usuário de teste {email} vinculado a empresa real ({len(real_links)}): vínculo removido, conta removida só se for de teste")
    by = lambda tbl, extra="": {r[0]: int(r[1]) for r in sql(f"select company_id, count(*) from {tbl} {extra} group by company_id")}  # noqa: E731
    for tbl in ("ctrl_fiscal.documents", "ctrl_fiscal.document_events", "ctrl_fiscal.drafts", "ctrl_fiscal.batches",
                "ctrl_fiscal.batch_items", "ctrl_fiscal.jobs", "ctrl_fiscal.blobs", "ctrl_fiscal.sync_schedules",
                "ctrl_fiscal.dfe_items", "ctrl_fiscal.dps_sequences", "ctrl_fiscal.idempotency_keys",
                "ctrl_fiscal.audit_events", "ctrl_core.customers", "ctrl_core.audit_events"):
        try:
            inv["fiscal"][tbl] = by(tbl)
        except SystemExit as exc:
            inv["fiscal"][tbl] = {"erro": str(exc)[:80]}
    for tbl in ("ctrl_nfse.certificates", "ctrl_nfse.submissions", "ctrl_nfse.mock_authority_records"):
        try:
            inv["nfse"][tbl] = by(tbl)
        except SystemExit as exc:
            inv["nfse"][tbl] = {"erro": str(exc)[:80]}
    # documentos/rascunhos/itens com company_id inexistente (órfãos)
    known = {c["id"] for c in inv["companies"]}
    for tbl, d in {**inv["fiscal"], **inv["nfse"]}.items():
        for cid in d:
            if cid not in known and cid not in ("erro", "NULL", None):
                inv["duvidas"].append(f"{tbl}: {d[cid]} registro(s) com company_id {str(cid)[:8]} sem empresa no core")
    # trabalhos pendentes nas empresas demo
    pend = sql("select left(company_id,8), status, submit_phase, count(*) from ctrl_fiscal.batch_items "
               "where status in ('SCHEDULED','PROCESSING','AWAITING_CONFIRMATION') group by 1,2,3")
    jobs = sql("select left(company_id,8), type, status, count(*) from ctrl_fiscal.jobs where status in ('QUEUED','RUNNING') group by 1,2,3")
    inv["pendentes"] = {"batch_items": pend, "jobs": jobs}
    short = lambda d: {("DEMO " if k in demo_ids else "REAL " if k in known else "?? ") + str(k)[:8]: v for k, v in d.items()}  # noqa: E731
    print("EMPRESAS:")
    for c in inv["companies"]:
        print(f"  {c['id'][:8]} demo={c['demo']} env={c['env']} ativa={c['active']} {c['name']}")
    print("USUÁRIOS:")
    for u in inv["users"]:
        print(f"  {u['id'][:8]} {u['kind']:8} ativo={u['active']} origem={u['origin']} {u['email']} vínculos demo={u['demo_links']} real={u['real_links']}")
    print("CONTAGENS POR EMPRESA:")
    for tbl, d in {**inv["fiscal"], **inv["nfse"]}.items():
        print(f"  {tbl}: {short(d) if isinstance(d, dict) else d}")
    print("PENDENTES:", json.dumps(inv["pendentes"], ensure_ascii=False))
    print("DÚVIDAS:", inv["duvidas"] or "nenhuma")
    if "--json" in argv:
        Path(argv[argv.index("--json") + 1]).write_text(json.dumps(inv, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
