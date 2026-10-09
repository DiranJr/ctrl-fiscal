"""Extrai da tabela OFICIAL de classificação tributária do IBS/CBS (SVRS — Portal DF-e, "TabelaClassificacaoTributaria",
dados embutidos na página) SOMENTE as classificações válidas para NFS-e (IndNfse = true), com vigência, CST,
percentuais de redução e indicadores usados nas regras E0958/E0959/E0964/E0971 do Anexo I.

Fonte: https://dfe-portal.svrs.rs.gov.br/DFE/TabelaClassificacaoTributaria (citada pelo IT 2025.002 v1.60 e pela
NT 009). O arquivo baixado é tratado como DADO (json.loads; nada é executado). Saída versionada:
services/shared/ctrl_common/dominios/cclasstrib_nfse.json (com URL, SHA-256 do download e data da publicação).

Uso: .venv\\Scripts\\python -I scripts\\extrair_cclasstrib.py <cct.json baixado>
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "services/shared/ctrl_common/dominios/cclasstrib_nfse.json"
URL = "https://dfe-portal.svrs.rs.gov.br/DFE/TabelaClassificacaoTributaria"


def main(src: str) -> int:
    raw = Path(src).read_bytes()
    data = json.loads(raw.decode("utf-8"))
    out = []
    for cst in data:
        for c in cst.get("ClassificacoesTributarias") or []:
            if not c.get("IndNfse"):
                continue
            out.append({
                "cst": cst["Cst"], "cst_nome": cst["NomeCst"], "cst_reducao_aliquota": bool(cst.get("IndReducaoAliq")),
                "cst_diferimento": bool(cst.get("IndDiferimento")), "cst_exige_tributacao": bool(cst.get("IndExigeTrib")),
                "codigo": c["CodClassTrib"], "nome": c.get("NomeReduzido") or c["NomeClassTrib"],
                "perc_red_ibs": str(c.get("PercRedIbs") or 0), "perc_red_cbs": str(c.get("PercRedCbs") or 0),
                "tipo_aliquota": c.get("TipoAliq"), "exige_tributacao_regular": bool(c.get("IndTribRegular")),
                "permite_credito_presumido": bool(c.get("IndPermiteCredPres")),
                "inicio_vigencia": (c.get("DthIniVig") or "")[:10] or None,
                "fim_vigencia": (c.get("DthFimVig") or "")[:10] or None,
                "publicacao": (c.get("DthPublicacao") or "")[:10] or None, "legislacao": c.get("TexUrlLegislacao"),
            })
    out.sort(key=lambda x: x["codigo"])
    meta = {"fonte": URL, "sha256_download": hashlib.sha256(raw).hexdigest(), "extraido_em": date.today().isoformat(),
            "publicacao_mais_recente": max(x["publicacao"] or "" for x in out), "filtro": "IndNfse = true",
            "total": len(out)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"meta": meta, "classificacoes": out}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
