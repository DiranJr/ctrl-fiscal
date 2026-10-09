"""Gera par Ed25519 para JWT. Uso: python -m core_api.keygen private|public <arquivo_privado>

`private` imprime uma nova chave privada PEM; `public <arquivo>` imprime a pública
correspondente. Usado pelo script de geração de segredos locais.
"""
from __future__ import annotations

import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def new_private_pem() -> str:
    key = Ed25519PrivateKey.generate()
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


def public_pem_from_private(private_pem: str) -> str:
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()


def main(argv: list[str]) -> int:
    if len(argv) >= 1 and argv[0] == "private":
        sys.stdout.write(new_private_pem())
        return 0
    if len(argv) >= 2 and argv[0] == "public":
        sys.stdout.write(public_pem_from_private(Path(argv[1]).read_text()))
        return 0
    if len(argv) >= 1 and argv[0] == "public-stdin":
        sys.stdout.write(public_pem_from_private(sys.stdin.read()))
        return 0
    sys.stderr.write("uso: python -m core_api.keygen private | public <arquivo> | public-stdin\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
