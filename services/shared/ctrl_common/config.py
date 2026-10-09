"""Leitura de configuração: segredos preferencialmente por arquivo (VAR_FILE).

Valores secretos nunca são registrados em log nem devolvidos por API.
"""
from __future__ import annotations

import os
from pathlib import Path


class ConfigError(RuntimeError):
    pass


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def env_bool(name: str, default: bool = False) -> bool:
    value = env(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int) -> int:
    value = env(name)
    return int(value) if value is not None else default


def env_list(name: str, default: str = "") -> list[str]:
    value = env(name, default) or ""
    return [v.strip() for v in value.split(",") if v.strip()]


def secret(name: str, required: bool = True, min_length: int = 0) -> str | None:
    """Lê VAR_FILE (preferido, ex.: /run/secrets/x) ou VAR."""
    path = env(name + "_FILE")
    value: str | None
    if path:
        p = Path(path)
        if not p.is_file():
            raise ConfigError(f"Arquivo de segredo ausente para {name}")
        value = p.read_text(encoding="utf-8").strip()
    else:
        value = env(name)
    if value is None or value == "":
        if required:
            raise ConfigError(f"Segredo obrigatório não configurado: {name}")
        return None
    if len(value) < min_length:
        raise ConfigError(f"Segredo {name} abaixo do tamanho mínimo")
    return value


def database_url(prefix: str) -> str:
    """Monta URL MySQL a partir de partes; senha vem de arquivo de segredo.

    Aceita {prefix}_DATABASE_URL completa (usada em testes/local).
    """
    direct = secret(prefix + "_DATABASE_URL", required=False)
    if direct:
        return direct
    from urllib.parse import quote

    host = env(prefix + "_DB_HOST", "mysql")
    port = env(prefix + "_DB_PORT", "3306")
    name = env(prefix + "_DB_NAME")
    user = env(prefix + "_DB_USER")
    password = secret(prefix + "_DB_PASSWORD")
    if not name or not user:
        raise ConfigError(f"{prefix}_DB_NAME/{prefix}_DB_USER não configurados")
    return f"mysql+pymysql://{quote(user)}:{quote(password or '')}@{host}:{port}/{name}?charset=utf8mb4"
