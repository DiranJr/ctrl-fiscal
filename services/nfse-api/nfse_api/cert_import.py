"""Instala certificado A1 no cofre do conector (execução administrativa), com a MESMA validação do endpoint
(nfse_api.cert_store.install: cadeia ICP-Brasil, revogação, uso, validade e CNPJ). Uso, dentro do container:
  python -m nfse_api.cert_import --company-id <uuid> --environment restricted \\
      --cnpj <14> --pfx /run/secrets/empresa.pfx --password-file /run/secrets/empresa.pfx.pass
A senha nunca é aceita por argumento de linha de comando nem registrada em log.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from ctrl_common.db import make_engine, make_sessionmaker

from . import cert_store
from .settings import NfseSettings
from .vault import Vault


def install(settings: NfseSettings, company_id: str, environment: str, cnpj: str, pfx: bytes, password: bytes) -> dict:
    sessions = make_sessionmaker(make_engine(settings.database_url))
    try:
        out = cert_store.install(sessions, Vault(settings.vault_key_b64), company_id=company_id, cnpj=cnpj,
                                 environments=[environment], pfx=pfx, password=password,
                                 now=datetime.now(timezone.utc), validator=settings.icp_validator())
    except cert_store.CertificateRejected as exc:
        return {"accepted_locally": False, "code": exc.code, "problems": [p["code"] for p in exc.problems] or [exc.code]}
    return {"accepted_locally": True, "problems": [p["code"] for p in out["problems"]], "not_after": out["not_after"].isoformat()}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company-id", required=True)
    ap.add_argument("--environment", required=True, choices=["restricted", "production"])
    ap.add_argument("--cnpj", required=True)
    ap.add_argument("--pfx", required=True)
    ap.add_argument("--password-file", required=True)
    a = ap.parse_args(argv)
    result = install(NfseSettings.from_env(), a.company_id, a.environment, a.cnpj.upper(),
                     Path(a.pfx).read_bytes(), Path(a.password_file).read_bytes().strip())
    sys.stdout.write(f"{result}\n")
    return 0 if result["accepted_locally"] else 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
