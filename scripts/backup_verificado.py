"""Backup consistente dos 3 bancos + arquivos de exportação, com TESTE DE RESTAURAÇÃO.

1. mysqldump --single-transaction dos bancos ctrl_core, ctrl_fiscal e ctrl_nfse (dentro do container MySQL; a senha
   de root é lida do segredo do próprio container e nunca sai dele);
2. cópia do volume de exportações;
3. restauração num MySQL 8.4 DESCARTÁVEL (container temporário, senha aleatória gerada aqui e passada por arquivo
   de ambiente temporário) e comparação da contagem de linhas de TODAS as tabelas com o banco de origem;
4. remove o container temporário. Gera backups/<data>-<rótulo>/VERIFICACAO.json (só contagens).

Uso: .venv\\Scripts\\python -I scripts\\backup_verificado.py <rotulo>
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = os.environ.get("CTRL_MYSQL_CONTAINER", "ctrlfiscal-mysql-1")
DBS = ["ctrl_core", "ctrl_fiscal", "ctrl_nfse"]


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, **kw)


def counts(container: str, pw_cmd: str) -> dict:
    q = ("select concat(table_schema,'.',table_name) from information_schema.tables where table_schema in "
         "('ctrl_core','ctrl_fiscal','ctrl_nfse') and table_type='BASE TABLE' order by 1")
    tables = run(["docker", "exec", container, "sh", "-c", f'{pw_cmd} mysql -uroot -N -B -e "{q}"'], text=True).stdout.split()
    union = " union all ".join(f"select '{t}', count(*) from {t}" for t in tables)
    out = run(["docker", "exec", container, "sh", "-c", f'{pw_cmd} mysql -uroot -N -B -e "{union}"'], text=True).stdout
    return {ln.split("\t")[0]: int(ln.split("\t")[1]) for ln in out.strip().splitlines()}


def main(label: str) -> int:
    dest = ROOT / "backups" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{label}"
    dest.mkdir(parents=True)
    pw_src = 'MYSQL_PWD="$(cat /run/secrets/mysql_root_password)"'
    dump = dest / "mysql-ctrl_core-fiscal-nfse.sql.gz"
    with open(dump, "wb") as fh:
        p1 = subprocess.Popen(["docker", "exec", SRC, "sh", "-c",
                               f"{pw_src} mysqldump -uroot --single-transaction --routines --triggers --databases {' '.join(DBS)} | gzip"],
                              stdout=fh, stderr=subprocess.DEVNULL)
        p1.wait()
    before = counts(SRC, pw_src)
    run(["docker", "cp", "ctrlfiscal-fiscal-api-1:/data/exports", str(dest / "exports")])
    # Restauração em container descartável
    name = f"ctrlfiscal-restore-check-{secrets.token_hex(3)}"
    pw = secrets.token_urlsafe(24)
    fd, envf = tempfile.mkstemp(prefix="restore-env-", text=True)
    with os.fdopen(fd, "w") as f:
        f.write(f"MYSQL_ROOT_PASSWORD={pw}\n")
    try:
        run(["docker", "run", "-d", "--name", name, "--env-file", envf, "--label", "ctrl-fiscal=restore-check", "mysql:8.4"], check=True)
        pw_cmd = f'MYSQL_PWD="$MYSQL_ROOT_PASSWORD"'
        # o servidor temporário de inicialização responde só por socket; o definitivo abre a porta TCP
        for _ in range(120):
            if run(["docker", "exec", name, "sh", "-c",
                    f"{pw_cmd} mysql -uroot -h127.0.0.1 --protocol=tcp -e 'select 1'"], text=True).returncode == 0:
                break
            time.sleep(2)
        with open(dump, "rb") as fh:
            r = subprocess.run(["docker", "exec", "-i", name, "sh", "-c",
                                f"gunzip | {pw_cmd} mysql -uroot -h127.0.0.1 --protocol=tcp"], stdin=fh, capture_output=True)
        restored_ok = r.returncode == 0
        if not restored_ok:
            print("restauração falhou:", r.stderr.decode(errors="replace")[:500], file=sys.stderr)
        after = counts(name, pw_cmd) if restored_ok else {}
    finally:
        run(["docker", "rm", "-f", name])
        os.remove(envf)
    diff = {t: (before.get(t), after.get(t)) for t in sorted(set(before) | set(after)) if before.get(t) != after.get(t)}
    result = {"backup": str(dest.relative_to(ROOT)), "dump_bytes": dump.stat().st_size, "tables": len(before),
              "rows_total": sum(before.values()), "restore_ok": restored_ok, "differences": diff,
              "exports_files": len(list((dest / "exports").glob("*"))) if (dest / "exports").exists() else 0,
              "note": "contagens tomadas logo após o dump; diferenças só por escrita concorrente (ex.: auditoria/sessões)"}
    (dest / "VERIFICACAO.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if restored_ok and not diff else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "verificado"))
