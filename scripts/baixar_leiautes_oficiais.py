"""Baixa (HTTPS público, sem certificado) os pacotes XSD e anexos de leiaute publicados no portal NFS-e
para Produção e Produção Restrita, registra SHA-256 e extrai os XSD como DADOS (nada é executado).

Uso: python scripts/baixar_leiautes_oficiais.py
Saída: docs/oficial/xsd/<ambiente>/..., docs/oficial/xsd/manifesto-leiautes.json
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "oficial" / "xsd"
PAGES = {
    "producao": "https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual",
    "producao-restrita": "https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/producao-restrita",
}
WANTED = re.compile(r'href="(https://www\.gov\.br/nfse/[^"]+?(?:esquema[^"/]*\.zip|anexo_(?:i|ii|b|c)-[^"/]+\.xlsx))"', re.I)
MAX_BYTES = 30 * 1024 * 1024


def safe_extract(zpath: Path, dest: Path) -> list[str]:
    names = []
    with zipfile.ZipFile(zpath) as zf:
        for info in zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".xsd"):
                continue
            rel = Path(*[p for p in Path(info.filename.replace("\\", "/")).parts if p not in ("..", "/", "\\")])
            if info.file_size > 5 * 1024 * 1024:
                continue
            target = (dest / rel).resolve()
            if not str(target).startswith(str(dest.resolve())):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
            names.append(rel.as_posix())
    return sorted(names)


def main() -> None:
    client = httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": "CTRL-Fiscal-docs/0.2"})
    manifest = {"obtido_em": datetime.now(timezone.utc).isoformat(), "ambientes": {}}
    for env, page in PAGES.items():
        html = client.get(page).text
        (OUT / "paginas").mkdir(parents=True, exist_ok=True)
        (OUT / "paginas" / f"{env}.html").write_text(html, encoding="utf-8")
        label = re.findall(r"NFSe-ESQUEMAS_XSD-[A-Z]*-?v[\d.]+-\d{8}", html)
        items = []
        for url in sorted(set(WANTED.findall(html))):
            name = Path(urlsplit(url).path).name
            dest = OUT / env / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            r = client.get(url)
            if r.status_code != 200 or len(r.content) > MAX_BYTES:
                items.append({"url": url, "status": r.status_code, "erro": "não baixado"})
                continue
            dest.write_bytes(r.content)
            item = {"url": url, "arquivo": f"{env}/{name}", "status": 200, "bytes": len(r.content),
                    "sha256": hashlib.sha256(r.content).hexdigest()}
            if name.lower().endswith(".zip"):
                xsd_dir = OUT / env / Path(name).stem
                item["xsd_extraidos"] = safe_extract(dest, xsd_dir)
                item["xsd_sha256"] = {n: hashlib.sha256((xsd_dir / n).read_bytes()).hexdigest() for n in item["xsd_extraidos"]}
            items.append(item)
            print(f"{env:18s} {r.status_code} {len(r.content):>9d}B {item['sha256'][:16]}… {name}")
        manifest["ambientes"][env] = {"pagina": page, "rotulos_pacote_na_pagina": sorted(set(label)), "arquivos": items}
    (OUT / "manifesto-leiautes.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("rótulos:", {e: v["rotulos_pacote_na_pagina"] for e, v in manifest["ambientes"].items()})


if __name__ == "__main__":
    sys.exit(main())
