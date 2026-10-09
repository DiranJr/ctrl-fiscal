"""Manifesto de versão para a revisão de integração (o repositório não está sob git).

Registra SHA-256 por arquivo relevante (frontend e backend), hashes agregados,
hashes dos OpenAPI e, para cada container do projeto, a imagem em execução
comparada ao ID da tag recém-construída.

Uso: python scripts/version_manifest.py <saida.json> [--label texto]
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {"node_modules", "dist", ".next", "__pycache__", ".pytest_cache", ".venv", "secrets"}
GROUPS = {
    "frontend": ["apps", "packages", "package.json", "package-lock.json"],
    "backend": ["services", "infra", "compose.yaml", "contracts"],
    "verificacao": ["scripts", "tests"],
}
SERVICES = ["core-api", "fiscal-api", "fiscal-worker", "nfse-api", "web", "painel-fiscal"]


def _files(entry: str) -> list[Path]:
    p = ROOT / entry
    if p.is_file():
        return [p]
    return [f for f in p.rglob("*") if f.is_file() and not (set(f.relative_to(ROOT).parts) & SKIP)
            and not f.name.endswith(".tsbuildinfo")]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _docker(*args: str) -> str:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False).stdout.strip()


def _git() -> str:
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True)
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True)
    if sha.returncode:
        return "repositório sem git (sem commit)"
    return f"{head.stdout.strip()}@{sha.stdout.strip()[:12]}" + (" (com alterações não commitadas)" if dirty.stdout.strip() else "")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--") or a == "--label"]
    if not args or args[0] in ("-h", "--help") or "--help" in sys.argv or "-h" in sys.argv:
        sys.exit("uso: version_manifest.py <arquivo-de-saida.json> [--label rotulo]")
    out = Path(args[0])
    label = sys.argv[sys.argv.index("--label") + 1] if "--label" in sys.argv else ""
    manifest: dict = {"gerado_em": datetime.now(timezone.utc).isoformat(), "rotulo": label, "git": _git()}
    for group, entries in GROUPS.items():
        files = sorted({f for e in entries for f in _files(e)}, key=lambda f: f.relative_to(ROOT).as_posix())
        per_file = {f.relative_to(ROOT).as_posix(): _sha(f) for f in files}
        agg = hashlib.sha256()
        for path, digest in per_file.items():
            agg.update(path.encode() + b"\0" + bytes.fromhex(digest))
        manifest[group] = {"agregado_sha256": agg.hexdigest(), "arquivos": len(per_file), "por_arquivo": per_file}
    manifest["openapi"] = {p.name: _sha(p) for p in sorted((ROOT / "contracts").glob("openapi-*.json"))}
    images = {}
    for svc in SERVICES:
        tag = f"ctrlfiscal/{svc}:dev"
        tag_id = _docker("image", "inspect", "--format", "{{.Id}}", tag)
        created = _docker("image", "inspect", "--format", "{{.Created}}", tag)
        running = _docker("inspect", "--format", "{{.Image}}", f"ctrlfiscal-{svc}-1")
        started = _docker("inspect", "--format", "{{.State.StartedAt}}", f"ctrlfiscal-{svc}-1")
        images[svc] = {"tag": tag, "imagem_tag": tag_id, "imagem_criada": created, "imagem_em_execucao": running,
                       "container_iniciado": started, "container_usa_imagem_da_tag": bool(tag_id) and tag_id == running}
    manifest["imagens"] = images
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"frontend={manifest['frontend']['agregado_sha256']} backend={manifest['backend']['agregado_sha256']} "
          f"verificacao={manifest['verificacao']['agregado_sha256']}")
    for k, v in manifest["openapi"].items():
        print(f"{k}={v}")
    for svc, i in images.items():
        print(f"{svc}: {i['imagem_em_execucao'][7:19]} criada={i['imagem_criada'][:19]} iniciado={i['container_iniciado'][:19]} "
              f"tag==execucao={i['container_usa_imagem_da_tag']}")


if __name__ == "__main__":
    main()
