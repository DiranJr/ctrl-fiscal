"""Investigação das séries 900 e 70000 nas DPS reais da empresa (ADN produção, leitura local) contra os XSD
oficiais empacotados, SEM alterar os XSD e sem relaxar validação.

Uso: .venv\\Scripts\\python -I scripts\\investigar_series_xsd.py

Para cada série: ambiente de origem (tpAmb da DPS, ambGer/procEmi/verAplic da NFS-e), contagem, e resultado da
validação XSD (lxml/libxml2, semântica W3C) da DPS assinada ORIGINAL contra os pacotes de produção (20260209) e
Produção Restrita (20260727), com o elemento/tipo/faceta violados. Também testa o tipo TSSerieDPS isolado com
valores sintéticos. Imprime só contagens, códigos de aplicação e mensagens do validador.
"""
from __future__ import annotations

import base64
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
XSD = ROOT / "services/nfse-api/nfse_api/xsd"
PKGS = {"producao-20260209": XSD / "producao-v1.01-20260209", "prodrest-20260727": XSD / "prodrest-v1.01-20260727"}
N = "{http://www.sped.fazenda.gov.br/nfse}"


class NoNet(etree.Resolver):
    def resolve(self, url, pubid, context):  # noqa: ANN001, ANN201
        if url.startswith(("http://", "https://")):
            raise ValueError("rede bloqueada")
        return None


def schema(path: Path) -> etree.XMLSchema:
    parser = etree.XMLParser(no_network=True, resolve_entities=False)
    parser.resolvers.add(NoNet())
    return etree.XMLSchema(etree.parse(str(path), parser))


def serie_type_schema(pkg: Path) -> etree.XMLSchema:
    """Esquema mínimo que só importa tiposSimples e declara <s> do tipo TSSerieDPS (para testar o tipo isolado)."""
    doc = f"""<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns="http://www.sped.fazenda.gov.br/nfse"
  targetNamespace="http://www.sped.fazenda.gov.br/nfse" elementFormDefault="qualified">
  <xs:include schemaLocation="{(pkg / 'tiposSimples_v1.01.xsd').as_uri()}"/>
  <xs:element name="s" type="TSSerieDPS"/></xs:schema>"""
    return etree.XMLSchema(etree.fromstring(doc.encode(), base_url=pkg.as_uri() + "/"))


def main() -> int:
    perfil = json.loads((ROOT / "data/homologacao/empresa.json").read_text(encoding="utf-8"))
    schemas = {k: schema(v / "DPS_v1.01.xsd") for k, v in PKGS.items()}
    origem: dict[str, Counter] = defaultdict(Counter)
    valid: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for f in sorted((ROOT / "data/oficial/producao/adn").glob("lote-*.json")):
        for item in json.loads(f.read_text(encoding="utf-8")).get("LoteDFe") or []:
            if item.get("TipoDocumento") != "NFSE":
                continue
            root = etree.fromstring(gzip.decompress(base64.b64decode(item["ArquivoXml"])))
            inf = root.find(f"{N}infNFSe")
            if inf.findtext(f"{N}emit/{N}CNPJ") != perfil["cnpj"]:
                continue
            dps_el = inf.find(f"{N}DPS")
            infdps = dps_el.find(f"{N}infDPS")
            serie = infdps.findtext(f"{N}serie")
            origem[serie].update([
                f"versao_DPS={dps_el.get('versao')}", f"tpAmb_DPS={infdps.findtext(N + 'tpAmb')}",
                f"ambGer_NFSe={inf.findtext(N + 'ambGer')}", f"procEmi={inf.findtext(N + 'procEmi')}",
                f"tpEmis={inf.findtext(N + 'tpEmis')}", f"verAplic_DPS={infdps.findtext(N + 'verAplic')}",
            ])
            standalone = etree.fromstring(etree.tostring(dps_el))  # DPS original assinada, sem alteração
            for k, sch in schemas.items():
                if sch.validate(etree.ElementTree(standalone)):
                    valid[(serie, k)]["VALIDA"] += 1
                else:
                    for e in sch.error_log:
                        msg = re.sub(r"'[^']*'", "'…'", e.message)
                        valid[(serie, k)][f"{e.path.split('/')[-1] if e.path else ''}: {msg}"[:200]] += 1
    for serie in sorted(origem, key=lambda s: int(s)):
        print(f"\n=== série {serie} ({max(origem[serie].values())} NFS-e emitidas pela empresa)")
        print("  origem:", dict(origem[serie]))
        for k in PKGS:
            print(f"  XSD {k}:", dict(valid[(serie, k)]))
    print("\n=== tipo TSSerieDPS isolado (valores sintéticos)")
    for k, pkg in PKGS.items():
        sch = serie_type_schema(pkg)
        res = {v: sch.validate(etree.ElementTree(etree.fromstring(
            f'<s xmlns="http://www.sped.fazenda.gov.br/nfse">{v}</s>'.encode()))) for v in
            ("1", "900", "70000", "89999", "90000", "99999", "^900$")}
        print(f"  {k}: {res}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
