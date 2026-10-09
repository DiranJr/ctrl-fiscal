"""Limpeza do ambiente operacional: remove demonstração/teste identificados pela ORIGEM (scripts/inventario_limpeza.py).

Remove:
- empresas de demonstração (CNPJs de core_api.seed.DEMO_COMPANIES) e TODOS os dados dependentes (clientes, vínculos,
  rascunhos, lotes, itens, jobs, documentos, eventos, blobs, itens ADN, agendas, sequências, chaves de idempotência,
  certificados sintéticos, submissões e registros simulados do conector, arquivos de exportação);
- contas de teste: marcadas na criação (auth.test_user_created / auth.homolog_user_created) ou o admin do seed
  (criado no mesmo instante das empresas de demonstração e só vinculado a elas).
Preserva: empresa real e seus dados (inclusive Produção Restrita = evidência de homologação), usuários legítimos e
TODA a auditoria (core e fiscal). Registra a limpeza na auditoria (sem dados pessoais).

Segurança:
- padrão = simulação (só conta). --aplicar exige --backup com dump existente;
- trabalhos pendentes: itens SCHEDULED/fase NONE/sem lease → WITHDRAWN (retirada segura); qualquer item em
  processamento, aguardando confirmação ou com lease ativo ABORTA a limpeza (nada é apagado);
- tudo numa única transação MySQL; se uma checagem falhar, o cliente aborta e nada é gravado.

Uso: .venv\\Scripts\\python -I scripts\\limpeza_ambiente.py [--aplicar --backup backups/<dir>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services/shared"), str(ROOT / "services/core-api")]

from core_api.seed import DEMO_COMPANIES  # noqa: E402

DEMO_CNPJS = sorted(c for _, c in DEMO_COMPANIES)
TEST_MARKS = ("auth.test_user_created", "auth.homolog_user_created")
FISCAL_BY_COMPANY = ["batch_items", "batches", "drafts", "document_events", "documents", "dfe_items", "jobs", "blobs",
                     "sync_schedules", "dps_sequences", "idempotency_keys"]


def mysql(sql: str, stdin: bool = False) -> list[list[str]]:
    cmd = ["docker", "exec", "-i", "ctrlfiscal-mysql-1", "sh", "-c",
           'MYSQL_PWD="$(cat /run/secrets/mysql_root_password)" mysql --default-character-set=utf8mb4 -uroot -N -B'
           + ("" if stdin else f' -e "{sql}"')]
    out = subprocess.run(cmd, input=sql if stdin else None, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=600)
    if out.returncode:
        raise RuntimeError(out.stderr.strip()[:500])
    return [ln.split("\t") for ln in out.stdout.strip().splitlines() if ln.strip()]


def q(ids: list[str]) -> str:
    return ",".join("'" + i.replace("'", "") + "'" for i in ids) or "''"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--backup")
    a = ap.parse_args(argv)
    if a.aplicar:
        dump = Path(a.backup or "") / "mysql-ctrl_core-fiscal-nfse.sql.gz"
        if not dump.is_file() or dump.stat().st_size < 1_000_000:
            print("ABORTADO: --aplicar exige --backup com dump completo existente.")
            return 2

    demo = [r[0] for r in mysql(f"select id from ctrl_core.companies where cnpj in ({q(DEMO_CNPJS)})")]
    seed_ts = {r[0] for r in mysql(f"select created_at from ctrl_core.companies where id in ({q(demo)})")}
    marked = {r[0] for r in mysql(f"select target_id from ctrl_core.audit_events where action in ({q(list(TEST_MARKS))})")}
    users = mysql("select id, email, created_at from ctrl_core.users")
    test_users, duvidas = [], []
    for uid, email, created in users:
        links = {r[0] for r in mysql(f"select company_id from ctrl_core.memberships where user_id='{uid}'")}
        if uid in marked:
            test_users.append(uid)
        elif created in seed_ts and links and links <= set(demo):
            test_users.append(uid)  # admin do seed: criado junto com as empresas demo e só vinculado a elas
        elif email.endswith("@ctrl.local"):
            duvidas.append(f"usuário {uid[:8]} com domínio de teste sem marca de origem: PRESERVADO")
    jobs = mysql(f"select id, coalesce(output_path,'') from ctrl_fiscal.jobs where company_id in ({q(demo)})")
    job_ids = [r[0] for r in jobs]

    # Trabalhos pendentes nas empresas demo
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    uncertain = mysql(f"select count(*) from ctrl_fiscal.batch_items where company_id in ({q(demo)}) and ("
                      f"status in ('PROCESSING','AWAITING_CONFIRMATION') or (status='SCHEDULED' and (submit_phase<>'NONE' "
                      f"or (lease_expires_at is not null and lease_expires_at > '{now}'))))")[0][0]
    withdrawable = mysql(f"select count(*) from ctrl_fiscal.batch_items where company_id in ({q(demo)}) and status='SCHEDULED' "
                         f"and submit_phase='NONE' and (lease_expires_at is null or lease_expires_at <= '{now}')")[0][0]
    running_jobs = mysql(f"select count(*) from ctrl_fiscal.jobs where company_id in ({q(demo)}) and status in ('QUEUED','RUNNING')")[0][0]

    counts = {}
    for t in FISCAL_BY_COMPANY:
        counts[f"ctrl_fiscal.{t}"] = int(mysql(f"select count(*) from ctrl_fiscal.{t} where company_id in ({q(demo)})")[0][0])
    counts["ctrl_nfse.certificates"] = int(mysql(f"select count(*) from ctrl_nfse.certificates where company_id in ({q(demo)})")[0][0])
    counts["ctrl_nfse.submissions"] = int(mysql(f"select count(*) from ctrl_nfse.submissions where company_id in ({q(demo)})")[0][0])
    counts["ctrl_nfse.mock_authority_records"] = int(mysql(
        f"select count(*) from ctrl_nfse.mock_authority_records where company_cnpj in ({q(DEMO_CNPJS)})")[0][0])
    counts["ctrl_core.customers"] = int(mysql(f"select count(*) from ctrl_core.customers where company_id in ({q(demo)})")[0][0])
    counts["ctrl_core.memberships"] = int(mysql(
        f"select count(*) from ctrl_core.memberships where company_id in ({q(demo)}) or user_id in ({q(test_users)})")[0][0])
    counts["ctrl_core.companies"] = len(demo)
    counts["ctrl_core.users"] = len(test_users)
    files = [r[1] for r in jobs if len(r) > 1 and r[1].startswith("/data/exports/")]
    report = {"demo_companies": [d[:8] for d in demo], "test_users": [u[:8] for u in test_users],
              "pending": {"withdrawable": int(withdrawable), "uncertain": int(uncertain), "running_jobs": int(running_jobs)},
              "counts": counts, "export_files": len(files), "duvidas": duvidas}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if not a.aplicar:
        print("SIMULAÇÃO: nada foi alterado.")
        return 0
    if int(uncertain) or int(running_jobs):
        print("ABORTADO: há itens em processamento/confirmação incerta ou jobs em execução nas empresas demo.")
        return 3

    D, J, U = q(demo), q(job_ids), q(test_users)
    guard = lambda cond: f"SELECT IF(({cond}) > 0, (SELECT 1 UNION SELECT 2), 0);"  # noqa: E731  aborta o script
    summary = json.dumps({"removed": counts, "demo_companies": [d[:8] for d in demo],
                          "test_users": [hashlib.sha256(u.encode()).hexdigest()[:12] for u in test_users]})
    sql = f"""
SET SESSION innodb_lock_wait_timeout = 30;
START TRANSACTION;
SELECT id FROM ctrl_fiscal.batch_items WHERE company_id IN ({D}) FOR UPDATE;
{guard(f"select count(*) from ctrl_fiscal.batch_items where company_id in ({D}) and (status in ('PROCESSING','AWAITING_CONFIRMATION') or (status='SCHEDULED' and (submit_phase<>'NONE' or (lease_expires_at is not null and lease_expires_at > UTC_TIMESTAMP()))))")}
UPDATE ctrl_fiscal.batch_items SET status='WITHDRAWN', finished_at=UTC_TIMESTAMP(6), updated_at=UTC_TIMESTAMP(6), fencing_token=fencing_token+1
  WHERE company_id IN ({D}) AND status='SCHEDULED' AND submit_phase='NONE';
INSERT INTO ctrl_fiscal.audit_events (id, occurred_at, actor_type, actor_id, company_id, action, target_type, target_id, details)
  SELECT UUID(), UTC_TIMESTAMP(6), 'system', 'scripts/limpeza_ambiente', company_id, 'cleanup.pending_withdrawn', 'batch_item', id, NULL
  FROM ctrl_fiscal.batch_items WHERE company_id IN ({D}) AND status='WITHDRAWN' AND finished_at >= UTC_TIMESTAMP() - INTERVAL 1 MINUTE;
DELETE FROM ctrl_fiscal.batch_items WHERE company_id IN ({D});
DELETE FROM ctrl_fiscal.batches WHERE company_id IN ({D});
DELETE FROM ctrl_fiscal.drafts WHERE company_id IN ({D});
DELETE FROM ctrl_fiscal.jobs WHERE id IN ({J});
DELETE FROM ctrl_fiscal.document_events WHERE company_id IN ({D});
DELETE FROM ctrl_fiscal.documents WHERE company_id IN ({D});
DELETE FROM ctrl_fiscal.dfe_items WHERE company_id IN ({D});
DELETE FROM ctrl_fiscal.blobs WHERE company_id IN ({D});
DELETE FROM ctrl_fiscal.sync_schedules WHERE company_id IN ({D});
DELETE FROM ctrl_fiscal.dps_sequences WHERE company_id IN ({D});
DELETE FROM ctrl_fiscal.idempotency_keys WHERE company_id IN ({D});
DELETE FROM ctrl_nfse.certificates WHERE company_id IN ({D});
DELETE FROM ctrl_nfse.submissions WHERE company_id IN ({D});
DELETE FROM ctrl_nfse.mock_authority_records WHERE company_cnpj IN ({q(DEMO_CNPJS)});
DELETE FROM ctrl_core.customers WHERE company_id IN ({D});
DELETE FROM ctrl_core.users WHERE id IN ({U});
DELETE FROM ctrl_core.companies WHERE id IN ({D});
INSERT INTO ctrl_core.audit_events (id, occurred_at, actor_type, actor_id, action, target_type, details)
  VALUES (UUID(), UTC_TIMESTAMP(6), 'system', 'scripts/limpeza_ambiente', 'cleanup.demo_and_test_removed', 'environment', '{summary}');
INSERT INTO ctrl_fiscal.audit_events (id, occurred_at, actor_type, actor_id, action, target_type, details)
  VALUES (UUID(), UTC_TIMESTAMP(6), 'system', 'scripts/limpeza_ambiente', 'cleanup.demo_and_test_removed', 'environment', '{summary}');
COMMIT;
"""
    mysql(sql, stdin=True)
    if files:
        safe = [p for p in files if "/" not in p[len("/data/exports/"):]]
        subprocess.run(["docker", "exec", "ctrlfiscal-fiscal-api-1", "rm", "-f", *safe, *[p + ".tmp" for p in safe]],
                       capture_output=True, timeout=120)
    print("APLICADO: transação confirmada; arquivos de exportação removidos:", len(files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
