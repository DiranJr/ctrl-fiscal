"""Gera o índice compacto das regras oficiais do Anexo I (código → elemento XML, regra, mensagem, efeito) usado pelo
detalhamento das pendências dos rascunhos. Fonte: docs/oficial/regras-dps-anexo-i.json (extraído da planilha oficial
do Anexo I, com sha256 registrado). Texto copiado sem alteração; nada é resumido nem reescrito.

Uso: .venv\\Scripts\\python -I scripts\\gerar_indice_regras_anexo_i.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORIGEM = ROOT / "docs" / "oficial" / "regras-dps-anexo-i.json"
DESTINO = ROOT / "services" / "shared" / "ctrl_common" / "dominios" / "regras_anexo_i.json"


def main() -> None:
    d = json.loads(ORIGEM.read_text(encoding="utf-8"))
    regras = {}
    for r in d["regras"]:
        if r["codigo"] in regras:
            raise SystemExit(f"código repetido na fonte: {r['codigo']}")
        regras[r["codigo"]] = {"elemento": r["elemento"], "regra": r["regra"], "mensagem": r["mensagem"],
                               "efeito": r["efeito"]}
    out = {"fonte": d["fonte"], "fonte_sha256": d["fonte_sha256"],
           "atribuicao": "Anexo I — RN DPS/NFS-e, Sistema Nacional NFS-e (gov.br), CC BY-ND 3.0; cópia sem alteração.",
           "regras": dict(sorted(regras.items()))}
    DESTINO.write_text(json.dumps(out, ensure_ascii=False, indent=0) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(regras)} regras -> {DESTINO.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
