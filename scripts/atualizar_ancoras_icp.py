"""Atualiza as âncoras ICP-Brasil do conector a partir do pacote OFICIAL do ITI.

Uso: .venv\\Scripts\\python -I scripts\\atualizar_ancoras_icp.py --zip <ACcompactado.zip> --hash <hashsha512.txt>

Fonte: https://acraiz.icpbrasil.gov.br/credenciadas/CertificadosAC-ICP-Brasil/ACcompactado.zip (+ hashsha512.txt).
- Confere o SHA-512 publicado antes de ler o conteúdo; lê os membros do zip em memória (sem extrair em disco).
- Raízes: certificados autoassinados, BasicConstraints CA=true, com "Raiz Brasileira" no CN, ainda válidos.
- Intermediárias: demais certificados de AC válidos. Grava em services/nfse-api/nfse_api/icp_ancoras/ (raizes.pem,
  intermediarias.pem, manifesto.json com SHA-256 de cada certificado e do pacote).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "services/nfse-api/nfse_api/icp_ancoras"
MAX_MEMBER = 1024 * 1024


def load_any(data: bytes) -> list[x509.Certificate]:
    try:
        if b"-----BEGIN CERTIFICATE-----" in data:
            return x509.load_pem_x509_certificates(data)
        return [x509.load_der_x509_certificate(data)]
    except ValueError:
        return []


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True)
    ap.add_argument("--hash", required=True)
    a = ap.parse_args(argv)
    raw = Path(a.zip).read_bytes()
    published = Path(a.hash).read_text(encoding="utf-8", errors="ignore").split()[0].lower()
    actual = hashlib.sha512(raw).hexdigest()
    if actual != published:
        raise SystemExit("SHA-512 do pacote não confere com o publicado; abortado")
    now = datetime.now(timezone.utc)
    roots: dict = {}
    inter: dict = {}
    skipped = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for info in z.infolist():
            if info.is_dir() or info.file_size > MAX_MEMBER:
                continue
            for cert in load_any(z.read(info)):
                try:
                    is_ca = cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
                except x509.ExtensionNotFound:
                    is_ca = False
                if not is_ca or cert.not_valid_after_utc <= now:
                    skipped += 1
                    continue
                fp = hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest()
                cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
                cn = cn[0].value if cn else cert.subject.rfc4514_string()
                if cert.issuer == cert.subject and "Raiz Brasileira" in cn:
                    roots[fp] = (cn, cert)
                elif cert.issuer != cert.subject:
                    inter[fp] = (cn, cert)
    if not roots:
        raise SystemExit("Nenhuma raiz ICP-Brasil válida encontrada")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "__init__.py").touch()
    (OUT / "raizes.pem").write_bytes(b"".join(c.public_bytes(Encoding.PEM) for _, c in roots.values()))
    (OUT / "intermediarias.pem").write_bytes(b"".join(c.public_bytes(Encoding.PEM) for _, c in inter.values()))
    (OUT / "manifesto.json").write_text(json.dumps({
        "fonte": "https://acraiz.icpbrasil.gov.br/credenciadas/CertificadosAC-ICP-Brasil/ACcompactado.zip",
        "pacote_sha512": actual, "gerado_em": now.isoformat(),
        "raizes": [{"cn": cn, "sha256": fp, "valido_ate": c.not_valid_after_utc.isoformat()} for fp, (cn, c) in sorted(roots.items())],
        "intermediarias": len(inter), "ignorados_vencidos_ou_nao_ac": skipped,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"raízes válidas: {sorted(cn for cn, _ in roots.values())}")
    print(f"ACs intermediárias válidas: {len(inter)}; ignorados (vencidos/não-AC): {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
