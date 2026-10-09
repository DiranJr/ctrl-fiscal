"""Assinatura da OPERAÇÃO para a configuração tributária (perfil de serviço) — mesma regra no core e no fiscal.

Duas operações são EQUIVALENTES quando têm os mesmos códigos oficiais de enquadramento: cTribNac, cNBS, tribISSQN,
CST do PIS/COFINS e a classificação IBS/CBS (cIndOp, CST, cClassTrib e o grupo de tributação regular). Uma
configuração APROVADA pode ser reutilizada por operações equivalentes; qualquer diferença é outra variante, que precisa
de revisão e aprovação próprias.

Ficam FORA da assinatura os dados de cada nota (tomador, valores, retenções, datas, local, obra, imóvel, destinatário,
diferimento, indFinal, descrição e cTribMun, que depende do município de incidência).
"""
from __future__ import annotations

import hashlib
import json

ROTULOS = {
    "national_code": "cTribNac", "nbs_code": "cNBS", "issqn_taxation": "tribISSQN",
    "pis_cofins_cst": "CST do PIS/COFINS", "operation_indicator": "cIndOp", "cst": "CST do IBS/CBS",
    "class_code": "cClassTrib", "regular_cst": "CSTReg", "regular_class_code": "cClassTribReg",
}
CAMPOS = tuple(ROTULOS)


def assinatura(national_code: str | None, nbs_code: str | None, issqn_taxation: str | None,
               pis_cofins_cst: str | None, ibs_cbs: dict | None) -> dict:
    ibs = ibs_cbs or {}
    reg = ibs.get("regular") or {}
    return {"national_code": national_code or None, "nbs_code": nbs_code or None,
            "issqn_taxation": issqn_taxation or None, "pis_cofins_cst": pis_cofins_cst or None,
            "operation_indicator": ibs.get("operation_indicator") or None, "cst": ibs.get("cst") or None,
            "class_code": ibs.get("class_code") or None, "regular_cst": reg.get("cst") or None,
            "regular_class_code": reg.get("class_code") or None}


def chave(sig: dict) -> str:
    """Identificador estável da variante (origin_key do perfil): independe da ordem e do nome."""
    bruto = json.dumps({k: sig.get(k) for k in CAMPOS}, sort_keys=True, separators=(",", ":"))
    return "op:" + hashlib.sha256(bruto.encode()).hexdigest()[:32]


def diferencas(a: dict, b: dict) -> list[str]:
    """Rótulos oficiais dos campos em que as duas assinaturas diferem."""
    return [ROTULOS[k] for k in CAMPOS if (a.get(k) or None) != (b.get(k) or None)]
