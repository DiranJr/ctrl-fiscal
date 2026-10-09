"""Gera o pacote de DOMÍNIOS OFICIAIS usado na validação (services/shared/ctrl_common/dominios/dados.json).

Uso: .venv\\Scripts\\python -I scripts\\gerar_dominios_oficiais.py

Fontes (planilhas oficiais baixadas do gov.br; SHA-256 registrado no pacote):
- Anexo A: TAB.MUN_IBGE (municípios, E0302/E0238/E0130) e TAB.LOC.GERAL (0000000 = Águas Marítimas).
- Anexo B: LISTA.SERV.NAC. (cTribNac com desdobro, E0310) e LISTA.NBS_v2.0 (cNBS de 9 dígitos, E0316).
- Anexo C: códigos indicadores de operação IBS/CBS (cIndOp, E0901).
- Anexo I: MUN.INCID_INFO.SERV. (localidade de incidência EP/LP/ET e grupo obrigatório obra/atvEvento).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
OFIC = ROOT / "docs/oficial/xsd"
OUT = ROOT / "services/shared/ctrl_common/dominios/dados.json"


def _wb(path: Path):  # noqa: ANN202
    return openpyxl.load_workbook(path, read_only=True, data_only=True)


def _s(v) -> str:  # noqa: ANN001
    return "" if v is None else str(v).replace("\n", " ").strip()


def main() -> int:
    a = next((OFIC / "producao").glob("anexo_a-*.xlsx"))
    b = next((OFIC / "producao-restrita").glob("anexo_b-*.xlsx"))
    c = next((OFIC / "producao-restrita").glob("anexo_c-*.xlsx"))
    i = next((OFIC / "producao-restrita").glob("anexo_i-*.xlsx"))
    wa = _wb(a)
    municipios = {}
    for r in wa["TAB.MUN_IBGE"].iter_rows(min_row=2, values_only=True):
        code = _s(r[3]) if len(r) > 3 else ""
        if re.fullmatch(r"\d{7}", code):
            municipios[code] = {"nome": _s(r[2]), "uf": _s(r[1])}
    paises = {_s(r[0]): _s(r[1]) for r in wa["TAB.PAÍS_ISO2"].iter_rows(min_row=2, values_only=True)
              if r and re.fullmatch(r"[A-Z]{2}", _s(r[0]))}
    wb_ = _wb(b)
    servicos = {}
    for r in wb_["LISTA.SERV.NAC."].iter_rows(min_row=2, values_only=True):
        cells = [x for x in r if x is not None]
        if len(cells) >= 5 and re.fullmatch(r"\d{5,6}", _s(cells[0])):
            servicos[_s(cells[0]).zfill(6)] = _s(cells[4])[:300]
    nbs = {}
    for r in wb_["LISTA.NBS_v2.0"].iter_rows(min_row=2, values_only=True):
        code = re.sub(r"\D", "", _s(r[0])) if r else ""
        if len(code) == 9:
            nbs[code] = _s(r[1])[:300]
    cindop = set()
    cindop_info: dict[str, dict] = {}
    ctx = {"artigo": "", "tipo": "", "local_operacao": "", "caracteristica": ""}
    for r in _wb(c).worksheets[0].iter_rows(values_only=True):
        cells = [" ".join(_s(x).split()) for x in (list(r) + [None] * 9)[:9]]
        # Linhas do Anexo C herdam (células mescladas) artigo, tipo, local e característica da linha anterior.
        for k, i_ in (("artigo", 0), ("tipo", 1), ("local_operacao", 2), ("caracteristica", 3)):
            if cells[i_]:
                ctx[k] = cells[i_]
        for x in r:
            if re.fullmatch(r"\d{6}", _s(x)):
                cindop.add(_s(x))
                cindop_info[_s(x)] = {**ctx, "local_dfe": cells[7], "campo_leiaute": cells[8][:200]}
    incid, grupos = {}, {}
    for r in _wb(i)["MUN.INCID_INFO.SERV."].iter_rows(values_only=True):
        code = _s(r[0]) if r else ""
        if not code.isdigit() or len(code) > 6:
            continue
        ep, lp, et = (_s(x).upper() for x in r[2:5])
        incid[code.zfill(6)] = "LP" if lp == "X" else "ET" if et == "X" else "EP"
        g = _s(r[6]) if len(r) > 6 else ""
        if g in ("obra", "atvEvento"):
            grupos[code.zfill(6)] = g
    fontes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (a, b, c, i)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "fontes_sha256": fontes, "municipios": municipios, "localidades_especiais": {"0000000": "Águas Marítimas"},
        "paises_iso2": paises, "servicos_nacionais": servicos, "nbs": nbs, "cindop": sorted(cindop),
        "cindop_info": cindop_info,
        "incidencia": incid, "grupo_obrigatorio": grupos,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"municípios={len(municipios)} países={len(paises)} cTribNac={len(servicos)} NBS={len(nbs)} "
          f"cIndOp={len(cindop)} incidência={len(incid)} grupos={dict((g, list(grupos.values()).count(g)) for g in set(grupos.values()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
