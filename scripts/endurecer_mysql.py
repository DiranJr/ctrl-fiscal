"""Endurecimento do MySQL após as migrações: auditoria IMUTÁVEL para os usuários da aplicação.

Os usuários de runtime (core, fiscal) têm DML no próprio banco; o MySQL não permite revogar UPDATE/DELETE de UMA
tabela quando o privilégio foi dado no banco inteiro. Por isso a proteção é por gatilho: qualquer UPDATE ou DELETE
em audit_events (ctrl_core e ctrl_fiscal) é recusado (SQLSTATE 45000). Os gatilhos são criados pelo root (dentro do
container, com a senha lida do segredo do próprio container; nada é exibido). Idempotente.

Uso: .venv\\Scripts\\python -I scripts\\endurecer_mysql.py [container]   (padrão: ctrlfiscal-mysql-1)
"""
from __future__ import annotations

import subprocess
import sys

SQL = "\n".join(
    f"""DROP TRIGGER IF EXISTS {db}.audit_events_imutavel_upd;
CREATE TRIGGER {db}.audit_events_imutavel_upd BEFORE UPDATE ON {db}.audit_events FOR EACH ROW
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'auditoria imutavel: UPDATE recusado';
DROP TRIGGER IF EXISTS {db}.audit_events_imutavel_del;
CREATE TRIGGER {db}.audit_events_imutavel_del BEFORE DELETE ON {db}.audit_events FOR EACH ROW
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'auditoria imutavel: DELETE recusado';"""
    for db in ("ctrl_core", "ctrl_fiscal")
) + "\nSELECT trigger_schema, trigger_name FROM information_schema.triggers WHERE trigger_name LIKE 'audit_events_imutavel%';\n"


def main(container: str) -> int:
    r = subprocess.run(["docker", "exec", "-i", container, "sh", "-c",
                        'MYSQL_PWD="$(cat /run/secrets/mysql_root_password)" mysql -uroot -N -B'],
                       input=SQL.encode(), capture_output=True)
    print(r.stdout.decode(errors="replace").strip())
    if r.returncode:
        print("falhou:", r.stderr.decode(errors="replace")[:400], file=sys.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "ctrlfiscal-mysql-1"))
