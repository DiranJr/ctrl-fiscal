"""IBS/CBS (Reforma Tributária) no rascunho: classificação oficial, alíquotas-padrão COM VIGÊNCIA e PRÉVIA do cálculo.

Fontes (consultadas em 08/10/2026):
- EC 132/2023, ADCT art. 125: em 2026, IBS à ALÍQUOTA ESTADUAL de 0,1% e CBS a 0,9%; §4º dispensa de recolhimento
  para quem cumpre as obrigações acessórias. LC 214/2025: art. 343 (IBS 2026 = 0,1% ESTADUAL; não há municipal),
  art. 346 (CBS 2026 = 0,9%), art. 348 (compensação com PIS/COFINS, aplicação com redução nos regimes diferenciados,
  NÃO se aplica ao Simples Nacional, §1º dispensa de recolhimento), art. 12 §2º (exclusões da base).
- IT 2025.002 v1.60 (22/06/2026), tabela 05: 2026 → pIBSUF 0,1 | pIBSMun 0 | pCBS 0,9; 2027+ "Aguardar Legislação".
- Anexo I NFS-e (prod. v1.01-20260209), E1530: em 2026, vBC = vServ − descIncond − vCalcReeRepRes − vISSQN − vPIS
  − vCOFINS (de 2027 a 2032 sem vPIS/vCOFINS). E1555: IBS/CBS "por fora"; em 2026 vTotNF = vLiq. A Sefin CALCULA
  (Calculadora oficial) e compara com tolerância de R$ 0,01; a DPS NÃO leva alíquota nem valor de IBS/CBS.
- Ato Conjunto RFB/CGIBS 4/2026: grupo IBS/CBS obrigatório na NFS-e de serviços sujeitos ao ISS a partir de
  01/10/2026 (Simples Nacional: 2027); até 31/12/2026 a ausência não é rejeitada, mas é desconformidade.
- Tabela oficial de cClassTrib (SVRS, IndNfse=true): ctrl_fiscal/data/cclasstrib_nfse.json.

Regras desta prévia (decisão do titular de 08/10/2026: alíquotas oficiais de transição de 2026 como padrão):
- alíquota pela DATA DO FATO GERADOR (dCompet); sem parâmetro para o ano → pendência (nada é estendido a 2027);
- só aplica a alíquota-padrão a classificação de TRIBUTAÇÃO INTEGRAL sem redução (CST 000); redução, regime
  específico, isenção/imunidade ou classificação indefinida → pendência (a Sefin calcula);
- dispensa de recolhimento em 2026 NÃO é alíquota zero nem dispensa de preenchimento;
- IBS/CBS não reduzem o valor líquido (não são retenção) e não são somados ao preço;
- Decimal em tudo; arredondamento a 2 casas ROUND_HALF_EVEN (não há regra oficial publicada para IBS/CBS na
  NFS-e; a referência é a Calculadora, com tolerância de R$ 0,01) — por isso é PRÉVIA, nunca o valor oficial.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

CENT = Decimal("0.01")
CEM = Decimal("100")
OBRIGATORIO_DESDE = date(2026, 10, 1)  # Ato Conjunto RFB/CGIBS 4/2026, art. 1º, III, "d"
SIMPLES = ("2", "3")  # opSimpNac: 2 MEI, 3 ME/EPP (art. 348, III, "c")
PIS_COFINS_TRIBUTAVEL = ("01", "02", "03", "05")
PIS_COFINS_SEM_DEBITO = ("04", "06", "07", "08", "09")
import ctrl_common  # noqa: E402

DATA = Path(ctrl_common.__file__).resolve().parent / "dominios" / "cclasstrib_nfse.json"  # tabela oficial compartilhada


@lru_cache(maxsize=1)
def tabela() -> dict:
    d = json.loads(DATA.read_text(encoding="utf-8"))
    return {"meta": d["meta"], "por_codigo": {c["codigo"]: c for c in d["classificacoes"]}}


def classificacao(codigo: str | None, em: date | None) -> dict | None:
    c = tabela()["por_codigo"].get(codigo or "")
    if c is None:
        return None
    if em is not None:
        ini = date.fromisoformat(c["inicio_vigencia"]) if c.get("inicio_vigencia") else None
        fim = date.fromisoformat(c["fim_vigencia"]) if c.get("fim_vigencia") else None
        if (ini and em < ini) or (fim and em > fim):
            return None
    return c


def classificacoes_vigentes(em: date) -> list[dict]:
    return [c for c in tabela()["por_codigo"].values() if classificacao(c["codigo"], em)]


def aliquotas(s: Session, em: date) -> dict | None:
    """Alíquotas-padrão vigentes na data do fato gerador; None se não houver parâmetro (ex.: 2027 sem lei)."""
    from ..models import TaxRateParameter

    rows = s.scalars(select(TaxRateParameter).where(TaxRateParameter.vigencia_inicio <= em,
                                                    TaxRateParameter.vigencia_fim >= em)).all()
    por = {(r.tributo, r.esfera): r for r in rows}
    need = {("IBS", "UF"), ("IBS", "MUN"), ("CBS", "FED")}
    if not need <= set(por):
        return None
    return {"ibs_uf": por[("IBS", "UF")].aliquota, "ibs_mun": por[("IBS", "MUN")].aliquota,
            "cbs": por[("CBS", "FED")].aliquota, "vigencia": [por[("CBS", "FED")].vigencia_inicio.isoformat(),
                                                             por[("CBS", "FED")].vigencia_fim.isoformat()],
            "fonte": por[("CBS", "FED")].fonte, "autorizacao": por[("CBS", "FED")].autorizacao}


def _d(v) -> Decimal | None:  # noqa: ANN001
    if v in (None, ""):
        return None
    return Decimal(str(v))


def _q(v: Decimal) -> str:
    return str(v.quantize(CENT, rounding=ROUND_HALF_EVEN))


def previa(s: Session, *, draft, fiscal_profile: dict | None, retention_summary: dict) -> dict:  # noqa: ANN001
    """Prévia LOCAL do IBS/CBS do rascunho (o valor oficial é o da Sefin). Nunca altera o rascunho nem a DPS."""
    tax = draft.tax_settings or {}
    ibs = tax.get("ibs_cbs") or {}
    out: dict = {"status": "PENDENTE", "fato_gerador": draft.service_date, "aliquotas": None, "base": None,
                 "valores": None, "classificacao": None, "pendencias": [], "observacoes": [],
                 "calculado_por": "Prévia local. O valor oficial é calculado pela Sefin (Calculadora; tolerância R$ 0,01)."}
    pend = out["pendencias"]
    perfil = fiscal_profile or {}
    if perfil.get("simples_option") in SIMPLES:
        out["status"] = "NAO_APLICAVEL_SIMPLES"
        out["observacoes"].append("Optante do Simples Nacional: as alíquotas de 2026 não se aplicam (LC 214/2025, "
                                  "art. 348, III, c); grupo IBS/CBS obrigatório só a partir de 2027.")
        return out
    fato = date.fromisoformat(draft.service_date) if draft.service_date else None
    if fato is None:
        pend.append({"code": "FATO_GERADOR_PENDENTE", "message": "Informe a data do fato gerador (dCompet): a alíquota "
                                                                 "depende da vigência."})
    rates = aliquotas(s, fato) if fato else None
    if fato and rates is None:
        pend.append({"code": "ALIQUOTA_SEM_VIGENCIA",
                     "message": f"Não há alíquota-padrão autorizada para fatos geradores de {fato.year}: as alíquotas de "
                                "2026 não se aplicam a anos posteriores (IT 2025.002: 'Aguardar Legislação')."})
    if rates:
        out["aliquotas"] = {"ibs_uf": str(rates["ibs_uf"]), "ibs_mun": str(rates["ibs_mun"]), "cbs": str(rates["cbs"]),
                            "unidade": "percentual", "vigencia": rates["vigencia"], "fonte": rates["fonte"],
                            "autorizacao": rates["autorizacao"]}
    if not ibs.get("class_code") or not ibs.get("cst"):
        pend.append({"code": "CLASSIFICACAO_INDEFINIDA",
                     "message": "Informe cIndOp, CST e cClassTrib definidos pela contabilidade para ESTE serviço: a "
                                "plataforma não presume tributação integral (nem pelo regime da empresa)."})
    else:
        c = classificacao(ibs["class_code"], fato)
        if c is None:
            pend.append({"code": "CCLASSTRIB_INVALIDO", "message": "cClassTrib inexistente na tabela oficial para NFS-e "
                                                                   "ou fora de vigência na data do fato gerador (E0958)."})
        else:
            out["classificacao"] = {"codigo": c["codigo"], "cst": c["cst"], "nome": c["nome"],
                                    "reducao_ibs": c["perc_red_ibs"], "reducao_cbs": c["perc_red_cbs"]}
            if ibs["cst"] != c["cst"]:
                pend.append({"code": "CST_DIVERGENTE", "message": "Os 3 primeiros dígitos do cClassTrib devem ser o CST "
                                                                  "(E0959)."})
            if c["exige_tributacao_regular"]:
                pend.append({"code": "TRIBUTACAO_REGULAR_NAO_SUPORTADA",
                             "message": "Esta classificação exige o grupo de tributação regular (E0964), ainda não "
                                        "suportado pela plataforma."})
            integral = c["cst"] == "000" and Decimal(c["perc_red_ibs"]) == 0 and Decimal(c["perc_red_cbs"]) == 0
            if not integral:
                pend.append({"code": "REDUCAO_OU_REGIME_ESPECIFICO",
                             "message": f"Classificação {c['codigo']} ({c['nome']}) não é tributação integral: a "
                                        "alíquota-padrão não é aplicada nesta prévia; redução/regime é calculado pela "
                                        "Sefin. Confirme com a contabilidade."})
    # Base de 2026 (E1530): vServ − descIncond − vISSQN − vPIS − vCOFINS (débitos próprios, não retidos).
    iss = retention_summary.get("iss") or {}
    v_iss = None
    if tax.get("issqn_taxation") in ("IMMUNE", "EXPORT", "NOT_INCIDENT"):
        v_iss = Decimal("0.00")
    elif iss.get("amount") not in (None, ""):
        v_iss = Decimal(str(iss["amount"]))
    elif iss.get("rate") not in (None, "") and iss.get("base") not in (None, ""):
        # ISS NÃO retido continua sendo débito excluído da base (FAQ v1.1, 32.6): mesma fórmula/arredondamento do
        # ISS do resumo (vBC × pAliq / 100, ROUND_HALF_EVEN — confere com as NFS-e reais).
        v_iss = (Decimal(str(iss["base"])) * Decimal(str(iss["rate"])) / CEM).quantize(CENT, rounding=ROUND_HALF_EVEN)
    else:
        pend.append({"code": "ISS_DEFINIDO_PELA_SEFIN",
                     "message": "O ISSQN (excluído da base do IBS/CBS em 2026) depende da alíquota parametrizada do "
                                "município: a base exata sai na NFS-e."})
    cst_pc = tax.get("pis_cofins_cst")
    v_pis, v_cof = _d(tax.get("pis_amount")), _d(tax.get("cofins_amount"))
    if cst_pc in PIS_COFINS_SEM_DEBITO:
        v_pis = v_pis or Decimal("0.00")
        v_cof = v_cof or Decimal("0.00")
    elif cst_pc in PIS_COFINS_TRIBUTAVEL and (v_pis is None or v_cof is None):
        out["observacoes"].append("PIS/COFINS de apuração própria (vPis/vCofins) não informados: em 2026 a base do "
                                  "IBS/CBS os exclui; sem eles a Sefin calcula a base sem essa exclusão.")
    v_pis, v_cof = v_pis or Decimal("0.00"), v_cof or Decimal("0.00")
    v_serv = _d(draft.amount) or Decimal("0.00")
    desc = _d(draft.discount_unconditional) or Decimal("0.00")
    if v_iss is not None:
        base = v_serv - desc - v_iss - v_pis - v_cof
        out["base"] = {"vServ": _q(v_serv), "descIncond": _q(desc), "vISSQN": _q(v_iss), "vPIS": _q(v_pis),
                       "vCOFINS": _q(v_cof), "vBC": _q(base), "formula": "vServ − descIncond − vISSQN − vPIS − vCOFINS "
                                                                           "(Anexo I E1530, 2026)"}
    if not pend and rates and out["base"]:
        base = Decimal(out["base"]["vBC"])
        v_uf = (base * rates["ibs_uf"] / CEM).quantize(CENT, rounding=ROUND_HALF_EVEN)
        v_mun = (base * rates["ibs_mun"] / CEM).quantize(CENT, rounding=ROUND_HALF_EVEN)
        v_cbs = (base * rates["cbs"] / CEM).quantize(CENT, rounding=ROUND_HALF_EVEN)
        out["valores"] = {"vIBSUF": _q(v_uf), "vIBSMun": _q(v_mun), "vIBSTot": _q(v_uf + v_mun), "vCBS": _q(v_cbs),
                          "vLiq_inalterado": retention_summary.get("net_amount"),
                          "vTotNF_2026": retention_summary.get("net_amount")}
        out["status"] = "CALCULADO"
    if fato and fato.year == 2026:
        out["observacoes"].append("2026: recolhimento DISPENSADO para quem cumpre as obrigações acessórias (LC 214/2025, "
                                  "art. 348, §1º) — os campos continuam devidos e os valores não são zerados; o valor "
                                  "é compensável com PIS/COFINS (art. 348, I).")
        out["observacoes"].append("IBS/CBS são 'por fora': não reduzem o valor líquido (não são retenção) e não são "
                                  "somados ao preço; em 2026 vTotNF = vLiq (E1555).")
    return out


def validacao_issues(draft, fiscal_profile: dict | None, environment: str = "production") -> list[dict]:  # noqa: ANN001
    """Issues de validação do grupo IBS/CBS (sem banco): obrigatoriedade por data e conferência com a tabela oficial."""
    from .regras_dps import _issue

    tax = draft.tax_settings or {}
    ibs = tax.get("ibs_cbs") or {}
    perfil = fiscal_profile or {}
    fato = date.fromisoformat(draft.service_date) if draft.service_date else None
    out: list[dict] = []
    simples = perfil.get("simples_option") in SIMPLES
    if not ibs:
        if not simples and fato and fato >= OBRIGATORIO_DESDE:
            # Produção: requisito de prontidão (erro). Homologação/simulação: aviso — a Sefin não rejeita a falta até
            # 31/12/2026, e os demais fluxos continuam testáveis.
            out.append(_issue("tax_settings.ibs_cbs", "IBS_CBS_REQUIRED",
                              "Grupo IBS/CBS obrigatório na NFS-e de serviços sujeitos ao ISS a partir de 01/10/2026 "
                              "(até 31/12/2026 a falta não é rejeitada, mas é desconformidade); informe cIndOp, CST e "
                              "cClassTrib definidos pela contabilidade", "Ato Conjunto RFB/CGIBS 4/2026",
                              "error" if environment == "production" else "warning"))
        elif not simples:
            out.append(_issue("tax_settings.ibs_cbs", "IBS_CBS_NOT_INFORMED",
                              "Grupo IBS/CBS não informado (obrigatório para fatos geradores a partir de 01/10/2026)",
                              "Ato Conjunto RFB/CGIBS 4/2026", "warning"))
        return out
    c = classificacao(ibs.get("class_code"), fato)
    if c is None:
        out.append(_issue("tax_settings.ibs_cbs.class_code", "CCLASSTRIB_NOT_VALID_FOR_NFSE",
                          "cClassTrib inexistente na tabela oficial para NFS-e ou fora de vigência na data do fato "
                          "gerador", "E0958"))
        return out
    if ibs.get("cst") != c["cst"]:
        out.append(_issue("tax_settings.ibs_cbs.cst", "CST_CCLASSTRIB_MISMATCH",
                          "Os 3 primeiros dígitos do cClassTrib devem ser iguais ao CST", "E0959"))
    reg = ibs.get("regular")
    if c["exige_tributacao_regular"] and not reg:
        out.append(_issue("tax_settings.ibs_cbs.regular", "TRIB_REGULAR_REQUIRED",
                          "Esta classificação exige o grupo de tributação regular (CSTReg e cClassTribReg)", "E0965"))
    elif reg and not c["exige_tributacao_regular"]:
        out.append(_issue("tax_settings.ibs_cbs.regular", "TRIB_REGULAR_NOT_ALLOWED",
                          "Grupo de tributação regular não deve ser informado para esta classificação", "E0964"))
    elif reg:
        cr = classificacao(reg.get("class_code"), fato)
        if cr is None:
            out.append(_issue("tax_settings.ibs_cbs.regular.class_code", "CCLASSTRIB_REG_NOT_VALID_FOR_NFSE",
                              "cClassTribReg inexistente na tabela oficial para NFS-e na data do fato gerador", "E0969"))
        elif reg.get("cst") != cr["cst"]:
            out.append(_issue("tax_settings.ibs_cbs.regular.cst", "CST_REG_MISMATCH",
                              "Os 3 primeiros dígitos do cClassTribReg devem ser iguais ao CSTReg", "E0970"))
    dif = ibs.get("deferral")
    if c["cst_diferimento"] and not dif:
        out.append(_issue("tax_settings.ibs_cbs.deferral", "DEFERRAL_REQUIRED",
                          "Esta classificação exige o grupo de diferimento (pDifUF, pDifMun, pDifCBS)", "E0972"))
    elif dif and not c["cst_diferimento"]:
        out.append(_issue("tax_settings.ibs_cbs.deferral", "DEFERRAL_NOT_ALLOWED",
                          "Diferimento não permitido para esta classificação", "E0971"))
    elif dif and any(Decimal(str(dif.get(k) or "0")) > 100 for k in ("uf", "mun", "cbs")):
        out.append(_issue("tax_settings.ibs_cbs.deferral", "DEFERRAL_ABOVE_100", "Percentual de diferimento acima de 100%"))
    if ibs.get("presumed_credit") and not c["permite_credito_presumido"]:
        out.append(_issue("tax_settings.ibs_cbs.presumed_credit", "PRESUMED_CREDIT_NOT_ALLOWED",
                          "Crédito presumido não é permitido para esta classificação (nenhuma classificação vigente "
                          "para NFS-e o permite)", "Tabela cClassTrib"))
    if not (c["cst"] == "000" and Decimal(c["perc_red_ibs"]) == 0 and Decimal(c["perc_red_cbs"]) == 0):
        out.append(_issue("tax_settings.ibs_cbs.class_code", "IBS_CBS_SPECIAL_TREATMENT_REVIEW",
                          f"Classificação com redução/regime específico ({c['nome']}): confirme com a contabilidade; "
                          "a Sefin calcula o valor", "LC 214/2025", "warning"))
    return out
