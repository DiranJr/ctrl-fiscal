"""Retira do OPERACIONAL os dados e recursos de homologação (Produção Restrita) e de teste, por ORIGEM confirmada.

Decisão do titular (08/10/2026): o operacional mantém somente o ambiente real (produção). Critérios de origem
(nunca só pelo nome), conferidos antes de qualquer remoção:
- notas: empresa real (CTRL_EMPRESA_ID), environment='restricted', tpAmb=2 (homologação), source='ADN', direction='RECEIVED';
- itens ADN, blobs e eventos dessas notas/desse ambiente; agenda ADN 'restricted';
- jobs SYNC do ambiente 'restricted' (e erros/arquivos/chaves de idempotência que apontam para eles);
- rascunho de homologação: environment='restricted', criado por conta de TESTE já removida (marca de auditoria
  auth.homolog_user_created/auth.test_user_created) e nunca transmitido (sem lote);
- cadastro do certificado da empresa para o ambiente 'restricted' (cópia cifrada do MESMO certificado real; o cadastro de
  produção é preservado);
- empresa: ambiente de emissão e leitura ADN passam a ser somente 'production' (emissão em produção continua
  bloqueada no código).
Preservados: notas/eventos de produção, cliente, usuário real, certificado de produção, auditoria (só INSERÇÃO).
Cada etapa grava um evento de auditoria com as quantidades (ids só como hash). Requer backup verificado antes.

Uso: .venv\\Scripts\\python -I scripts\\limpeza_homologacao.py            (simulação: só conta)
     .venv\\Scripts\\python -I scripts\\limpeza_homologacao.py --executar
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

CONTAINER = "ctrlfiscal-mysql-1"
EMPRESA = os.environ.get("CTRL_EMPRESA_ID", "")  # id da empresa real (informado na execução; não versionado)


def sql(q: str) -> list[list[str]]:
    r = subprocess.run(["docker", "exec", "-i", CONTAINER, "sh", "-c",
                        'MYSQL_PWD="$(cat /run/secrets/mysql_root_password)" mysql -uroot -N -B --default-character-set=utf8mb4'],
                       input=q.encode(), capture_output=True)
    if r.returncode:
        raise SystemExit("SQL falhou: " + r.stderr.decode(errors="replace")[:400])
    return [ln.split("\t") for ln in r.stdout.decode("utf-8", errors="replace").splitlines() if ln]


def ids(q: str) -> list[str]:
    return [r[0] for r in sql(q)]


def inlist(xs: list[str]) -> str:
    return ",".join("'" + x.replace("'", "") + "'" for x in xs) or "''"


def h(xs: list[str]) -> list[str]:
    return [hashlib.sha256(x.encode()).hexdigest()[:12] for x in xs]


def main(executar: bool) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if not EMPRESA:
        raise SystemExit("Informe o id da empresa em CTRL_EMPRESA_ID (nada foi consultado nem removido).")
    docs = ids(f"SELECT id FROM ctrl_fiscal.documents WHERE company_id='{EMPRESA}' AND environment='restricted' "
               "AND tp_amb='2' AND source='ADN' AND direction='RECEIVED'")
    outros_restritos = int(sql("SELECT COUNT(*) FROM ctrl_fiscal.documents WHERE environment='restricted'")[0][0]) - len(docs)
    dfe = ids(f"SELECT id FROM ctrl_fiscal.dfe_items WHERE company_id='{EMPRESA}' AND environment='restricted'")
    blob_ids = sorted(set(ids(f"SELECT xml_blob_id FROM ctrl_fiscal.documents WHERE id IN ({inlist(docs)}) AND xml_blob_id IS NOT NULL")
                          + ids(f"SELECT blob_id FROM ctrl_fiscal.dfe_items WHERE id IN ({inlist(dfe)}) AND blob_id IS NOT NULL")))
    # blob só sai se NADA de produção o referenciar
    usados_prod = set(ids(f"SELECT xml_blob_id FROM ctrl_fiscal.documents WHERE environment<>'restricted' AND xml_blob_id IN ({inlist(blob_ids)})")
                      + ids(f"SELECT blob_id FROM ctrl_fiscal.dfe_items WHERE environment<>'restricted' AND blob_id IN ({inlist(blob_ids)})")
                      + ids(f"SELECT blob_id FROM ctrl_fiscal.document_events WHERE environment<>'restricted' AND blob_id IN ({inlist(blob_ids)})"))
    blobs = [b for b in blob_ids if b not in usados_prod]
    eventos = ids(f"SELECT id FROM ctrl_fiscal.document_events WHERE company_id='{EMPRESA}' AND environment='restricted'")
    jobs = ids(f"SELECT id FROM ctrl_fiscal.jobs WHERE company_id='{EMPRESA}' AND environment='restricted' AND type='SYNC'")
    idem = [r[0] for r in sql("SELECT id, response_body FROM ctrl_fiscal.idempotency_keys WHERE operation='sync.run'")
            if any(j in r[1] for j in jobs)]
    agendas = ids(f"SELECT id FROM ctrl_fiscal.sync_schedules WHERE company_id='{EMPRESA}' AND environment='restricted'")
    marcados = set(ids("SELECT target_id FROM ctrl_core.audit_events WHERE action IN "
                       "('auth.homolog_user_created','auth.test_user_created')"))
    existentes = set(ids("SELECT id FROM ctrl_core.users"))
    rasc = [r[0] for r in sql(f"SELECT id, created_by FROM ctrl_fiscal.drafts WHERE company_id='{EMPRESA}' AND environment='restricted' "
                              "AND current_item_id IS NULL AND status='DRAFT'")
            if r[1] in marcados and r[1] not in existentes]
    certs = ids(f"SELECT id FROM ctrl_nfse.certificates WHERE company_id='{EMPRESA}' AND environment='restricted'")
    cert_prod = int(sql(f"SELECT COUNT(*) FROM ctrl_nfse.certificates WHERE company_id='{EMPRESA}' AND environment='production' "
                        "AND is_active=1")[0][0])
    empresa = sql(f"SELECT nfse_environment, adn_environments FROM ctrl_core.companies WHERE id='{EMPRESA}'")[0]
    plano = {"documentos_homologacao": len(docs), "outros_documentos_restricted_nao_tocados": outros_restritos,
             "itens_adn": len(dfe), "blobs": len(blobs), "blobs_preservados_por_uso_em_producao": len(usados_prod),
             "eventos": len(eventos), "jobs_sync": len(jobs), "chaves_idempotencia": len(idem), "agendas": len(agendas),
             "rascunhos_homologacao": len(rasc), "certificado_restricted": len(certs),
             "certificado_producao_ativo_preservado": cert_prod, "empresa_antes": empresa}
    print(json.dumps(plano, ensure_ascii=False))
    if cert_prod != 1:
        raise SystemExit("ABORTADO: certificado de produção da empresa não confirmado; nada removido.")
    if not executar:
        return 0
    D, B, E, J, I, A, R = (inlist(x) for x in (docs, blobs, eventos, jobs, idem, agendas, rasc))
    detalhes = json.dumps({**{k: v for k, v in plano.items() if k != "empresa_antes"},
                           "ids_documentos_sha256_12": h(docs), "ids_jobs_sha256_12": h(jobs)}).replace("'", "")
    sql(f"""
START TRANSACTION;
DELETE FROM ctrl_fiscal.job_errors WHERE job_id IN ({J});
DELETE FROM ctrl_fiscal.job_files WHERE job_id IN ({J});
DELETE FROM ctrl_fiscal.job_documents WHERE job_id IN ({J}) OR document_id IN ({D});
DELETE FROM ctrl_fiscal.download_tickets WHERE job_id IN ({J});
DELETE FROM ctrl_fiscal.jobs WHERE id IN ({J});
DELETE FROM ctrl_fiscal.idempotency_keys WHERE id IN ({I});
DELETE FROM ctrl_fiscal.cancellation_requests WHERE document_id IN ({D});
DELETE FROM ctrl_fiscal.document_events WHERE id IN ({E});
DELETE FROM ctrl_fiscal.dfe_items WHERE id IN ({inlist(dfe)});
DELETE FROM ctrl_fiscal.documents WHERE id IN ({D});
DELETE FROM ctrl_fiscal.blobs WHERE id IN ({B});
DELETE FROM ctrl_fiscal.sync_schedules WHERE id IN ({A});
DELETE FROM ctrl_fiscal.drafts WHERE id IN ({R});
INSERT INTO ctrl_fiscal.audit_events (id, occurred_at, actor_type, actor_id, company_id, action, target_type, target_id, details, trace_id)
  VALUES (UUID(), UTC_TIMESTAMP(6), 'system', 'scripts/limpeza_homologacao.py', '{EMPRESA}', 'cleanup.homologation_removed',
          'environment', 'restricted', '{detalhes}', NULL);
COMMIT;
START TRANSACTION;
DELETE FROM ctrl_nfse.certificates WHERE id IN ({inlist(certs)});
COMMIT;
START TRANSACTION;
UPDATE ctrl_core.companies SET nfse_environment='production', adn_environments=JSON_ARRAY('production'), updated_at=UTC_TIMESTAMP(6)
  WHERE id='{EMPRESA}';
INSERT INTO ctrl_core.audit_events (id, occurred_at, actor_type, actor_id, company_id, action, target_type, target_id, details, trace_id)
  VALUES (UUID(), UTC_TIMESTAMP(6), 'system', 'scripts/limpeza_homologacao.py', '{EMPRESA}', 'cleanup.homologation_retired',
          'company', '{EMPRESA}', JSON_OBJECT('nfse_environment_antes', '{empresa[0]}', 'adn_environments_antes', CAST('{empresa[1]}' AS JSON),
          'nfse_environment', 'production', 'adn_environments', JSON_ARRAY('production'), 'certificado_restricted_removido', {len(certs)}), NULL);
COMMIT;
""")
    depois = sql(f"""SELECT
 (SELECT COUNT(*) FROM ctrl_fiscal.documents WHERE environment='restricted'),
 (SELECT COUNT(*) FROM ctrl_fiscal.dfe_items WHERE environment='restricted'),
 (SELECT COUNT(*) FROM ctrl_fiscal.jobs WHERE environment='restricted'),
 (SELECT COUNT(*) FROM ctrl_fiscal.sync_schedules WHERE environment='restricted'),
 (SELECT COUNT(*) FROM ctrl_fiscal.drafts),
 (SELECT COUNT(*) FROM ctrl_nfse.certificates WHERE environment='restricted'),
 (SELECT COUNT(*) FROM ctrl_nfse.certificates WHERE environment='production' AND is_active=1),
 (SELECT nfse_environment FROM ctrl_core.companies WHERE id='{EMPRESA}'),
 (SELECT COUNT(*) FROM ctrl_fiscal.documents WHERE environment='production')""")[0]
    print("depois:", json.dumps(dict(zip(["docs_restricted", "itens_adn_restricted", "jobs_restricted", "agendas_restricted",
                                          "rascunhos", "cert_restricted", "cert_producao_ativo", "ambiente_empresa",
                                          "docs_producao"], depois)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main("--executar" in sys.argv))
