"""Impressão digital reprodutível do código (o repositório não está sob git).

SHA-256 sobre (caminho relativo + SHA-256 do conteúdo) de cada arquivo, em ordem.
Uso: python scripts/fingerprint.py
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"node_modules", "dist", ".next", "__pycache__", ".pytest_cache", ".venv", "secrets"}
GROUPS = {
    "backend": ["services", "infra", "compose.yaml", "contracts", "scripts", "tests", ".env.example"],
    "frontend": ["apps", "packages", "package.json", "package-lock.json"],
}


def files(entry: str) -> list[Path]:
    p = ROOT / entry
    if p.is_file():
        return [p]
    out = []
    for f in p.rglob("*"):
        if f.is_file() and not (set(f.relative_to(ROOT).parts) & SKIP_DIRS) and not f.name.endswith(".tsbuildinfo"):
            out.append(f)
    return out


def digest(entries: list[str]) -> tuple[str, int]:
    h = hashlib.sha256()
    items = sorted({f for e in entries for f in files(e)}, key=lambda f: f.relative_to(ROOT).as_posix())
    for f in items:
        h.update(f.relative_to(ROOT).as_posix().encode() + b"\0" + hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest(), len(items)


if __name__ == "__main__":
    for name, entries in GROUPS.items():
        d, n = digest(entries)
        print(f"{name}: sha256={d} arquivos={n}")
