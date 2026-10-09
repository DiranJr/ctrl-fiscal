"""Ambiente DESCARTÁVEL completo para testes autenticados (MySQL, core, fiscal, worker, nfse, gateway, portal, painel).

Isolado do operacional: projeto compose próprio (ctrlfiscal-desc), volumes e banco próprios, porta 8098, SEGREDOS
PRÓPRIOS gerados aqui (secrets-descartavel/, nunca exibidos), integrações oficiais DESLIGADAS e governo simulado
(mock) ligado. Usa as mesmas imagens que serão implantadas (IMAGE_TAG, padrão "rc").

Uso (.venv\\Scripts\\python -I scripts\\ambiente_descartavel.py <comando>):
  preparar   gera secrets-descartavel/ e .env.descartavel (não sobrescreve segredos existentes)
  subir      sobe a pilha com as imagens IMAGE_TAG (sem build)
  semear     empresas/usuários FICTÍCIOS: seed (2 empresas + admin) → SUPER_ADMIN do admin fictício → usuários de
             teste criados pela API de administração e ativados pelo link de uso único
  destruir   derruba a pilha e APAGA os volumes do projeto descartável (nunca toca no operacional)
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEC = ROOT / "secrets-descartavel"
ENV = ROOT / ".env.descartavel"
PROJECT = "ctrlfiscal-desc"
PORT = 8098
BASE = f"http://127.0.0.1:{PORT}"
ADMIN_EMAIL = "admin@descartavel.example.com"
OPERADOR = ["view", "import", "sync", "review", "prepare", "schedule", "emit", "cancel"]
USERS = {  # e-mail → (nome, vínculos {empresa: permissões})
    "operador.a@descartavel.example.com": ("Operador A", {"A": OPERADOR}),
    "leitor.a@descartavel.example.com": ("Leitor A", {"A": ["view"]}),
    "operador.b@descartavel.example.com": ("Operador B", {"B": OPERADOR}),
    # Admin de empresa SEM cancel/certificates: usado nos testes de teto de concessão.
    "admin.a@descartavel.example.com": ("Admin A", {"A": ["view", "import", "sync", "review", "prepare", "admin"]}),
    # MESMO usuário em dois workspaces com permissões diferentes (admin em A, só leitura em B).
    "multi.ab@descartavel.example.com": ("Multi AB", {"A": OPERADOR + ["admin"], "B": ["view"]}),
}


def compose(*args: str, check: bool = True, **kw) -> subprocess.CompletedProcess:
    env = {**os.environ, "SECRETS_DIR": "./secrets-descartavel", "IMAGE_TAG": os.environ.get("IMAGE_TAG", "rc")}
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
    put("nfse_vault_key.txt", base64.b64encode(os.urandom(32)).decode())
    if not (SEC / "jwt_private_key.pem").exists():
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        k = Ed25519PrivateKey.generate()
        put("jwt_private_key.pem", k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()).decode())
        put("jwt_public_key.pem", k.public_key().public_bytes(serialization.Encoding.PEM,
                                                              serialization.PublicFormat.SubjectPublicKeyInfo).decode())
    put("admin_password.txt", secrets.token_urlsafe(24))
    for email in USERS:
        put(f"senha-{email.split('@')[0]}.txt", "Fx-" + secrets.token_urlsafe(18))
    lines = [ln for ln in (ROOT / ".env").read_text(encoding="utf-8").splitlines()]
    over = {"HTTP_PORT": str(PORT), "ALLOWED_ORIGINS": f"{BASE},http://localhost:{PORT}", "APP_ENV": "development",
            "NFSE_RESTRICTED_ENABLED": "false", "NFSE_PRODUCTION_DISTRIBUTION_ENABLED": "false",
            "NFSE_MOCK_ENABLED": "true", "IMAGE_TAG": os.environ.get("IMAGE_TAG", "rc"), "DANFSE_FONT_DIR": ""}
    out, seen = [], set()
    for ln in lines:
        k = ln.split("=", 1)[0].strip()
        if k in over:
            out.append(f"{k}={over[k]}")
            seen.add(k)
        else:
            out.append(ln)
    out += [f"{k}={v}" for k, v in over.items() if k not in seen]
    out.insert(0, "# GERADO por scripts/ambiente_descartavel.py — ambiente DESCARTÁVEL (sem integrações oficiais)")
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("preparado: secrets-descartavel/ e .env.descartavel (valores não exibidos)")


def subir() -> None:
    compose("up", "-d", "--no-build")
    for _ in range(90):
        r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"{BASE}/fiscal/"], capture_output=True, text=True)
        if r.stdout == "200":
            print("descartável no ar:", BASE)
            return
        time.sleep(2)
    raise SystemExit("descartável não respondeu em 180 s")


def _exec(service: str, *cmd: str, env: dict | None = None, files: dict | None = None) -> str:
    args = ["exec", "-T"]
    for k, v in (env or {}).items():
        args += ["-e", f"{k}={v}"]
    r = compose(*args, service, *cmd, check=False, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"{service} {cmd[:3]} falhou: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def semear() -> None:
    import httpx

    admin_pw = (SEC / "admin_password.txt").read_text(encoding="utf-8").strip()
    # 1) seed fictício (empresas A/B + admin) — exige confirmação explícita; senha passada pelo ambiente do exec
    _exec("core-api", "python", "-m", "core_api.seed",
          env={"ALLOW_DEMO_SEED": "true", "SEED_ADMIN_EMAIL": ADMIN_EMAIL, "SEED_ADMIN_PASSWORD": admin_pw})
    print("seed:", _exec("core-api", "python", "-m", "core_api.super_admin", "--email", ADMIN_EMAIL))
    # Auditoria imutável também no descartável (mesmo endurecimento do operacional).
    subprocess.run([sys.executable, "-I", str(ROOT / "scripts/endurecer_mysql.py"), f"{PROJECT}-mysql-1"], check=True)
    c = httpx.Client(base_url=BASE, timeout=60, headers={"Origin": BASE})
    r = c.post("/api/core/v1/auth/login", json={"email": ADMIN_EMAIL, "password": admin_pw})
    r.raise_for_status()
    h = {"Authorization": "Bearer " + r.json()["access_token"]}
    comps = {x["name"]: x["id"] for x in c.get("/api/core/v1/admin/companies", headers=h).json()}
    ids = {"A": next(v for k, v in comps.items() if "A (LOCAL)" in k), "B": next(v for k, v in comps.items() if "B (LOCAL)" in k)}
    existing = {u["email"] for u in c.get("/api/core/v1/admin/users", headers=h, params={"page_size": 100}).json()["items"]}
    for email, (name, links) in USERS.items():
        if email in existing:
            continue
        body = {"name": name, "email": email,
                "memberships": [{"company_id": ids[k], "permissions": perms} for k, perms in links.items()]}
        r = c.post("/api/core/v1/admin/users", headers=h, json=body)
        r.raise_for_status()
        token = r.json()["credential_link"]["path"].split("#token=", 1)[1]
        pw = (SEC / f"senha-{email.split('@')[0]}.txt").read_text(encoding="utf-8").strip()
        a = c.post("/api/core/v1/auth/activate", json={"token": token, "password": pw})
        if a.status_code != 204:
            raise SystemExit(f"ativação falhou para {email}: {a.status_code}")
    (SEC / "fixtures.json").write_text(json.dumps({"base": BASE, "companies": ids, "admin": ADMIN_EMAIL,
                                                   "users": {e: {"name": n, "links": l} for e, (n, l) in USERS.items()}},
                                                  indent=1), encoding="utf-8")
    print("semeado: empresas", list(ids), "usuários", len(USERS) + 1, "(fixtures em secrets-descartavel/fixtures.json)")


def destruir() -> None:
    compose("down", "-v", "--remove-orphans", check=False)
    print("ambiente descartável removido (volumes do projeto", PROJECT, "apagados)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"preparar": preparar, "subir": subir, "semear": semear, "destruir": destruir}.get(
        cmd, lambda: sys.exit(__doc__))()
