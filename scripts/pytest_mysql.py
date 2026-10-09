"""Executa a suíte pytest contra um MySQL 8.4 TEMPORÁRIO, criado e destruído nesta execução.

- Container próprio com nome único (ctrlfiscal-tmp-mysql-<aleatório>), porta aleatória em 127.0.0.1, sem dados reais.
- Senha aleatória gerada aqui; vai ao container por --env-file temporário e ao pytest por um arquivo temporário FORA
  do projeto. Nada é impresso nem fica gravado no repositório.
- Ao final (sucesso ou falha) o container e seus volumes anônimos são removidos e o arquivo de credencial apagado.

Uso: .venv\\Scripts\\python -I scripts\\pytest_mysql.py [args do pytest]   (padrão: tests -q -p no:cacheprovider -W ignore)
"""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "mysql:8.4"


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(argv: list[str]) -> int:
    import pymysql  # noqa: PLC0415

    name = f"ctrlfiscal-tmp-mysql-{secrets.token_hex(4)}"
    port = _free_port()
    pw = secrets.token_urlsafe(24)
    workdir = Path(tempfile.mkdtemp(prefix="ctrl-pytest-"))
    env_path, url_file = workdir / "mysql.env", workdir / "url.txt"
    try:
        env_path.write_text(f"MYSQL_ROOT_PASSWORD={pw}\n", encoding="utf-8")
        _docker("run", "-d", "--name", name, "--env-file", str(env_path), "-p", f"127.0.0.1:{port}:3306",
                "--label", "ctrl-fiscal=test-temporario", IMAGE, "--max-connections=500")
        env_path.unlink()
        url_file.write_text(f"mysql+pymysql://root:{pw}@127.0.0.1:{port}", encoding="utf-8")
        for _ in range(90):
            try:
                pymysql.connect(host="127.0.0.1", port=port, user="root", password=pw, connect_timeout=2).close()
                break
            except Exception:  # noqa: BLE001
                time.sleep(2)
        else:
            raise SystemExit("MySQL temporário não ficou pronto em 180 s")
        args = argv or ["tests", "-q", "-p", "no:cacheprovider", "-W", "ignore"]
        env = {**os.environ, "CTRL_TEST_MYSQL_URL_FILE": str(url_file)}
        env.pop("CTRL_TEST_MYSQL_URL", None)
        print(f"pytest em MySQL 8.4 temporário ({name}, porta {port}); credencial não exibida; destruído ao final.", flush=True)
        return subprocess.run([sys.executable, "-m", "pytest", *args], cwd=ROOT, env=env).returncode
    finally:
        _docker("rm", "-f", "-v", name, check=False)
        for f in (env_path, url_file):
            if f.exists():
                f.unlink()
        workdir.rmdir()
        print(f"MySQL temporário {name} removido (container e volumes).", flush=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
