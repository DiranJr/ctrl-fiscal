"""Inventário SOMENTE LEITURA do banco operacional, por origem (empresa, ambiente, fonte, autor), para a limpeza de
recursos de demonstração/teste/homologação. Imprime contagens e prefixos de id; nenhum dado pessoal, segredo ou XML.

Uso: .venv\\Scripts\\python -I scripts\\inventario_operacional.py [container]   (padrão ctrlfiscal-mysql-1)
"""
from __future__ import annotations

import subprocess
import sys

Q = r"""
SELECT '== empresas', id, CONCAT(LEFT(name,12),'…'), nfse_environment, adn_environments, is_active FROM ctrl_core.companies;
SELECT '== usuarios', LEFT(id,8), CONCAT(LEFT(email,2),'***@',SUBSTRING_INDEX(email,'@',-1)), is_active, is_super_admin FROM ctrl_core.users;
SELECT '== vinculos', LEFT(user_id,8), LEFT(company_id,8), permissions FROM ctrl_core.memberships;
SELECT '== clientes', company_id, COUNT(*) FROM ctrl_core.customers GROUP BY company_id;
SELECT '== tokens_credencial', COUNT(*) FROM ctrl_core.credential_tokens;
SELECT '== docs', company_id, environment, direction, source, simulated, tp_amb, COUNT(*) FROM ctrl_fiscal.documents GROUP BY 2,3,4,5,6,7;
SELECT '== eventos', environment, source, simulated, COUNT(*) FROM ctrl_fiscal.document_events GROUP BY 2,3,4;
SELECT '== dfe_items', environment, COUNT(*) FROM ctrl_fiscal.dfe_items GROUP BY 2;
SELECT '== blobs_por_uso', 'doc', d.environment, COUNT(*) FROM ctrl_fiscal.documents d JOIN ctrl_fiscal.blobs b ON b.id=d.xml_blob_id GROUP BY d.environment;
SELECT '== blobs_total', COUNT(*) FROM ctrl_fiscal.blobs;
SELECT '== agendas', LEFT(company_id,8), environment, enabled, status, cursor_nsu FROM ctrl_fiscal.sync_schedules;
SELECT '== jobs', LEFT(id,8), type, status, environment, LEFT(created_by,8), created_at FROM ctrl_fiscal.jobs;
SELECT '== rascunhos', LEFT(id,8), status, LEFT(company_id,8), created_at FROM ctrl_fiscal.drafts;
SELECT '== lotes', COUNT(*) FROM ctrl_fiscal.batches;
SELECT '== idempotencia', operation, COUNT(*) FROM ctrl_fiscal.idempotency_keys GROUP BY operation;
SELECT '== tickets', COUNT(*) FROM ctrl_fiscal.download_tickets;
SELECT '== certificados', LEFT(company_id,8), environment, subject_cnpj IS NOT NULL, is_active, created_at FROM ctrl_nfse.certificates;
SELECT '== submissions', COUNT(*) FROM ctrl_nfse.submissions;
SELECT '== mock_authority', COUNT(*) FROM ctrl_nfse.mock_authority_records;
SELECT '== auditoria_core', COUNT(*) FROM ctrl_core.audit_events;
SELECT '== auditoria_fiscal', COUNT(*) FROM ctrl_fiscal.audit_events;
"""


def main(container: str) -> int:
    r = subprocess.run(["docker", "exec", "-i", container, "sh", "-c",
                        'MYSQL_PWD="$(cat /run/secrets/mysql_root_password)" mysql -uroot -N -B --default-character-set=utf8mb4'],
                       input=Q.encode(), capture_output=True)
    sys.stdout.reconfigure(encoding="utf-8")
    print(r.stdout.decode("utf-8", errors="replace"))
    if r.returncode:
        print(r.stderr.decode(errors="replace")[:400], file=sys.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "ctrlfiscal-mysql-1"))
