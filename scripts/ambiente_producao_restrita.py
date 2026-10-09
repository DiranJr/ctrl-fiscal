"""Ambiente TEMPORÁRIO para validar o CTRL Fiscal contra a PRODUÇÃO RESTRITA oficial com a empresa e o certificado da AEL.

Isolamento:
- Cópia do operacional restaurada de um backup VERIFICADO (backups/<…>/mysql-*.sql.gz). Nada é escrito no operacional.
- Projeto Compose próprio (ctrlfiscal-pr), porta 8097, rede e volumes próprios, segredos próprios em secrets-pr/ —
  exceto a chave do cofre, copiada do operacional só para o conector temporário abrir o certificado (o arquivo é
  apagado em "destruir"). Nenhum segredo é exibido.
- Chaves: NFSE_RESTRICTED_ENABLED=true; TODAS as de produção desligadas (emissão, cancelamento, distribuição);
  governo simulado desligado. A empresa passa a usar o ambiente "restricted" SÓ NA CÓPIA.
- O certificado da AEL é recifrado para "restricted" DENTRO do conector temporário (mesma validação da plataforma:
  cadeia ICP-Brasil, revogação, usos, validade e CNPJ); o material nunca sai do container.

Uso (.venv\\Scripts\\python -I scripts\\ambiente_producao_restrita.py <comando>):
  preparar             segredos e .env.pr (não sobrescreve)
  restaurar <backup>   sobe o MySQL temporário e carrega o dump do backup indicado
  subir                sobe a pilha (migrações aplicadas NA CÓPIA) e espera o gateway
  ajustar <company_id> empresa em "restricted", usuário de validação fictício e certificado para "restricted"
  destruir             derruba a pilha, apaga volumes, secrets-pr/ e .env.pr
"""
from __future__ import annotations

import base64
import gzip
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEC = ROOT / "secrets-pr"
ENV = ROOT / ".env.pr"
PROJECT = "ctrlfiscal-pr"
PORT = 8097
BASE = f"http://127.0.0.1:{PORT}"
USUARIO = "validacao.pr@temporario.invalid"
PERMS = ["view", "import", "sync", "review", "prepare", "schedule", "emit", "cancel", "certificates", "admin"]


def compose(*args: str, check: bool = True, **kw) -> subprocess.CompletedProcess:
    env = {**os.environ, "SECRETS_DIR": "./secrets-pr", "IMAGE_TAG": os.environ.get("IMAGE_TAG", "rc")}
    return subprocess.run(["docker", "compose", "-p", PROJECT, "--env-file", str(ENV), *args], cwd=ROOT, env=env,
                          check=check, **kw)


def preparar() -> None:
    SEC.mkdir(exist_ok=True)

    def put(name: str, value: str) -> None:
        p = SEC / name
        if not p.exists():
            p.write_text(value, encoding="utf-8")

    for n in ("mysql_root_password", "core_db_password", "core_migrate_db_password", "fiscal_db_password",
              "fiscal_migrate_db_password", "nfse_db_password", "nfse_migrate_db_password", "csrf_secret",
              "internal_fiscal_api_to_core", "internal_fiscal_worker_to_core", "internal_fiscal_worker_to_nfse",
              "internal_core_to_nfse"):
        put(f"{n}.txt", secrets.token_hex(32))
    # Chave do cofre do operacional: necessária para abrir o certificado já cifrado na cópia (não é exibida).
    if not (SEC / "nfse_vault_key.txt").exists():
        shutil.copyfile(ROOT / "secrets" / "nfse_vault_key.txt", SEC / "nfse_vault_key.txt")
    if not (SEC / "jwt_private_key.pem").exists():
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        k = Ed25519PrivateKey.generate()
        put("jwt_private_key.pem", k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()).decode())
        put("jwt_public_key.pem", k.public_key().public_bytes(serialization.Encoding.PEM,
                                                              serialization.PublicFormat.SubjectPublicKeyInfo).decode())
    put("senha-validacao.txt", "Pr-" + secrets.token_urlsafe(20))
    over = {"HTTP_PORT": str(PORT), "ALLOWED_ORIGINS": f"{BASE},http://localhost:{PORT}", "APP_ENV": "development",
            "NFSE_RESTRICTED_ENABLED": "true", "NFSE_PRODUCTION_DISTRIBUTION_ENABLED": "false",
            "NFSE_PRODUCTION_CANCELLATION_ENABLED": "false", "NFSE_PRODUCTION_EMISSION_ENABLED": "false",
            "NFSE_MOCK_ENABLED": "false", "IMAGE_TAG": os.environ.get("IMAGE_TAG", "rc"), "DANFSE_FONT_DIR": ""}
    out, seen = [], set()
    for ln in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        k = ln.split("=", 1)[0].strip()
        if k in over:
            out.append(f"{k}={over[k]}")
            seen.add(k)
        else:
            out.append(ln)
    out += [f"{k}={v}" for k, v in over.items() if k not in seen]
    out.insert(0, "# GERADO por scripts/ambiente_producao_restrita.py — TEMPORÁRIO (Produção Restrita; produção desligada)")
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("preparado: secrets-pr/ e .env.pr (valores não exibidos)")


def _mysql_container() -> str:
    return f"{PROJECT}-mysql-1"


def restaurar(backup: str) -> None:
    dump = next((ROOT / backup).glob("mysql-*.sql.gz"))
    compose("up", "-d", "mysql")
    for _ in range(90):
        r = subprocess.run(["docker", "inspect", "-f", "{{.State.Health.Status}}", _mysql_container()],
                           capture_output=True, text=True)
        if r.stdout.strip() == "healthy":
            break
        time.sleep(2)
    else:
        raise SystemExit("MySQL temporário não ficou saudável")
    p = subprocess.Popen(["docker", "exec", "-i", _mysql_container(), "sh", "-c",
                          'MYSQL_PWD="$(cat /run/secrets/mysql_root_password)" mysql -uroot'],
                         stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    with gzip.open(dump, "rb") as fh:
        shutil.copyfileobj(fh, p.stdin)
    p.stdin.close()
    err = p.stderr.read().decode(errors="replace")
    if p.wait():
        raise SystemExit(f"restauração falhou: {err[:300]}")
    print("restaurado na cópia temporária:", dump.parent.name)


def subir() -> None:
    compose("up", "-d", "--no-build")
    for _ in range(120):
        r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"{BASE}/fiscal/"],
                           capture_output=True, text=True)
        if r.stdout == "200":
            print("temporário no ar:", BASE)
            return
        time.sleep(2)
    raise SystemExit("temporário não respondeu em 240 s")


def _exec(service: str, code: str, env: dict | None = None) -> str:
    args = ["exec", "-T"]
    for k, v in (env or {}).items():
        args += ["-e", f"{k}={v}"]
    r = compose(*args, service, "python", "-c", code, check=False, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"{service} falhou: {r.stderr.strip()[-400:]}")
    return r.stdout.strip()


def ajustar(company_id: str) -> None:
    senha = (SEC / "senha-validacao.txt").read_text(encoding="utf-8").strip()
    print(_exec("core-api", (
        "import os\n"
        "from datetime import datetime, timezone\n"
        "from sqlalchemy import select\nfrom sqlalchemy.orm import Session\n"
        "from core_api.settings import CoreSettings\nfrom core_api.models import Company, User, Membership\n"
        "from core_api.security import hash_password\nfrom ctrl_common.db import make_engine\n"
        "now = datetime.now(timezone.utc)\n"
        "with Session(make_engine(CoreSettings.from_env().database_url)) as s:\n"
        f"    c = s.get(Company, '{company_id}')\n"
        "    c.nfse_environment = 'restricted'\n"
        "    envs = set(c.adn_environments or []) | {'restricted'}\n"
        "    c.adn_environments = sorted(envs)\n"
        f"    u = s.scalar(select(User).where(User.email == '{USUARIO}'))\n"
        "    if u is None:\n"
        f"        u = User(email='{USUARIO}', name='Validação Produção Restrita (temporário)', is_active=True,\n"
        "                 password_hash=hash_password(os.environ['SENHA_PR']), password_set_at=now, created_at=now,\n"
        "                 updated_at=now)\n"
        "        s.add(u); s.flush()\n"
        f"        s.add(Membership(user_id=u.id, company_id=c.id, permissions={PERMS!r}, created_at=now, updated_at=now))\n"
        "    s.commit()\n"
        "    print('cópia: empresa em', c.nfse_environment, '| usuário de validação pronto')\n"
    ), env={"SENHA_PR": senha}))
    # Certificado: abre o material da cópia (cifrado para "production") e o instala para production+restricted pela
    # rotina da plataforma (mesmas validações). Só a CÓPIA é alterada; nada é impresso além do resultado.
    print(_exec("nfse-api", (
        "from datetime import datetime, timezone\n"
        "from sqlalchemy import select\n"
        "from ctrl_common.db import make_engine, make_sessionmaker\n"
        "from nfse_api import cert_store\nfrom nfse_api.models import Certificate\n"
        "from nfse_api.settings import NfseSettings\nfrom nfse_api.vault import Vault\n"
        "st = NfseSettings.from_env(); sessions = make_sessionmaker(make_engine(st.database_url)); v = Vault(st.vault_key_b64)\n"
        "with sessions() as s:\n"
        f"    row = s.scalar(select(Certificate).where(Certificate.company_id == '{company_id}', "
        "Certificate.environment == 'production', Certificate.is_active.is_(True)))\n"
        f"    aad = '{company_id}:production'.encode()\n"
        "    pfx, pw, cnpj = v.decrypt(row.encrypted_pfx, aad), v.decrypt(row.encrypted_password, aad), row.subject_cnpj\n"
        f"out = cert_store.install(sessions, v, company_id='{company_id}', cnpj=cnpj, environments=['production', 'restricted'],\n"
        "                         pfx=pfx, password=pw, now=datetime.now(timezone.utc), validator=st.icp_validator())\n"
        "del pfx, pw\n"
        "print('certificado na cópia:', out['environments'], 'aceito', out['accepted'], 'validade até', out['not_after'].date(),\n"
        "      'problemas', [p['code'] for p in out['problems']])\n"
    )))


def destruir() -> None:
    compose("down", "-v", "--remove-orphans", check=False)
    shutil.rmtree(SEC, ignore_errors=True)
    ENV.unlink(missing_ok=True)
    print("temporário removido: contêineres, volumes, secrets-pr/ e .env.pr")


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:] or [""]
    fn = {"preparar": preparar, "restaurar": restaurar, "subir": subir, "ajustar": ajustar, "destruir": destruir}.get(cmd)
    if fn is None:
        sys.exit(__doc__)
    fn(*rest)
