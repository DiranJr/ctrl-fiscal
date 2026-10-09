"""Gera os contratos canônicos contracts/openapi-core.json e contracts/openapi-fiscal.json.

Uso: python scripts/export_openapi.py [--out contracts/sprint-02 --version 0.2.0]   (não acessa banco nem rede)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("services/shared", "services/core-api", "services/fiscal-api"):
    sys.path.insert(0, str(ROOT / sub))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

CONTRACT_VERSION = "0.9.2"


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="contracts")
    ap.add_argument("--version", default=CONTRACT_VERSION)
    args = ap.parse_args()
    from core_api.app import create_app as core_app
    from core_api.settings import CoreSettings
    from ctrl_fiscal.api.app import create_app as fiscal_app
    from ctrl_fiscal.settings import FiscalSettings

    key = Ed25519PrivateKey.generate()
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    core = core_app(CoreSettings(database_url="sqlite://", jwt_private_key_pem=priv, jwt_public_key_pem=pub,
                                 csrf_secret="x" * 40, app_env="test", allowed_origins=["http://127.0.0.1:8088"],
                                 internal_clients={"fiscal-api": ("k" * 40, {"authz"})}))
    fiscal = fiscal_app(FiscalSettings(database_url="sqlite://", app_env="test", jwt_public_key_pem=pub,
                                       core_api_url="http://core-api:8000", core_api_key="k" * 40))
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    for name, app in (("core", core), ("fiscal", fiscal)):
        spec = app.openapi()
        spec["info"]["version"] = args.version
        spec["info"]["x-contract"] = f"CTRL Fiscal contrato compartilhado v{args.version.rsplit('.', 1)[0]}"
        spec["servers"] = [{"url": "/", "description": "Gateway (mesma origem), ex.: http://127.0.0.1:8088"}]
        spec.setdefault("components", {})["securitySchemes"] = {
            "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT (EdDSA), somente em memória"}
        }
        public = {"/api/core/v1/auth/login", "/api/core/v1/auth/csrf", "/api/core/v1/auth/refresh", "/api/core/v1/auth/logout",
                  "/api/core/v1/auth/activate"}
        for path, ops in spec["paths"].items():
            for op in ops.values():
                if path not in public:
                    op["security"] = [{"bearerAuth": []}]
        (out / f"openapi-{name}.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{args.out}/openapi-{name}.json: {len(spec['paths'])} paths")


if __name__ == "__main__":
    main()
