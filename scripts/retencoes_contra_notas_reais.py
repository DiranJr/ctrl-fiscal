"""Confere ctrl_fiscal.domain.retencoes.resumo contra as NFS-e REAIS aceitas da empresa (ADN, leitura local):
recalcula vTotalRet / vLiq / vISSQN a partir da DPS embutida e compara com os valores que a Sefin gravou na NFS-e.
Para o ISS retido sem pAliq na DPS, usa pAliqAplic da NFS-e (o que a Sefin aplicou) só para fechar a conta.
Imprime apenas contagens e combinações — nenhum dado fiscal identificável.

Uso: .venv\\Scripts\\python -I scripts\\retencoes_contra_notas_reais.py
"""
from __future__ import annotations

import base64
import gzip
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("services/shared", "services/fiscal-api", "scripts"):
    sys.path.insert(0, str(ROOT / sub))

from ctrl_fiscal.domain import retencoes  # noqa: E402

N = "{http://www.sped.fazenda.gov.br/nfse}"


def t(el, path):
    if el is None:
        return None
    return el.findtext("/".join(f"{N}{p}" for p in path.split("/")))


def main() -> int:
    from defusedxml import ElementTree as SafeET

    cnpj = json.loads((ROOT / "data/homologacao/empresa.json").read_text(encoding="utf-8"))["cnpj"]
    stats: Counter = Counter()
    combos: Counter = Counter()
    divergencias: list[str] = []
    for f in sorted((ROOT / "data/oficial/producao/adn").glob("lote-*.json")):
        for item in json.loads(f.read_text(encoding="utf-8")).get("LoteDFe") or []:
            if item.get("TipoDocumento") != "NFSE":
                continue
            root = SafeET.fromstring(gzip.decompress(base64.b64decode(item["ArquivoXml"])))
            inf_nfse = root.find(f"{N}infNFSe")
            if t(inf_nfse, "emit/CNPJ") != cnpj:
                continue
            inf = inf_nfse.find(f"{N}DPS/{N}infDPS")
            stats["notas"] += 1
            tp_iss = t(inf, "valores/trib/tribMun/tpRetISSQN")
            tax = {
                "iss_retention": {"1": "NOT_RETAINED", "2": "RETAINED_BY_TOMADOR", "3": "RETAINED_BY_INTERMEDIARY"}.get(tp_iss or ""),
                "iss_rate": t(inf, "valores/trib/tribMun/pAliq"),
                "inss_withheld": t(inf, "valores/trib/tribFed/vRetCP"),
                "irrf_withheld": t(inf, "valores/trib/tribFed/vRetIRRF"),
                "csll_withheld": t(inf, "valores/trib/tribFed/vRetCSLL"),
                "pis_cofins_retention": t(inf, "valores/trib/tribFed/piscofins/tpRetPisCofins"),
                "pis_amount": t(inf, "valores/trib/tribFed/piscofins/vPis"),
                "cofins_amount": t(inf, "valores/trib/tribFed/piscofins/vCofins"),
            }
            sefin_rate = t(inf_nfse, "valores/pAliqAplic")
            if tax["iss_retention"] in retencoes.ISS_RETAINED and not tax["iss_rate"]:
                stats["iss_retido_sem_pAliq_na_dps"] += 1
                tax = {**tax, "iss_rate": sefin_rate}
            r = retencoes.resumo(t(inf, "valores/vServPrest/vServ"), t(inf, "valores/vDescCondIncond/vDescIncond"),
                                 t(inf, "valores/vDescCondIncond/vDescCond"), t(inf, "valores/vDedRed/vDR"), tax)
            combos[(tp_iss, tax["pis_cofins_retention"] or "-", "CP" if tax["inss_withheld"] else "",
                    "IRRF" if tax["irrf_withheld"] else "", "vRetCSLL" if tax["csll_withheld"] else "",
                    "vPis/vCofins" if (tax["pis_amount"] or tax["cofins_amount"]) else "")] += 1
            got_tot = Decimal(t(inf_nfse, "valores/vTotalRet") or "0.00")
            got_liq = Decimal(t(inf_nfse, "valores/vLiq"))
            ok_tot = Decimal(r["total_withheld"]) == got_tot
            ok_liq = Decimal(r["net_amount"]) == got_liq
            stats["vTotalRet_igual" if ok_tot else "vTotalRet_DIVERGE"] += 1
            stats["vLiq_igual" if ok_liq else "vLiq_DIVERGE"] += 1
            if r["iss"]["amount"] is not None and t(inf_nfse, "valores/vISSQN"):
                ok_iss = Decimal(r["iss"]["amount"]) == Decimal(t(inf_nfse, "valores/vISSQN"))
                stats["vISSQN_retido_igual" if ok_iss else "vISSQN_retido_DIVERGE"] += 1
            if r["iss"]["base"] and t(inf_nfse, "valores/vBC"):
                stats["vBC_igual" if Decimal(r["iss"]["base"]) == Decimal(t(inf_nfse, "valores/vBC")) else "vBC_DIVERGE"] += 1
            if tax["pis_amount"] or tax["cofins_amount"]:
                # Prova de que vPis/vCofins não entram: se entrassem, o total seria maior que o da Sefin.
                extra = sum(Decimal(x) for x in (tax["pis_amount"], tax["cofins_amount"]) if x)
                stats["vPis_vCofins_fora_do_total_confirmado" if ok_tot and extra > 0 else "vPis_vCofins_inconclusivo"] += 1
            if not (ok_tot and ok_liq) and len(divergencias) < 5:
                divergencias.append(f"tpRetISSQN={tp_iss} tpRetPisCofins={tax['pis_cofins_retention']} "
                                    f"calc(tot={r['total_withheld']}, liq={r['net_amount']}) sefin(tot={got_tot}, liq={got_liq})")
    print(json.dumps(dict(stats), indent=1, ensure_ascii=False))
    print("combinações (tpRetISSQN, tpRetPisCofins, CP, IRRF, vRetCSLL, vPis/vCofins): quantidade")
    for k, v in combos.most_common():
        print(" ", k, v)
    for d in divergencias:
        print("DIVERGÊNCIA:", d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
