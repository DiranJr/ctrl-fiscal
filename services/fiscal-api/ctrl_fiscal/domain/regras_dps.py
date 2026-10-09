"""Regras de conformidade da DPS v1.01 antecipadas localmente (Anexo I — RN DPS_NFS-e — e domínios oficiais).

Cada issue traz o código oficial da regra (`rule`). Dividido em:
- `dominio_issues`: códigos precisam EXISTIR nas tabelas oficiais (Anexos A, B, C, I) — tamanho correto não basta.
  Vale em qualquer ambiente.
- `negocio_issues`: combinações proibidas/obrigatórias do Anexo I que dependem só de dados do rascunho, do
  tomador e do perfil fiscal. Ambientes oficiais.
Regras que dependem de cadastros governamentais (CNC, CNPJ, CEP, parametrização municipal, tabela cClassTrib)
NÃO são presumidas aqui: ficam para a transmissão (parametrização) ou para a API oficial — ver
a matriz gerada sob demanda por scripts/gerar_matriz_conformidade.py.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from ctrl_common import dominios

# E0187 / E0234: cIndOp que exigem tomador e endereço do tomador.
CINDOP_EXIGE_TOMADOR_END = {"030102", "050102", "100101", "100301", "100501", "030103", "050103", "100102", "100201",
                            "100302", "100401", "100502", "100601"}
# E0903: serviços que exigem tpOper (não suportado) quando há IBS/CBS.
TPOPER_PREFIXOS = ("2505", "1509", "1712", "1005")
# E0441/E0442: subitens em que ME/EPP pelo SN ainda pode informar dedução/redução.
SN_DEDUCAO_PERMITIDA = {"042201", "042301", "050901", "060101", "060201", "070201", "070202", "070501", "070502",
                        "090201", "090202"}
IBSCBS_INICIO = date(2026, 1, 1)


def _issue(field: str, code: str, message: str, rule: str, severity: str = "error") -> dict:
    return {"field": field, "code": code, "message": f"{message} (regra {rule}).", "severity": severity, "rule": rule}


def _dec(v) -> Decimal | None:  # noqa: ANN001
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def dominio_issues(d, customer: dict | None) -> list[dict]:  # noqa: ANN001
    out: list[dict] = []
    if d.service_national_code and len(d.service_national_code) == 6 and not dominios.servico_nacional(d.service_national_code):
        out.append(_issue("service.national_code", "NATIONAL_CODE_NOT_IN_LIST",
                          "cTribNac não existe na lista de serviços nacional (Anexo B)", "E0310"))
    if d.service_nbs_code and not dominios.nbs(d.service_nbs_code):
        out.append(_issue("service.nbs_code", "NBS_NOT_IN_LIST", "cNBS não existe na tabela NBS (Anexo B)", "E0316"))
    if d.service_location_code and not dominios.municipio_ou_local_especial(d.service_location_code):
        out.append(_issue("service.location_code", "LOCATION_NOT_IN_IBGE",
                          "Município da prestação não existe na tabela IBGE (Anexo A)", "E0302"))
    if customer and customer.get("municipality_code") and not dominios.municipio(customer["municipality_code"]):
        out.append(_issue("customer.municipality_code", "CUSTOMER_MUNICIPALITY_NOT_IN_IBGE",
                          "Município do tomador não existe na tabela IBGE (Anexo A)", "E0238"))
    if d.service_municipal_code == "000":
        out.append(_issue("service.municipal_code", "MUNICIPAL_CODE_000", "cTribMun não pode ser 000", "E0315"))
    ibs = (d.tax_settings or {}).get("ibs_cbs") or {}
    if ibs.get("operation_indicator") and not dominios.cindop_valido(ibs["operation_indicator"]):
        out.append(_issue("tax_settings.ibs_cbs.operation_indicator", "CINDOP_NOT_IN_LIST",
                          "cIndOp não consta da tabela do Anexo C", "E0901"))
    return out


def negocio_issues(d, company, customer: dict | None, now: datetime) -> list[dict]:  # noqa: ANN001, C901, PLR0912, PLR0915
    out: list[dict] = []
    tax = d.tax_settings or {}
    perfil = company.fiscal_profile or {}
    op, ap_sn, esp = perfil.get("simples_option"), perfil.get("simples_regime"), perfil.get("special_regime")
    trib, ret = tax.get("issqn_taxation"), tax.get("iss_retention")
    vserv = _dec(d.amount) or Decimal("0")
    ctn = d.service_national_code or ""

    # ---------------------------------------------------------------- prestador (perfil fiscal)
    if ap_sn and op != "3":
        out.append(_issue("company.simples_regime", "SIMPLES_REGIME_NOT_ALLOWED", "regApTribSN só para opSimpNac=3", "E0162"))
    if op == "3" and not ap_sn:
        out.append(_issue("company.simples_regime", "SIMPLES_REGIME_REQUIRED", "regApTribSN obrigatório para opSimpNac=3", "E0166"))
    if esp not in (None, "0"):
        if trib in ("IMMUNE", "EXPORT", "NOT_INCIDENT"):
            out.append(_issue("tax_settings.issqn_taxation", "SPECIAL_REGIME_WITH_NON_TAXABLE",
                              "Regime especial não permitido com imunidade, exportação ou não incidência", "E0172"))
        if op == "2":
            out.append(_issue("company.special_regime", "SPECIAL_REGIME_MEI", "MEI não tem regime especial", "E0174"))
        if op == "3" and ap_sn == "1":
            out.append(_issue("company.special_regime", "SPECIAL_REGIME_SN", "ME/EPP pelo SN não tem regime especial", "E0175"))

    # ---------------------------------------------------------------- datas
    if d.service_date:
        try:
            dc = date.fromisoformat(d.service_date)
        except ValueError:
            dc = None
        today = now.astimezone(ZoneInfo(company.timezone)).date()
        if dc and dc > today:
            out.append(_issue("service_date", "SERVICE_DATE_AFTER_EMISSION",
                              "dCompet não pode ser posterior à data de emissão", "E0015"))
        if dc and tax.get("ibs_cbs") and dc < IBSCBS_INICIO:
            out.append(_issue("tax_settings.ibs_cbs", "IBS_CBS_BEFORE_2026", "IBS/CBS só a partir da competência 01/01/2026", "E0850"))

    # ---------------------------------------------------------------- tomador
    if customer:
        doc = customer.get("document")
        if doc and doc == company.cnpj:
            out.append(_issue("customer_id", "CUSTOMER_IS_PROVIDER", "CNPJ do tomador igual ao do prestador", "E0202"))
        addr = customer.get("address") or {}
        full_addr = bool(customer.get("municipality_code") and customer.get("postal_code") and addr.get("street")
                         and addr.get("number") and addr.get("district"))
        cindop = ((tax.get("ibs_cbs") or {}).get("operation_indicator"))
        if ret == "RETAINED_BY_TOMADOR" and not full_addr:
            out.append(_issue("customer.address", "CUSTOMER_ADDRESS_REQUIRED_RETENTION",
                              "Retenção pelo tomador exige endereço nacional completo do tomador", "E0237"))
        if (cindop in CINDOP_EXIGE_TOMADOR_END or dominios.incidencia(ctn) == "ET") and not full_addr:
            out.append(_issue("customer.address", "CUSTOMER_ADDRESS_REQUIRED",
                              "Endereço do tomador obrigatório para este cIndOp ou serviço com incidência no tomador", "E0234"))

    # ---------------------------------------------------------------- serviço / construção civil / eventos
    grupo = dominios.grupo_obrigatorio(ctn)
    extra = d.service_extra or {}
    tem_obra = bool(d.service_obra_code or d.service_obra_address or extra.get("obra_cib"))
    if grupo == "obra" and not tem_obra:
        out.append(_issue("service.obra_code", "OBRA_REQUIRED",
                          "Este subitem exige o grupo obra (cObra, cCIB ou endereço da obra)", "E0370"))
    if extra.get("obra_property_registration") and not tem_obra:
        out.append(_issue("service.obra_property_registration", "OBRA_IDENTIFICATION_REQUIRED",
                          "Inscrição imobiliária só acompanha a identificação da obra (cObra, cCIB ou endereço)", "XSD"))
    if tem_obra and grupo != "obra" and ctn != dominios.OBRA_EXCECAO:
        out.append(_issue("service.obra_code", "OBRA_NOT_ALLOWED", "Grupo obra não permitido para este subitem", "E0372"))
    if grupo == "atvEvento":
        out.append(_issue("service.national_code", "EVENT_GROUP_NOT_SUPPORTED",
                          "Subitem exige o grupo atvEvento (evento), não suportado pela plataforma", "Anexo I MUN.INCID"))
    if ctn == "990101" and trib != "NOT_INCIDENT":
        out.append(_issue("tax_settings.issqn_taxation", "NOT_INCIDENT_REQUIRED", "99.01.01 exige não incidência", "E0532"))

    # ---------------------------------------------------------------- descontos e deduções
    for key, rule in (("unconditional_discount", "E0431"), ("conditional_discount", "E0432")):
        v = _dec(getattr(d, f"discount_{key.split('_')[0]}", None))
        if v is not None and (v <= 0 or v >= vserv):
            out.append(_issue(key, "DISCOUNT_RANGE", "Desconto deve ser maior que zero e menor que o valor do serviço", rule))
    if d.deductions and Decimal(d.deductions) > 0:
        if trib in ("IMMUNE", "EXPORT", "NOT_INCIDENT"):
            out.append(_issue("deductions", "DEDUCTION_NOT_ALLOWED_TAXATION",
                              "Dedução não permitida com imunidade, exportação ou não incidência", "E0435"))
        if op == "2":
            out.append(_issue("deductions", "DEDUCTION_NOT_ALLOWED_MEI", "MEI não informa dedução/redução", "E0436"))
        if esp not in (None, "0"):
            out.append(_issue("deductions", "DEDUCTION_NOT_ALLOWED_SPECIAL_REGIME", "Regime especial não informa dedução", "E0438"))
        if op == "3" and ap_sn == "1" and ctn not in SN_DEDUCAO_PERMITIDA:
            out.append(_issue("deductions", "DEDUCTION_NOT_ALLOWED_SN", "ME/EPP pelo SN não deduz para este subitem", "E0441"))

    # ---------------------------------------------------------------- ISSQN
    if trib == "IMMUNE" and not tax.get("immunity_type"):
        out.append(_issue("tax_settings.immunity_type", "IMMUNITY_TYPE_REQUIRED", "Imunidade exige o tipo (tpImunidade)", "E0592"))
    if tax.get("immunity_type") and trib != "IMMUNE":
        out.append(_issue("tax_settings.immunity_type", "IMMUNITY_TYPE_NOT_ALLOWED", "tpImunidade só com imunidade", "E0592"))
    if trib == "EXPORT":
        out.append(_issue("tax_settings.issqn_taxation", "EXPORT_NOT_SUPPORTED",
                          "Exportação exige país de resultado e grupo de comércio exterior, não suportados", "E0590"))
    if ret in ("RETAINED_BY_TOMADOR", "RETAINED_BY_INTERMEDIARY"):
        if trib in ("IMMUNE", "EXPORT", "NOT_INCIDENT"):
            out.append(_issue("tax_settings.iss_retention", "RETENTION_NOT_ALLOWED_TAXATION",
                              "Retenção não permitida com imunidade, exportação ou não incidência", "E0580"))
        if op == "2":
            out.append(_issue("tax_settings.iss_retention", "RETENTION_NOT_ALLOWED_MEI", "MEI sem retenção de ISSQN", "E0583"))
        if esp not in (None, "0"):
            out.append(_issue("tax_settings.iss_retention", "RETENTION_NOT_ALLOWED_SPECIAL_REGIME",
                              "Regime especial sem retenção de ISSQN", "E0588"))
        if ctn == "220101":
            out.append(_issue("tax_settings.iss_retention", "RETENTION_NOT_ALLOWED_220101", "22.01.01 sem retenção", "E0596"))
    if ret == "RETAINED_BY_INTERMEDIARY":
        out.append(_issue("tax_settings.iss_retention", "INTERMEDIARY_NOT_SUPPORTED",
                          "Retenção pelo intermediário exige o grupo interm, não suportado", "Leiaute interm"))

    # ---------------------------------------------------------------- tributos federais
    fed_keys = ("pis_cofins_cst", "pis_cofins_base", "pis_rate", "cofins_rate", "pis_amount", "cofins_amount",
                "pis_cofins_retention", "irrf_withheld", "inss_withheld", "csll_withheld")
    if op == "2" and any(tax.get(k) not in (None, "") for k in fed_keys):
        out.append(_issue("tax_settings", "FEDERAL_NOT_ALLOWED_MEI", "MEI não informa tributos federais", "E0676"))
    base = _dec(tax.get("pis_cofins_base"))
    if base is not None and base > vserv:
        out.append(_issue("tax_settings.pis_cofins_base", "PIS_COFINS_BASE_ABOVE_SERVICE", "BC PIS/COFINS maior que o serviço", "E0677"))
    for rate_k, amt_k, rule in (("pis_rate", "pis_amount", "E0694"), ("cofins_rate", "cofins_amount", "E0696")):
        rate, amt = _dec(tax.get(rate_k)), _dec(tax.get(amt_k))
        if rate is not None:
            if base is None or amt is None:
                # A regra oficial só compara quando base, alíquota e valor existem (nota aceita sem eles: falso
                # positivo medido em scripts/regras_contra_notas_reais.py) — fica como aviso.
                out.append(_issue(f"tax_settings.{amt_k}", "PIS_COFINS_CALC_INCOMPLETE",
                                  "Alíquota informada sem base ou valor; confira com a contabilidade", rule, "warning"))
            elif abs((base * rate / 100).quantize(Decimal("0.01")) - amt) > Decimal("0.01"):
                out.append(_issue(f"tax_settings.{amt_k}", "PIS_COFINS_CALC_MISMATCH", "Valor diferente de base × alíquota", rule))
    for key, rule in (("inss_withheld", "E0699"), ("irrf_withheld", "E0700"), ("csll_withheld", "E0701")):
        v = _dec(tax.get(key))
        if v is not None and (v <= 0 or v >= vserv):
            out.append(_issue(f"tax_settings.{key}", "WITHHELD_RANGE", "Retenção deve ser maior que zero e menor que o serviço", rule))
    tp = tax.get("pis_cofins_retention")
    if tp == "0" and tax.get("csll_withheld") not in (None, ""):
        out.append(_issue("tax_settings.csll_withheld", "CSLL_NOT_ALLOWED", "tpRetPisCofins=0 não informa vRetCSLL", "E0720"))
    if tp not in (None, "", "0", "2") and tax.get("csll_withheld") in (None, ""):
        out.append(_issue("tax_settings.csll_withheld", "CSLL_REQUIRED", "Este tpRetPisCofins exige vRetCSLL", "E0724"))

    # ---------------------------------------------------------------- tributos aproximados (totTrib)
    approx = tax.get("approx_tax") or {}
    mode = approx.get("mode")
    if op == "1" and mode in ("NOT_INFORMED", "SIMPLES_PERCENT"):
        out.append(_issue("tax_settings.approx_tax.mode", "APPROX_MODE_NOT_ALLOWED",
                          "Não optante do Simples informa valores ou percentuais (nunca indTotTrib nem pTotTribSN)", "E0713"))
    if op == "3" and mode == "NOT_INFORMED":
        out.append(_issue("tax_settings.approx_tax.mode", "APPROX_MODE_NOT_ALLOWED", "ME/EPP não usa indTotTrib", "E0712"))
    if op == "2" and mode == "SIMPLES_PERCENT":
        out.append(_issue("tax_settings.approx_tax.mode", "APPROX_MODE_NOT_ALLOWED", "MEI não usa pTotTribSN", "E0710"))
    for part, rule_v, rule_p in (("federal", "E0702", "E0706"), ("state", "E0703", "E0707"), ("municipal", "E0704", "E0708")):
        v = _dec(approx.get(part))
        if v is None:
            continue
        if mode == "VALUES" and (v < 0 or v > vserv):
            out.append(_issue(f"tax_settings.approx_tax.{part}", "APPROX_VALUE_RANGE", "Valor entre 0 e o valor do serviço", rule_v))
        if mode == "PERCENT" and (v < 0 or v > 100):
            out.append(_issue(f"tax_settings.approx_tax.{part}", "APPROX_PERCENT_RANGE", "Percentual entre 0 e 100", rule_p))

    # ---------------------------------------------------------------- IBS/CBS
    ibs = tax.get("ibs_cbs") or {}
    if ibs:
        if ibs.get("cst") and ibs.get("class_code") and ibs["class_code"][:3] != ibs["cst"]:
            out.append(_issue("tax_settings.ibs_cbs.class_code", "CLASS_TRIB_CST_MISMATCH",
                              "Os 3 primeiros dígitos de cClassTrib devem ser iguais ao CST", "E0959"))
        if ctn[:4] in TPOPER_PREFIXOS:
            out.append(_issue("service.national_code", "TPOPER_NOT_SUPPORTED",
                              "Este serviço exige tpOper no IBS/CBS, não suportado", "E0903"))
    return out
