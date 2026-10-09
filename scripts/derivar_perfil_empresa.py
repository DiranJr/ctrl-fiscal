"""Deriva o perfil fiscal do prestador a partir dos lotes ADN de PRODUÇÃO já baixados (somente leitura).

Uso (no host, a partir da raiz do repositório):
  .venv\\Scripts\\python -I scripts\\derivar_perfil_empresa.py [--lotes data\\oficial\\producao\\adn]
                                                         [--saida data\\homologacao\\empresa.json]

- Lê apenas NFS-e em que o CNPJ do certificado é o EMITENTE (o certificado é o único CNPJ presente em
  todos os documentos distribuídos ao contribuinte).
- Usa a NFS-e emitida mais recente para nome, IM, município, fone e e-mail; regTrib vem da DPS dessa nota e
  é conferido contra as demais (divergências são contadas, não resolvidas por palpite).
- Grava o resultado em arquivo local fora do git (data/ está no .gitignore). NÃO imprime CNPJ, nome, IM,
  fone ou e-mail — só contagens, códigos de regime e o caminho do arquivo.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

from defusedxml import ElementTree as ET

NS = {"n": "http://www.sped.fazenda.gov.br/nfse"}


def _text(el, path: str) -> str | None:
    found = el.find(path, NS)
    return found.text.strip() if found is not None and found.text else None


def _cnpjs(root) -> set[str]:
    return {el.text.strip() for el in root.iter(f"{{{NS['n']}}}CNPJ") if el.text}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lotes", default="data/oficial/producao/adn")
    ap.add_argument("--saida", default="data/homologacao/empresa.json")
    a = ap.parse_args(argv)

    docs = []
    for f in sorted(Path(a.lotes).glob("lote-*.json")):
        for item in json.loads(f.read_text(encoding="utf-8")).get("LoteDFe") or []:
            if item.get("TipoDocumento") != "NFSE" or not item.get("ArquivoXml"):
                continue
            root = ET.fromstring(gzip.decompress(base64.b64decode(item["ArquivoXml"])))
            docs.append((int(item["NSU"]), root))
    if not docs:
        sys.stderr.write("Nenhuma NFS-e nos lotes informados.\n")
        return 2

    common = set.intersection(*(_cnpjs(r) for _, r in docs))
    if len(common) != 1:
        sys.stderr.write(f"CNPJ do certificado não identificável ({len(common)} candidatos comuns).\n")
        return 2
    cnpj = common.pop()

    emitted = [(nsu, r) for nsu, r in docs if _text(r, "n:infNFSe/n:emit/n:CNPJ") == cnpj]
    if not emitted:
        sys.stderr.write("Nenhuma NFS-e emitida pela empresa nos lotes (só como tomador).\n")
        return 3
    regimes = Counter(
        (_text(r, "n:infNFSe/n:DPS/n:infDPS/n:prest/n:regTrib/n:opSimpNac"),
         _text(r, "n:infNFSe/n:DPS/n:infDPS/n:prest/n:regTrib/n:regApTribSN"),
         _text(r, "n:infNFSe/n:DPS/n:infDPS/n:prest/n:regTrib/n:regEspTrib"))
        for _, r in emitted
    )
    _, last = max(emitted, key=lambda x: x[0])
    emit = last.find("n:infNFSe/n:emit", NS)
    op, ap_sn, esp = _text(last, "n:infNFSe/n:DPS/n:infDPS/n:prest/n:regTrib/n:opSimpNac"), \
        _text(last, "n:infNFSe/n:DPS/n:infDPS/n:prest/n:regTrib/n:regApTribSN"), \
        _text(last, "n:infNFSe/n:DPS/n:infDPS/n:prest/n:regTrib/n:regEspTrib")
    perfil = {
        "cnpj": cnpj,
        "name": _text(emit, "n:xNome"),
        "municipal_registration": _text(emit, "n:IM"),
        "municipality_code": _text(emit, "n:enderNac/n:cMun") or _text(last, "n:infNFSe/n:cLocIncid"),
        "phone": _text(emit, "n:fone"),
        "email": _text(emit, "n:email"),
        "simples_option": op,
        "simples_regime": ap_sn,
        "special_regime": esp,
        "timezone": "America/Sao_Paulo",
        "fiscal_profile_source": f"ADN produção (somente leitura): NFS-e emitida NSU {max(n for n, _ in emitted)}; "
                                 f"{len(emitted)} notas emitidas conferidas",
    }
    out = Path(a.saida)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(perfil, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.stdout.write(
        f"NFS-e lidas={len(docs)} emitidas_pela_empresa={len(emitted)}\n"
        f"regTrib (opSimpNac,regApTribSN,regEspTrib) -> ocorrências: {dict(regimes)}\n"
        f"campos preenchidos: {sorted(k for k, v in perfil.items() if v)}\n"
        f"campos ausentes: {sorted(k for k, v in perfil.items() if not v)}\n"
        f"perfil gravado em {out} (conteúdo não exibido)\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
