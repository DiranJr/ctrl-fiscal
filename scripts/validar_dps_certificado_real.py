"""Validação LOCAL (sem transmissão) da DPS da nota de teste assinada com o e-CNPJ REAL do repositório do Windows.

Uso: .venv\\Scripts\\python -I scripts\\validar_dps_certificado_real.py --thumbprint <inicio-do-thumbprint> [--numero 1]

1. Monta a DPS com o mesmo código da plataforma (nfse_api.dps.montar), a partir de data/homologacao/empresa.json
   e data/homologacao/nota-teste.json, série 1, ambiente Produção Restrita (tpAmb=2).
2. Assina com o MESMO perfil da plataforma (nfse_api.signer.sign_enveloped_with); só a operação RSA-SHA1 é
   feita pela chave não exportável, via .NET/PowerShell (GetRSAPrivateKey().SignData). Nada sai da máquina.
3. Verifica: assinatura (verify_enveloped), XSD PRODREST v1.01-20260727, CNPJ do certificado = CNPJ do
   prestador, Id DPS com 45 caracteres.
Grava a DPS assinada em data/homologacao/ (fora do git). Imprime só resultados, sem dados fiscais.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("services/shared", "services/nfse-api"):
    sys.path.insert(0, str(ROOT / sub))

from cryptography import x509  # noqa: E402

from nfse_api import dps  # noqa: E402
from nfse_api.certs import inspect_certificate  # noqa: E402
from nfse_api.signer import sign_enveloped_with, verify_enveloped  # noqa: E402

PS_SIGN = r"""
$ErrorActionPreference = 'Stop'
$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Thumbprint -like "$env:CTRL_THUMB*" } | Select-Object -First 1
if (-not $cert) { throw 'certificado não encontrado' }
if ($env:CTRL_MODE -eq 'cert') { [Convert]::ToBase64String($cert.RawData); exit 0 }
$data = [Convert]::FromBase64String([Console]::In.ReadToEnd().Trim())
$rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($cert)
[Convert]::ToBase64String($rsa.SignData($data, [System.Security.Cryptography.HashAlgorithmName]::SHA1,
  [System.Security.Cryptography.RSASignaturePadding]::Pkcs1))
"""


def ps(mode: str, thumb: str, stdin: bytes = b"") -> bytes:
    env = {**__import__("os").environ, "CTRL_THUMB": thumb, "CTRL_MODE": mode}
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_SIGN], input=stdin,
                         capture_output=True, env=env, timeout=120)
    if out.returncode != 0:
        raise SystemExit("falha no PowerShell: " + out.stderr.decode("utf-8", "ignore")[:300])
    return base64.b64decode(out.stdout.strip())


def snapshot(perfil: dict, nota: dict) -> dict:
    def clean(o):  # noqa: ANN001, ANN202
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items() if not k.startswith("_")}
        return o
    n = clean(nota["nota"])
    return {
        "company": {"cnpj": perfil["cnpj"], "municipality_code": perfil["municipality_code"],
                    "timezone": perfil.get("timezone") or "America/Sao_Paulo",
                    "fiscal_profile": {k: perfil.get(k) for k in ("municipal_registration", "simples_option",
                                                                    "simples_regime", "special_regime", "phone", "email")}},
        "customer": clean(nota["tomador"]),
        "competence": n["competence"], "service_date": n["service_date"], "service": n["service"],
        "amount": n["amount"], "deductions": n.get("deductions") or "0.00", "tax_settings": n["tax_settings"],
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--thumbprint", required=True)
    ap.add_argument("--numero", type=int, default=1)
    a = ap.parse_args(argv)
    perfil = json.loads((ROOT / "data/homologacao/empresa.json").read_text(encoding="utf-8"))
    nota = json.loads((ROOT / "data/homologacao/nota-teste.json").read_text(encoding="utf-8"))
    try:
        m = dps.montar(snapshot(perfil, nota), environment="restricted", serie="1", numero=a.numero,
                       agora=datetime.now(timezone.utc))
    except dps.DpsIncompleta as exc:
        print("DPS incompleta:", [f"{f['campo']}:{f['codigo']}" for f in exc.faltas])
        return 2
    print(f"1. DPS montada: Id com {len(m.dps_id)} caracteres, XSD (sem assinatura) OK, série 1, nDPS {a.numero}, tpAmb 2")
    cert_der = ps("cert", a.thumbprint)
    rep = inspect_certificate(x509.load_der_x509_certificate(cert_der))
    ok_cert, problems = rep.ok_for(perfil["cnpj"], datetime.now(timezone.utc))
    print(f"2. Certificado do repositório: CNPJ confere={rep.subject_cnpj == perfil['cnpj']} "
          f"válido até {rep.not_after.date()} uso_cliente={rep.checks.get('client_auth')} "
          f"apto={ok_cert} avisos={[p['code'] for p in problems]}")
    signed = sign_enveloped_with(m.xml, cert_der, lambda data: ps("sign", a.thumbprint, base64.b64encode(data)))
    print(f"3. Assinatura RSA-SHA1 pela chave não exportável: verify_enveloped={verify_enveloped(signed)}")
    dps.validar_xsd(signed, "restricted")
    print("4. DPS assinada válida no XSD PRODREST v1.01-20260727")
    tampered = signed.replace(b"<nDPS>", b"<nDPS>9", 1)
    print(f"5. Adulteração detectada: {not verify_enveloped(tampered)}")
    out = ROOT / "data/homologacao/dps-teste-assinada-local.xml"
    out.write_bytes(signed)
    print(f"DPS assinada gravada em {out.relative_to(ROOT)} (NÃO transmitida)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
