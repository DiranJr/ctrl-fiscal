"""Cofre de segredos do conector: AES-256-GCM com chave fora do banco/repositório.

A chave (32 bytes, base64) é entregue somente ao container nfse-api como
segredo de arquivo. Não usa DPAPI (VPS Linux).
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class VaultUnavailable(Exception):
    pass


class Vault:
    VERSION = 1

    def __init__(self, key_b64: str | None) -> None:
        if not key_b64:
            self._aead = None
            return
        key = base64.b64decode(key_b64)
        if len(key) != 32:
            raise ValueError("NFSE_VAULT_KEY deve ter 32 bytes (base64)")
        self._aead = AESGCM(key)

    @property
    def available(self) -> bool:
        return self._aead is not None

    def encrypt(self, data: bytes, aad: bytes) -> bytes:
        if self._aead is None:
            raise VaultUnavailable("cofre sem chave")
        nonce = os.urandom(12)
        return bytes([self.VERSION]) + nonce + self._aead.encrypt(nonce, data, aad)

    def decrypt(self, blob: bytes, aad: bytes) -> bytes:
        if self._aead is None:
            raise VaultUnavailable("cofre sem chave")
        if not blob or blob[0] != self.VERSION:
            raise ValueError("versão de cifra desconhecida")
        return self._aead.decrypt(blob[1:13], blob[13:], aad)
