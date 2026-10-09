"""Passa as DPS REAIS aceitas da empresa (ADN produção, leitura local) pelas regras de conformidade locais
(ctrl_fiscal.domain.regras_dps) para medir FALSOS POSITIVOS: uma regra que acusa nota aceita pela Sefin está
mais restrita que o sistema oficial (ou a nota é de outra condição, ex.: data). Imprime só contagens por regra.

Uso: .venv\\Scripts\\python -I scripts\\regras_contra_notas_reais.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for sub in ("services/shared", "services/fiscal-api", "services/nfse-api", "scripts"):
    sys.path.insert(0, str(ROOT / sub))

from comparar_dps_reais import N, snapshot_from_real  # noqa: E402
from ctrl_fiscal.domain.regras_dps import dominio_issues, negocio_issues  # noqa: E402


def main() -> int:
    import base64
    import gzip

    from defusedxml import ElementTree as SafeET

    perfil = json.loads((ROOT / "data/homologacao/empresa.json").read_text(encoding="utf-8"))
    company = SimpleNamespace(cnpj=perfil["cnpj"], timezone="America/Sao_Paulo",
                              fiscal_profile={k: perfil.get(k) for k in ("simples_option", "simples_regime", "special_regime")})
    hits: Counter = Counter()
    total = 0
    for f in sorted((ROOT / "data/oficial/producao/adn").glob("lote-*.json")):
        for item in json.loads(f.read_text(encoding="utf-8")).get("LoteDFe") or []:
            if item.get("TipoDocumento") != "NFSE":
                continue
            root = SafeET.fromstring(gzip.decompress(base64.b64decode(item["ArquivoXml"])))
            if root.findtext(f"{N}infNFSe/{N}emit/{N}CNPJ") != perfil["cnpj"]:
                continue
            inf = root.find(f"{N}infNFSe/{N}DPS/{N}infDPS")
            snap = snapshot_from_real(inf, perfil)
            svc = snap["service"]
            d = SimpleNamespace(
                service_national_code=svc["national_code"], service_municipal_code=svc["municipal_code"],
                service_nbs_code=svc["nbs_code"], service_location_code=svc["location_code"],
                service_obra_code=svc["obra_code"], service_obra_address=svc["obra_address"],
                service_date=snap["service_date"], amount=snap["amount"], deductions=snap["deductions"],
                discount_unconditional=None, discount_conditional=None, tax_settings=snap["tax_settings"])
            when = datetime.fromisoformat(inf.findtext(f"{N}dhEmi"))
            total += 1
            for i in dominio_issues(d, snap["customer"]) + negocio_issues(d, company, snap["customer"], when):
                if i["severity"] == "error":
                    hits[f"{i['rule']} {i['code']}"] += 1
    print(f"DPS reais aceitas avaliadas: {total}")
    print("falsos positivos (regra acusou nota aceita):", dict(hits) or "nenhum")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
