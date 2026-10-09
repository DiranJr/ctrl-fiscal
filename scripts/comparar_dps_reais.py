"""Compara, elemento a elemento, as DPS REAIS emitidas pela empresa (ADN produção, somente leitura, arquivos
locais fora do git) com a DPS que o CTRL Fiscal monta a partir dos mesmos dados.

Uso: .venv\\Scripts\\python -I scripts\\comparar_dps_reais.py [--lotes data\\oficial\\producao\\adn]
                                                          [--perfil data\\homologacao\\empresa.json]

Imprime apenas contagens por caminho XML (iguais / divergentes / ausentes no CTRL / extras no CTRL) — nunca
valores, nomes, documentos ou chaves. Elementos que dependem do momento da emissão (dhEmi, verAplic, Id,
nDPS, serie, tpAmb) são excluídos da comparação de valores. Monta com o XSD de Produção Restrita: o XSD de
produção v1.01-20260209 tem o padrão de `serie` com ^/$ literais e recusa séries válidas (ver API_OFICIAL §8).
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from defusedxml import ElementTree as SafeET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "nfse-api"))
sys.path.insert(0, str(ROOT / "services" / "shared"))

from nfse_api import dps  # noqa: E402

N = "{http://www.sped.fazenda.gov.br/nfse}"
SKIP_VALUES = {"tpAmb", "dhEmi", "verAplic", "serie", "nDPS"}
REV_ISS = {v: k for k, v in dps.TRIB_ISSQN.items()}
REV_RET = {v: k for k, v in dps.RET_ISSQN.items()}


def t(el, path: str) -> str | None:  # noqa: ANN001
    found = el.find("/".join(N + p for p in path.split("/"))) if el is not None else None
    return found.text.strip() if found is not None and found.text else None


def snapshot_from_real(inf, perfil: dict) -> dict:  # noqa: ANN001
    toma = inf.find(N + "toma")
    end = toma.find(N + "end") if toma is not None else None
    obra = inf.find(f"{N}serv/{N}obra")
    trib = inf.find(f"{N}valores/{N}trib")
    tot = trib.find(N + "totTrib")
    if tot.find(N + "vTotTrib") is not None:
        approx = {"mode": "VALUES", "federal": t(tot, "vTotTrib/vTotTribFed"), "state": t(tot, "vTotTrib/vTotTribEst"),
                  "municipal": t(tot, "vTotTrib/vTotTribMun")}
    elif tot.find(N + "pTotTrib") is not None:
        approx = {"mode": "PERCENT", "federal": t(tot, "pTotTrib/pTotTribFed"), "state": t(tot, "pTotTrib/pTotTribEst"),
                  "municipal": t(tot, "pTotTrib/pTotTribMun")}
    elif tot.find(N + "pTotTribSN") is not None:
        approx = {"mode": "SIMPLES_PERCENT", "simples": t(tot, "pTotTribSN")}
    else:
        approx = {"mode": "NOT_INFORMED"}
    g = inf.find(N + "IBSCBS")
    ibs = None
    if g is not None:
        ibs = {"operation_indicator": t(g, "cIndOp"), "cst": t(g, "valores/trib/gIBSCBS/CST"),
               "class_code": t(g, "valores/trib/gIBSCBS/cClassTrib"), "personal_use": t(g, "indFinal")}
    obra_address = None
    if obra is not None and obra.find(N + "end") is not None:
        obra_address = {"postal_code": t(obra, "end/CEP"), "street": t(obra, "end/xLgr"), "number": t(obra, "end/nro"),
                        "complement": t(obra, "end/xCpl"), "district": t(obra, "end/xBairro")}
    d_compet = t(inf, "dCompet")
    return {
        "company": {"cnpj": t(inf, "prest/CNPJ"), "municipality_code": t(inf, "cLocEmi"), "timezone": "America/Sao_Paulo",
                    "fiscal_profile": {"municipal_registration": perfil.get("municipal_registration"),
                                       "phone": perfil.get("phone"), "email": perfil.get("email"),
                                       "simples_option": perfil.get("simples_option"),
                                       "simples_regime": perfil.get("simples_regime"),
                                       "special_regime": perfil.get("special_regime")}},
        "customer": {"document": t(toma, "CNPJ") or t(toma, "CPF"), "name": t(toma, "xNome"),
                     "municipal_registration": t(toma, "IM"), "email": t(toma, "email"), "phone": t(toma, "fone"),
                     "municipality_code": t(end, "endNac/cMun"), "postal_code": t(end, "endNac/CEP"),
                     "address": {"street": t(end, "xLgr"), "number": t(end, "nro"), "complement": t(end, "xCpl"),
                                 "district": t(end, "xBairro")} if end is not None else None},
        "competence": d_compet[:7], "service_date": d_compet,
        "service": {"national_code": t(inf, "serv/cServ/cTribNac"), "municipal_code": t(inf, "serv/cServ/cTribMun"),
                    "description": t(inf, "serv/cServ/xDescServ"), "nbs_code": t(inf, "serv/cServ/cNBS"),
                    "location_code": t(inf, "serv/locPrest/cLocPrestacao"), "obra_code": t(obra, "cObra"),
                    "obra_address": obra_address},
        "amount": t(inf, "valores/vServPrest/vServ"), "deductions": t(inf, "valores/vDedRed/vDR") or "0.00",
        "tax_settings": {
            "issqn_taxation": REV_ISS.get(t(trib, "tribMun/tribISSQN")), "iss_retention": REV_RET.get(t(trib, "tribMun/tpRetISSQN")),
            "iss_rate": t(trib, "tribMun/pAliq"), "pis_cofins_cst": t(trib, "tribFed/piscofins/CST"),
            "pis_cofins_retention": t(trib, "tribFed/piscofins/tpRetPisCofins"), "inss_withheld": t(trib, "tribFed/vRetCP"),
            "pis_cofins_base": t(trib, "tribFed/piscofins/vBCPisCofins"), "pis_rate": t(trib, "tribFed/piscofins/pAliqPis"),
            "cofins_rate": t(trib, "tribFed/piscofins/pAliqCofins"), "pis_amount": t(trib, "tribFed/piscofins/vPis"),
            "cofins_amount": t(trib, "tribFed/piscofins/vCofins"),
            "irrf_withheld": t(trib, "tribFed/vRetIRRF"), "csll_withheld": t(trib, "tribFed/vRetCSLL"),
            "approx_tax": approx, "ibs_cbs": ibs,
        },
    }


def leaves(el, prefix: str = "") -> dict[str, str]:  # noqa: ANN001
    out: dict[str, str] = {}
    for ch in el:
        tag = ch.tag.replace(N, "")
        path = f"{prefix}{tag}"
        if len(ch):
            out.update(leaves(ch, path + "/"))
        else:
            out[path] = (ch.text or "").strip()
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lotes", default="data/oficial/producao/adn")
    ap.add_argument("--perfil", default="data/homologacao/empresa.json")
    a = ap.parse_args(argv)
    perfil = json.loads(Path(a.perfil).read_text(encoding="utf-8"))
    equal, differ, missing, extra = Counter(), Counter(), Counter(), Counter()
    built = failed = 0
    fail_reasons: Counter = Counter()
    for f in sorted(Path(a.lotes).glob("lote-*.json")):
        for item in json.loads(f.read_text(encoding="utf-8")).get("LoteDFe") or []:
            if item.get("TipoDocumento") != "NFSE":
                continue
            root = SafeET.fromstring(gzip.decompress(base64.b64decode(item["ArquivoXml"])))
            if t(root, "infNFSe/emit/CNPJ") != perfil["cnpj"]:
                continue
            inf = root.find(f"{N}infNFSe/{N}DPS/{N}infDPS")
            try:
                m = dps.montar(snapshot_from_real(inf, perfil), environment="restricted", serie="1",  # série real é do Emissor Web (E0010)
                               numero=int(t(inf, "nDPS")), agora=datetime.fromisoformat(t(inf, "dhEmi")))
            except dps.DpsIncompleta as exc:
                failed += 1
                fail_reasons.update(f"{x['campo']}:{x['codigo']}" for x in exc.faltas)
                continue
            except dps.DpsInvalidaXsd as exc:
                failed += 1
                fail_reasons.update(e.split(":", 1)[-1].strip()[:90] for e in exc.erros)
                continue
            built += 1
            ours = leaves(SafeET.fromstring(m.xml).find(N + "infDPS"))
            real = leaves(inf)
            for p, v in real.items():
                if p not in ours:
                    missing[p] += 1
                elif p.split("/")[-1] in SKIP_VALUES or ours[p] == v:
                    equal[p] += 1
                else:
                    differ[p] += 1
            for p in ours:
                if p not in real:
                    extra[p] += 1
    print(f"DPS reais da empresa: montadas={built} recusadas_localmente={failed}")
    for r, n in fail_reasons.most_common(10):
        print(f"  recusa: {r} x{n}")
    print(f"caminhos iguais (soma)={sum(equal.values())}")
    for name, c in (("DIVERGENTE", differ), ("AUSENTE_NO_CTRL", missing), ("EXTRA_NO_CTRL", extra)):
        for p, n in c.most_common():
            print(f"  {name}: {p} x{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
