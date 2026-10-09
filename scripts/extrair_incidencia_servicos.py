"""Extrai do Anexo I oficial (aba MUN.INCID_INFO.SERV.) a localidade de incidência do ISSQN por código de tributação
nacional e grava services/nfse-api/nfse_api/data/incidencia_servicos.json (com SHA-256 da planilha de origem).

Uso: .venv\\Scripts\\python -I scripts\\extrair_incidencia_servicos.py

Colunas da aba: EP = estabelecimento/domicílio do prestador (regra geral), LP = local da prestação,
ET = estabelecimento/domicílio do tomador. Linhas sem "X" em LP/ET seguem EP.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs/oficial/xsd/producao-restrita/anexo_i-sefin_adn-dps_nfse-snnfse-prodrest-v1-01-20260209.xlsx"
OUT = ROOT / "services/nfse-api/nfse_api/data/incidencia_servicos.json"


def main() -> int:
    wb = openpyxl.load_workbook(SRC, read_only=True, data_only=True)
    table: dict[str, str] = {}
    for row in wb["MUN.INCID_INFO.SERV."].iter_rows(values_only=True):
        code = str(row[0]).strip() if row and row[0] is not None else ""
        if not code.isdigit() or len(code) > 6:
            continue
        ep, lp, et = (str(c).strip().upper() if c is not None else "" for c in row[2:5])
        table[code.zfill(6)] = "LP" if lp == "X" else "ET" if et == "X" else "EP"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "fonte": SRC.name, "fonte_sha256": hashlib.sha256(SRC.read_bytes()).hexdigest(),
        "aba": "MUN.INCID_INFO.SERV.", "legenda": {"EP": "estabelecimento do prestador", "LP": "local da prestação",
                                                   "ET": "estabelecimento do tomador"},
        "incidencia": dict(sorted(table.items())),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    counts: dict[str, int] = {}
    for v in table.values():
        counts[v] = counts.get(v, 0) + 1
    print(f"{len(table)} códigos; {counts}; gravado em {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
