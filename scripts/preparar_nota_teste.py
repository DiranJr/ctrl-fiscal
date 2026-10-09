"""Prepara data/homologacao/nota-teste.json a partir de uma NFS-e AUTORIZADA real da empresa (ADN produção, leitura
local), com NOVA identidade fiscal para o teste em Produção Restrita.

Uso: .venv\\Scripts\\python -I scripts\\preparar_nota_teste.py [--nsu N] [--data-prestacao AAAA-MM-DD]

- Referência: a NFS-e emitida pela empresa mais recente que não tenha evento de cancelamento nem tenha sido
  substituída (nenhuma outra DPS aponta para ela em subst/chSubstda). --nsu força outra.
- Preserva os dados conhecidos: tomador, serviço (códigos, descrição, NBS, local, obra), valor, ISSQN, PIS/COFINS,
  retenções e tributos aproximados.
- Nova identidade: série 1 da plataforma e número da fila (definidos na emissão), nova competência e nova
  data da prestação (dCompet); dhEmi é o instante da emissão.
- NÃO copia IBS/CBS: os códigos da referência não provam adequação ao novo serviço/data. Ficam como decisão
  contábil explícita (`_decisao_contabil`). Também não informa pAliq (a confirmar pela parametrização).
- Imprime só um resumo sem dados pessoais/fiscais; o arquivo fica fora do git.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import sys
from datetime import date
from pathlib import Path

from defusedxml import ElementTree as SafeET

N = "{http://www.sped.fazenda.gov.br/nfse}"
REV_ISS = {"1": "TAXABLE", "2": "IMMUNE", "3": "EXPORT", "4": "NOT_INCIDENT"}
REV_RET = {"1": "NOT_RETAINED", "2": "RETAINED_BY_TOMADOR", "3": "RETAINED_BY_INTERMEDIARY"}


def t(el, path: str) -> str | None:  # noqa: ANN001
    if el is None:
        return None
    f = el.find("/".join(N + p for p in path.split("/")))
    return f.text.strip() if f is not None and f.text else None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lotes", default="data/oficial/producao/adn")
    ap.add_argument("--perfil", default="data/homologacao/empresa.json")
    ap.add_argument("--saida", default="data/homologacao/nota-teste.json")
    ap.add_argument("--nsu", type=int)
    ap.add_argument("--data-prestacao", default=date.today().isoformat())
    a = ap.parse_args(argv)
    me = json.loads(Path(a.perfil).read_text(encoding="utf-8"))["cnpj"]
    issued, cancelled, substituted = [], set(), set()
    for f in sorted(Path(a.lotes).glob("lote-*.json")):
        for item in json.loads(f.read_text(encoding="utf-8")).get("LoteDFe") or []:
            root = SafeET.fromstring(gzip.decompress(base64.b64decode(item["ArquivoXml"])))
            if item.get("TipoDocumento") == "EVENTO":
                ped = root.find(f"{N}infEvento/{N}pedRegEvento/{N}infPedReg")
                if ped is not None and (ped.find(f"{N}e101101") is not None or ped.find(f"{N}e105102") is not None):
                    cancelled.add(t(ped, "chNFSe"))
                continue
            inf = root.find(f"{N}infNFSe")
            if t(inf, "emit/CNPJ") != me:
                continue
            dps = inf.find(f"{N}DPS/{N}infDPS")
            if t(dps, "subst/chSubstda"):
                substituted.add(t(dps, "subst/chSubstda"))
            issued.append((int(item["NSU"]), item.get("ChaveAcesso"), inf, dps))
    valid = [x for x in issued if x[1] not in cancelled and x[1] not in substituted and t(x[3], "subst/chSubstda") is None]
    if a.nsu:
        valid = [x for x in valid if x[0] == a.nsu]
    if not valid:
        sys.stderr.write("Nenhuma NFS-e autorizada elegível como referência.\n")
        return 2
    nsu, _key, inf, d = max(valid, key=lambda x: x[0])
    toma, end = d.find(f"{N}toma"), d.find(f"{N}toma/{N}end")
    obra = d.find(f"{N}serv/{N}obra")
    trib = d.find(f"{N}valores/{N}trib")
    tot = trib.find(f"{N}totTrib")
    if tot.find(f"{N}vTotTrib") is not None:
        approx = {"mode": "VALUES", "federal": t(tot, "vTotTrib/vTotTribFed"), "state": t(tot, "vTotTrib/vTotTribEst"),
                  "municipal": t(tot, "vTotTrib/vTotTribMun"), "simples": None}
    elif tot.find(f"{N}pTotTrib") is not None:
        approx = {"mode": "PERCENT", "federal": t(tot, "pTotTrib/pTotTribFed"), "state": t(tot, "pTotTrib/pTotTribEst"),
                  "municipal": t(tot, "pTotTrib/pTotTribMun"), "simples": None}
    elif tot.find(f"{N}pTotTribSN") is not None:
        approx = {"mode": "SIMPLES_PERCENT", "federal": None, "state": None, "municipal": None, "simples": t(tot, "pTotTribSN")}
    else:
        approx = {"mode": "NOT_INFORMED", "federal": None, "state": None, "municipal": None, "simples": None}
    doc = t(toma, "CNPJ") or t(toma, "CPF")
    obra_end = None
    if obra is not None and obra.find(f"{N}end") is not None:
        obra_end = {"postal_code": t(obra, "end/CEP"), "street": t(obra, "end/xLgr"), "number": t(obra, "end/nro"),
                    "complement": t(obra, "end/xCpl"), "district": t(obra, "end/xBairro")}
    data_prest = date.fromisoformat(a.data_prestacao)
    ref_ibs = d.find(f"{N}IBSCBS") is not None
    g = d.find(f"{N}IBSCBS")
    ref_ibs_vals = None if g is None else {
        "finNFSe": t(g, "finNFSe"), "indFinal": t(g, "indFinal"), "cIndOp": t(g, "cIndOp"), "indDest": t(g, "indDest"),
        "CST": t(g, "valores/trib/gIBSCBS/CST"), "cClassTrib": t(g, "valores/trib/gIBSCBS/cClassTrib"),
    }
    p_aliq_aplic = t(inf, "valores/pAliqAplic")  # alíquota APLICADA pelo sistema na nota de referência
    loc_incid = t(inf, "cLocIncid")
    nota = {
        "_leia": [
            "Nota de TESTE para Produção Restrita (sem validade fiscal), derivada de NFS-e autorizada real da empresa.",
            "Nova identidade: série 1 e número da fila da plataforma, nova competência e nova data da prestação.",
            "Arquivo local (fora do git). Não publicar em chat/relatórios.",
        ],
        "_referencia": {"origem": "ADN produção (somente leitura)", "nsu": nsu,
                        "competencia_original": (t(d, "dCompet") or "")[:7], "tinha_grupo_ibscbs": ref_ibs},
        "_decisao_contabil": [
            "tax_settings.ibs_cbs (cIndOp, CST, cClassTrib, indFinal): NÃO copiado da referência; a referência não "
            "comprova adequação ao novo serviço/data. Ver _revisao_contabil_ibs_cbs.",
        ],
        "_revisao_contabil_ibs_cbs": {"valores_da_referencia": ref_ibs_vals,
                                      "nao_suportado_na_plataforma": "indDest=1 (dest), grupo imovel, gRefNFSe, tpOper"},
        "_pAliq": {"valor": p_aliq_aplic, "origem": "pAliqAplic da NFS-e de referência (alíquota aplicada pelo sistema)",
                   "municipio_incidencia_referencia": loc_incid,
                   "regra": "RN 506/507: opSimpNac=1 => proibida com convênio do município de incidência ATIVO (E0617), "
                            "obrigatória com convênio INATIVO e regEspTrib=0 (E0619); conferido na transmissão."},
        "tomador": {
            "document_type": "CNPJ" if t(toma, "CNPJ") else "CPF", "document": doc, "name": t(toma, "xNome"),
            "email": t(toma, "email"), "phone": t(toma, "fone"), "municipal_registration": t(toma, "IM"),
            "municipality_code": t(end, "endNac/cMun"), "postal_code": t(end, "endNac/CEP"),
            "address": {"street": t(end, "xLgr"), "number": t(end, "nro"), "complement": t(end, "xCpl"),
                        "district": t(end, "xBairro"), "city": None, "state": None} if end is not None else None,
        },
        "nota": {
            "competence": data_prest.strftime("%Y-%m"),
            "service_date": data_prest.isoformat(),
            "service": {
                "national_code": t(d, "serv/cServ/cTribNac"), "municipal_code": t(d, "serv/cServ/cTribMun"),
                "description": t(d, "serv/cServ/xDescServ"), "nbs_code": t(d, "serv/cServ/cNBS"),
                "location_code": t(d, "serv/locPrest/cLocPrestacao"), "obra_code": t(obra, "cObra"),
                "obra_address": obra_end,
            },
            "amount": t(d, "valores/vServPrest/vServ"),
            "deductions": t(d, "valores/vDedRed/vDR") or "0.00",
            "tax_settings": {
                "issqn_taxation": REV_ISS.get(t(trib, "tribMun/tribISSQN") or ""),
                "iss_retention": REV_RET.get(t(trib, "tribMun/tpRetISSQN") or ""),
                "iss_rate": p_aliq_aplic,
                "pis_cofins_cst": t(trib, "tribFed/piscofins/CST"),
                "pis_cofins_retention": t(trib, "tribFed/piscofins/tpRetPisCofins"),
                "pis_cofins_base": t(trib, "tribFed/piscofins/vBCPisCofins"),
                "pis_rate": t(trib, "tribFed/piscofins/pAliqPis"),
                "cofins_rate": t(trib, "tribFed/piscofins/pAliqCofins"),
                "pis_amount": t(trib, "tribFed/piscofins/vPis"),
                "cofins_amount": t(trib, "tribFed/piscofins/vCofins"),
                "irrf_withheld": t(trib, "tribFed/vRetIRRF"),
                "inss_withheld": t(trib, "tribFed/vRetCP"),
                "csll_withheld": t(trib, "tribFed/vRetCSLL"),
                "approx_tax": approx,
                "ibs_cbs": None,
            },
        },
        "lote_agendado": {"quantidade": 2, "scheduled_at": None,
                          "_ajuda": "Horário LOCAL da empresa, ex.: 2026-10-07T18:00:00-03:00."},
    }
    out = Path(a.saida)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(nota, ensure_ascii=False, indent=2), encoding="utf-8")
    filled = sorted(k for k, v in nota["nota"]["tax_settings"].items() if v)
    print(f"referência NSU={nsu} (competência original {nota['_referencia']['competencia_original']}); "
          f"emitidas={len(issued)} canceladas={len(cancelled & {x[1] for x in issued})} substituídas={len(substituted)}")
    print(f"tomador: tipo={nota['tomador']['document_type']} endereço={'sim' if end is not None else 'não'} "
          f"fone={'sim' if nota['tomador']['phone'] else 'não'}")
    print(f"serviço: nbs={'sim' if nota['nota']['service']['nbs_code'] else 'não'} "
          f"cTribMun={'sim' if nota['nota']['service']['municipal_code'] else 'não'} "
          f"obra={'código' if nota['nota']['service']['obra_code'] else 'endereço' if obra_end else 'não'}")
    print(f"tributos preenchidos: {filled}; totTrib={approx['mode']}; referência tinha IBSCBS={ref_ibs}")
    print(f"nova identidade: competência={nota['nota']['competence']} dCompet={nota['nota']['service_date']} série=1")
    print(f"decisão contábil pendente: ibs_cbs | gravado em {out} (conteúdo não exibido)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
