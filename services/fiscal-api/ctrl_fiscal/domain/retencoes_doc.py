"""Retenções de NFS-e emitidas/recebidas a partir dos valores e indicações OFICIAIS (XML da NFS-e ou snapshot de
emissão). Usado pelos filtros (colunas normalizadas), pela exibição e pela migração 0005 (cópia congelada).

Retenção EFETIVA ≠ imposto informado:
- ISS: retido quando tpRetISSQN = 2/3 (valor = vISSQN da NFS-e).
- INSS/CP e IRRF: vRetCP / vRetIRRF > 0.
- Contribuições (PIS/COFINS/CSLL): o leiaute traz UM valor consolidado (vRetCSLL) e a modalidade (tpRetPisCofins)
  que diz quais contribuições ele cobre. Não há rateio por tributo no XML e a plataforma não o inventa.
  NT 008 v1.02 (DANFSe): com tpRetPisCofins = 1, "Contribuições Sociais – Retidas" = vRetCSLL + vPis + vCofins.
- vPis/vCofins fora do código 1 = débito de apuração própria (informados, não retidos).

Classe: WITH (há retenção efetiva), WITHOUT (indicações completas e nenhuma retenção), UNKNOWN (sem indicação da
retenção do ISS no documento, ou retenção indicada sem o valor correspondente).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

ZERO = Decimal("0")
TP_RET_PIS_COFINS: dict[str, tuple[bool | None, bool | None, bool | None]] = {
    "0": (False, False, False), "1": (True, True, None), "2": (False, False, None), "3": (True, True, True),
    "4": (True, True, False), "5": (True, False, False), "6": (False, True, False), "7": (False, True, True),
    "8": (False, False, True), "9": (True, False, True),
}
TP_RET_PIS_COFINS_LABELS = {
    "0": "PIS/COFINS/CSLL não retidos", "1": "PIS/COFINS retidos", "2": "PIS/COFINS não retidos",
    "3": "PIS/COFINS/CSLL retidos", "4": "PIS/COFINS retidos, CSLL não retido", "5": "PIS retido, COFINS/CSLL não retidos",
    "6": "COFINS retido, PIS/CSLL não retidos", "7": "PIS não retido, COFINS/CSLL retidos",
    "8": "PIS/COFINS não retidos, CSLL retido", "9": "COFINS não retido, PIS/CSLL retidos",
}
RETAINED_CODES = {k for k, v in TP_RET_PIS_COFINS.items() if any(v)}
ISS_RETAINED = ("RETAINED_BY_TOMADOR", "RETAINED_BY_INTERMEDIARY")


def _d(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))


def classify(*, indicators: dict | None, ret_iss: Any, ret_inss: Any, ret_irrf: Any, ret_csll: Any,
             total_retained: Any) -> dict:
    """Colunas normalizadas para filtro. Entradas = colunas já gravadas do documento."""
    ind = indicators or {}
    iss_status = (ind.get("iss") or {}).get("status") or "NOT_INFORMED"
    code = None
    src = ((ind.get("pis") or {}).get("source") or (ind.get("csll") or {}).get("source") or "")
    if src.startswith("tpRetPisCofins="):
        code = src.split("=", 1)[1] or None
    code = code or ind.get("pis_cofins_retention_code")
    v_pis = _d((ind.get("pis") or {}).get("informed_amount"))
    v_cofins = _d((ind.get("cofins") or {}).get("informed_amount"))
    inss, irrf, csll, iss = _d(ret_inss), _d(ret_irrf), _d(ret_csll), _d(ret_iss)
    if code == "1":
        parts = [v for v in (csll, v_pis, v_cofins) if v is not None]
        contrib = sum(parts, ZERO) if parts else None
    else:
        contrib = csll
    has_iss = iss_status in ISS_RETAINED
    has_inss = bool(inss and inss > 0)
    has_irrf = bool(irrf and irrf > 0)
    has_contrib = bool(contrib and contrib > 0)
    total = _d(total_retained)
    missing_value = (has_iss and iss is None) or (code in RETAINED_CODES and code != "1" and not has_contrib)
    if iss_status == "NOT_INFORMED" or missing_value:
        klass = "UNKNOWN"
    elif has_iss or has_inss or has_irrf or has_contrib or (total is not None and total > 0):
        klass = "WITH"
    else:
        klass = "WITHOUT"
    if total is not None:
        effective = total
    elif klass == "WITHOUT":
        effective = ZERO  # nenhuma retenção indicada; vTotalRet é opcional e fica ausente nesses casos
    else:
        effective = None
    return {"ret_class": klass, "ret_has_iss": has_iss, "ret_has_inss": has_inss, "ret_has_irrf": has_irrf,
            "ret_has_contrib": has_contrib, "ret_pis_cofins_code": code, "ret_contrib_amount": contrib,
            "ret_total_effective": effective}


def apply(doc) -> None:  # noqa: ANN001
    for k, v in classify(indicators=doc.retention_indicators, ret_iss=doc.ret_iss, ret_inss=doc.ret_inss,
                         ret_irrf=doc.ret_irrf, ret_csll=doc.ret_csll, total_retained=doc.total_retained).items():
        setattr(doc, k, v)


def _s(v: Decimal | None) -> str | None:
    return None if v is None else f"{v.quantize(Decimal('0.01'))}"


def out(doc) -> dict:  # noqa: ANN001
    """Resumo exibível: retenção efetiva separada do imposto informado; contribuições sem rateio."""
    ind = doc.retention_indicators or {}
    code = doc.ret_pis_cofins_code
    covered = TP_RET_PIS_COFINS.get(code or "")
    v_pis = (ind.get("pis") or {}).get("informed_amount")
    v_cofins = (ind.get("cofins") or {}).get("informed_amount")
    return {
        "classification": doc.ret_class or "UNKNOWN",
        "iss": {"retained": bool(doc.ret_has_iss), "amount": _s(_d(doc.ret_iss)) if doc.ret_has_iss else None},
        "inss": _s(_d(doc.ret_inss)),
        "irrf": _s(_d(doc.ret_irrf)),
        "contributions": {
            "amount": _s(_d(doc.ret_contrib_amount)),
            "code": code,
            "label": TP_RET_PIS_COFINS_LABELS.get(code or ""),
            "covered": None if covered is None else {"pis": covered[0], "cofins": covered[1], "csll": covered[2]},
            "consolidated": True,
            "note": ("Valor consolidado (vRetCSLL) das contribuições indicadas pela modalidade; o XML não separa "
                     "PIS, COFINS e CSLL." if code != "1" else
                     "Modalidade 1: conforme a NT 008 v1.02, vRetCSLL + vPis + vCofins; o XML não separa por tributo."),
        },
        "informed_not_withheld": ({"pis": None, "cofins": None} if code == "1" else {"pis": v_pis, "cofins": v_cofins}),
        "total_withheld": _s(_d(doc.total_retained)),
        "total_withheld_source": "vTotalRet" if doc.total_retained is not None else None,
    }
