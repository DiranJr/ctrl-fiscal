"""Resumo de retenções da DPS — fonte ÚNICA para rascunho (DraftOut), revisão, snapshot de emissão e testes.

Separa VALOR DO TRIBUTO de VALOR EFETIVAMENTE RETIDO (XSD v1.01 TCTribFederal/TCTribOutrosPisCofins, Anexo I):
- tribFed/vRetCSLL = valor consolidado das contribuições retidas indicadas por tpRetPisCofins (PIS, COFINS e/ou
  CSLL). E0724 o exige para todo código fora de {0, 2}; E0720 o proíbe com código 0.
- piscofins/vPis e piscofins/vCofins = "débito de apuração própria". Fora da modalidade 1 são valores informados
  (não retidos). NA MODALIDADE 1 (PIS/COFINS retidos), a NT 008 v1.02 manda APRESENTAR no DANFSe
  "Contribuições Sociais – Retidas" = vRetCSLL + vPis + vCofins (e PIS/COFINS próprios = 0,00): ou seja, ali vPis e
  vCofins representam valores retidos. Não há regra geral de que nunca sejam retenção.
- O que NÃO está confirmado é o CÁLCULO: se a Sefin inclui vPis/vCofins em vTotalRet/vLiq na modalidade 1 (nenhuma
  NFS-e real com código 1 até agora). Por isso, no rascunho, a modalidade 1 sai com complete=false, pendência
  federal.contributions_code_1 e os valores em federal.code1_pis_cofins (não somados ao total desta prévia);
  o total oficial vem da NFS-e autorizada (vTotalRet).
- tribFed/vRetCP (contribuição previdenciária) e tribFed/vRetIRRF: valores retidos.
- ISSQN entra no total somente quando retido (tpRetISSQN = 2 tomador ou 3 intermediário).

Fórmulas (XSD TCValoresNFSe e Anexo I):
  vBC       = vServ − vDescIncond − vDR          (benefício municipal não é informado pelo rascunho)
  vISSQN    = vBC × pAliq / 100                  (arredondamento BANCÁRIO a 2 casas — ROUND_HALF_EVEN: fecha 186/186
                                                  NFS-e reais aceitas; HALF_UP diverge 1 centavo em 4, truncar em 79)
  vTotalRet = vRetCP + vRetIRRF + vRetCSLL + vISSQN (só se retido)
  vLiq      = vServ − vDescIncond − vDescCond − vTotalRet

Sem pAliq na DPS, a Sefin aplica a alíquota parametrizada do município: o ISSQN retido fica "DETERMINED_BY_SEFIN",
o total e o líquido saem PARCIAIS (complete=false) e não são inventados aqui.
Todos os valores são Decimal com 2 casas e saem como string — sem float em nenhum ponto.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
ISS_RETAINED = ("RETAINED_BY_TOMADOR", "RETAINED_BY_INTERMEDIARY")
# TSTipoRetPISCofins (XSD v1.01): (PIS, COFINS, CSLL) efetivamente retidos; None = o código não trata o tributo.
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


def dec(v: Any) -> Decimal | None:
    """Valor monetário exato (até 2 casas) a partir de Decimal/str; None/'' = ausente.
    Nunca aceita float nem arredonda em silêncio: mais de 2 casas é erro (a API já exige até 13 dígitos, ponto e 2 casas)."""
    if v is None or v == "":
        return None
    if isinstance(v, float):
        raise TypeError("valor monetário não pode ser float")
    try:
        d = Decimal(str(v))
    except InvalidOperation as exc:
        raise ValueError(f"valor monetário inválido: {v!r}") from exc
    if d != d.quantize(CENT):
        raise ValueError(f"valor monetário com mais de 2 casas: {v!r}")
    return d.quantize(CENT)


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v.quantize(CENT))


def iss_amount(base: Decimal, rate: Decimal) -> Decimal:
    return (base * rate / Decimal(100)).quantize(CENT, rounding=ROUND_HALF_EVEN)


def resumo(amount: Any, unconditional_discount: Any, conditional_discount: Any, deductions: Any,
           tax: dict | None) -> dict:
    tax = tax or {}
    v_serv = dec(amount) or ZERO
    d_inc = dec(unconditional_discount) or ZERO
    d_con = dec(conditional_discount) or ZERO
    v_dr = dec(deductions) or ZERO
    ret_iss = tax.get("iss_retention")
    iss_retained = ret_iss in ISS_RETAINED
    rate = None
    if tax.get("iss_rate") not in (None, ""):
        try:
            rate = Decimal(str(tax["iss_rate"]))
        except InvalidOperation:
            rate = None
    base = v_serv - d_inc - v_dr
    if not iss_retained:
        iss_val, iss_src = None, "NOT_RETAINED"
    elif rate is None:
        iss_val, iss_src = None, "DETERMINED_BY_SEFIN"
    else:
        iss_val, iss_src = iss_amount(base, rate), "ESTIMATED_FROM_RATE"

    inss, irrf, contrib = dec(tax.get("inss_withheld")), dec(tax.get("irrf_withheld")), dec(tax.get("csll_withheld"))
    tp = tax.get("pis_cofins_retention")
    covered = TP_RET_PIS_COFINS.get(tp or "")
    federal_total = sum((v for v in (inss, irrf, contrib) if v is not None), ZERO)
    total = federal_total + (iss_val or ZERO)
    net = v_serv - d_inc - d_con - total
    pending = ["iss.amount"] if iss_src == "DETERMINED_BY_SEFIN" else []
    if tp == "1":
        # NT 008 v1.02: no DANFSe, com tpRetPisCofins=1, "Contribuições Sociais – Retidas" = vRetCSLL + vPis + vCofins.
        # Se a Sefin também os inclui em vTotalRet não foi confirmado (nenhuma NFS-e real com código 1): o total e o
        # líquido locais ficam marcados como NÃO confirmados; o valor oficial vem da NFS-e autorizada.
        pending.append("federal.contributions_code_1")
    return {
        "service_amount": _s(v_serv),
        "unconditional_discount": _s(d_inc),
        "conditional_discount": _s(d_con),
        "deductions": _s(v_dr),
        "iss": {
            "retention": ret_iss,
            "retained": iss_retained,
            "base": _s(base),
            "rate": None if rate is None else str(rate),
            "amount": _s(iss_val),
            "amount_source": iss_src,
        },
        "federal": {
            "inss": _s(inss),
            "irrf": _s(irrf),
            "contributions": _s(contrib),
            "contributions_code": tp,
            "contributions_label": TP_RET_PIS_COFINS_LABELS.get(tp or ""),
            "contributions_covered": None if covered is None else
            {"pis": covered[0], "cofins": covered[1], "csll": covered[2]},
            "total": _s(federal_total),
            "code1_pis_cofins": ({"pis": _s(dec(tax.get("pis_amount"))), "cofins": _s(dec(tax.get("cofins_amount"))),
                                  "status": "UNCONFIRMED_IN_TOTAL"} if tp == "1" else None),
        },
        # Modalidade 1: vPis/vCofins são apresentados como retidos pela NT 008 v1.02 → não são "informados, não
        # retidos"; ficam em federal.code1_pis_cofins até a confirmação do cálculo da Sefin.
        "informed_not_withheld": ({"pis": None, "cofins": None} if tp == "1" else
                                  {"pis": _s(dec(tax.get("pis_amount"))), "cofins": _s(dec(tax.get("cofins_amount")))}),
        "total_withheld": _s(total),
        "net_amount": _s(net),
        "complete": not pending,
        "pending": pending,
    }


def issues(summary: dict) -> list[tuple[str, str, str, str, str | None]]:
    """(campo, código, mensagem, severidade, regra) derivados do resumo — mesmas contas exibidas na tela."""
    out = []
    v_serv = Decimal(summary["service_amount"])
    net = Decimal(summary["net_amount"])
    if v_serv > 0 and net < 0:
        out.append(("tax_settings", "NET_AMOUNT_NEGATIVE",
                    "Descontos e retenções excedem o valor do serviço: o valor líquido (vLiq) ficaria negativo.", "error", None))
    if "federal.contributions_code_1" in summary["pending"]:
        out.append(("tax_settings.pis_cofins_retention", "CONTRIBUTIONS_CODE1_UNCONFIRMED",
                    "Modalidade 1 (PIS/COFINS retidos): a NT 008 v1.02 apresenta vPis e vCofins como contribuições "
                    "retidas no DANFSe. Ainda não está confirmado se a Sefin os inclui no total retido (vTotalRet) e "
                    "no líquido; esta prévia não os soma e o valor oficial virá da NFS-e autorizada.", "warning", "NT008"))
    if "iss.amount" in summary["pending"]:
        out.append(("tax_settings.iss_rate", "ISS_RETAINED_AMOUNT_BY_SEFIN",
                    "ISS retido sem alíquota informada: a Sefin aplicará a alíquota parametrizada do município; "
                    "total retido e líquido exibidos sem o ISS.", "info", None))
    fed = summary["federal"]
    cov = fed["contributions_covered"]
    if cov is not None and not any(cov.values()) and fed["contributions"] is not None:
        # Código 2 (PIS/COFINS não retidos) não proíbe vRetCSLL no Anexo I, mas o valor reduziria o líquido sem
        # contribuição retida indicada.
        out.append(("tax_settings.csll_withheld", "CONTRIBUTIONS_WITHHELD_WITHOUT_INDICATION",
                    "Valor de contribuições retidas informado, mas o tipo de retenção não indica PIS/COFINS/CSLL retidos.",
                    "warning", None))
    if fed["contributions"] is not None and fed["contributions_code"] in (None, ""):
        # Nenhuma regra do Anexo I rejeita vRetCSLL sem piscofins/tpRetPisCofins; sem o código, porém, a nota não
        # diz quais contribuições o valor representa. Aviso, não bloqueio (não ser mais restrito que a Sefin).
        out.append(("tax_settings.pis_cofins_retention", "CONTRIBUTIONS_CODE_RECOMMENDED",
                    "Informe o tipo de retenção de PIS/COFINS/CSLL (tpRetPisCofins) para identificar as contribuições retidas.",
                    "warning", None))
    return out
